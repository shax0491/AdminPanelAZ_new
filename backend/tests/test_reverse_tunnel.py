from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.services.reverse_tunnel import (
    ReverseTunnelRuntime,
    ReverseWatchdog,
    generate_wg_keypair,
    pick_direct_ipv4,
)


KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def completed(args, returncode=0, stdout=""):
    return subprocess.CompletedProcess(args, returncode, stdout, "")


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        return completed(args)


def test_generated_wireguard_keypair_has_standard_base64_keys():
    pair = generate_wg_keypair()
    assert pair["private_key"] != pair["public_key"]
    assert len(pair["private_key"]) == 44
    assert len(pair["public_key"]) == 44


def test_exit_reverse_tunnel_is_persistent_and_does_not_return_private_key(tmp_path: Path):
    runner = Runner()
    runtime = ReverseTunnelRuntime(runner=runner, config_dir=tmp_path)

    result = runtime.configure_exit(
        link_id=5,
        private_key=KEY,
        address4="10.77.5.2/32",
        address6="fd77::5:2/128",
        peer_public_key=KEY,
        relay_host="2001:db8::10",
        listen_port=51877,
        allowed_ips="10.77.5.1/32, fd77::5:1/128",
    )

    saved = (tmp_path / "azr5.conf").read_text()
    assert "Endpoint = [2001:db8::10]:51877" in saved
    assert "PersistentKeepalive = 15" in saved
    assert (tmp_path / "azr5.conf").stat().st_mode & 0o777 == 0o600
    assert "private_key" not in result
    assert KEY not in str(result)
    assert ["systemctl", "enable", "--now", "wg-quick@azr5"] in runner.calls


def test_relay_reverse_tunnel_listens_and_limits_peer_to_tunnel_addresses(tmp_path: Path):
    runner = Runner()
    runtime = ReverseTunnelRuntime(runner=runner, config_dir=tmp_path)

    result = runtime.configure_relay(
        link_id=5,
        private_key=KEY,
        address4="10.77.5.1/32",
        address6="fd77::5:1/128",
        listen_port=51877,
        peer_public_key=KEY,
        peer_allowed_ips="10.77.5.2/32, fd77::5:2/128",
    )

    saved = (tmp_path / "azr5.conf").read_text()
    assert "ListenPort = 51877" in saved
    assert "AllowedIPs = 10.77.5.2/32, fd77::5:2/128" in saved
    assert result["active"] is True


class ProbeRunner:
    def __init__(self):
        self.reachable = False

    def __call__(self, args, timeout=30):
        return completed(args, returncode=0 if self.reachable else 1)


def configured_watchdog(tmp_path: Path):
    runner = ProbeRunner()
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=runner)
    watchdog.configure(
        {
            "link_id": 5,
            "enabled": True,
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
            "tunnel_ipv4": "10.77.5.2",
        }
    )
    return watchdog, runner


def test_watchdog_switches_only_after_sixty_seconds_without_both_families(tmp_path: Path, monkeypatch):
    watchdog, runner = configured_watchdog(tmp_path)
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))

    assert watchdog.check(5, now=1000)["reverse_active"] is False
    assert watchdog.check(5, now=1059)["reverse_active"] is False
    assert watchdog.check(5, now=1060)["reverse_active"] is True
    assert switches == ["10.77.5.2"]


def test_watchdog_returns_to_direct_after_stable_recovery(tmp_path: Path, monkeypatch):
    watchdog, runner = configured_watchdog(tmp_path)
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))
    watchdog.check(5, now=1000)
    watchdog.check(5, now=1060)

    runner.reachable = True
    watchdog.check(5, now=1070)
    assert watchdog.check(5, now=1099)["reverse_active"] is True
    assert watchdog.check(5, now=1100)["reverse_active"] is False
    assert switches == ["10.77.5.2", "203.0.113.10"]


def test_watchdog_state_survives_process_restart_without_secrets(tmp_path: Path):
    watchdog, _runner = configured_watchdog(tmp_path)
    restored = ReverseWatchdog(state_dir=tmp_path)

    status = restored.public_status(5)
    assert status["direct_ipv4"] == "203.0.113.10"
    assert status["direct_ipv6"] == "2001:db8::10"
    assert "private_key" not in restored.state_path.read_text()


def test_watchdog_prefers_direct_ipv6_when_only_ipv4_is_blocked(tmp_path: Path, monkeypatch):
    class SplitRunner:
        def __call__(self, args, timeout=30):
            return completed(args, returncode=1 if args[1] == "-4" else 0)

    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=SplitRunner())
    watchdog.configure(
        {
            "link_id": 5,
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
            "tunnel_ipv4": "10.77.5.2",
        }
    )
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))

    status = watchdog.check(5, now=1000)

    assert status["active_family"] == "ipv6"
    assert status["ipv4_reachable"] is False
    assert status["ipv6_reachable"] is True
    assert status["reverse_active"] is False
    assert switches == ["2001:db8::10"]


def test_ip6_destination_rewrite_uses_ip6tables_and_brackets():
    from app.services.reverse_tunnel import plan_ip6_destination_rewrite

    rules = """*nat
-A PREROUTING -p udp --dport 443 -j DNAT --to-destination [2001:db8::10]:50443
-A POSTROUTING -d 2001:db8::10/128 -j MASQUERADE
COMMIT
"""
    plan = plan_ip6_destination_rewrite(rules, "2001:db8::10", "2001:db8::20")

    assert plan
    assert all(call[0] == "ip6tables" for call in plan)
    assert any("[2001:db8::20]:50443" in " ".join(call) for call in plan)
    assert any("-d 2001:db8::20/128" in " ".join(call) for call in plan)


def test_watchdog_returns_from_direct_ipv6_after_stable_ipv4_recovery(tmp_path: Path, monkeypatch):
    class SplitRunner:
        ipv4_reachable = False

        def __call__(self, args, timeout=30):
            ipv4 = args[1] == "-4"
            return completed(args, returncode=0 if self.ipv4_reachable or not ipv4 else 1)

    runner = SplitRunner()
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=runner)
    watchdog.configure(
        {
            "link_id": 5,
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
            "tunnel_ipv4": "10.77.5.2",
        }
    )
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))

    assert watchdog.check(5, now=1000)["active_family"] == "ipv6"
    runner.ipv4_reachable = True
    assert watchdog.check(5, now=1010)["active_family"] == "ipv6"
    assert watchdog.check(5, now=1039)["active_family"] == "ipv6"
    assert watchdog.check(5, now=1040)["active_family"] == "ipv4"
    assert switches == ["2001:db8::10", "203.0.113.10"]


def test_ipv6_switch_executes_ip6tables_plan(tmp_path: Path):
    rules = """*nat
-A PREROUTING -p udp --dport 443 -j DNAT --to-destination [2001:db8::10]:50443
-A POSTROUTING -d 2001:db8::10/128 -j MASQUERADE
COMMIT
"""

    class Ip6Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, args, timeout=30):
            self.calls.append(args)
            return completed(args, stdout=rules if args[0] == "ip6tables-save" else "")

    runner = Ip6Runner()
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=runner)
    watchdog.configure(
        {
            "link_id": 5,
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
            "tunnel_ipv4": "10.77.5.2",
        }
    )

    watchdog._switch(watchdog.links["5"], "2001:db8::20")

    assert ["ip6tables-save", "-t", "nat"] in runner.calls
    assert any(call[:2] == ["ip6tables", "-w"] for call in runner.calls)
    assert ["netfilter-persistent", "save"] in runner.calls


def test_ipv6_labeled_failover_uses_ip6tables(tmp_path: Path):
    rules = """*nat
-A PREROUTING -p udp --dport 51880 -m comment --comment az-failover:pool -j DNAT --to-destination [2001:db8::10]:50443
-A POSTROUTING -p udp -d 2001:db8::10/128 --dport 50443 -m comment --comment az-failover:pool -j MASQUERADE
COMMIT
"""

    class Ip6Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, args, timeout=30):
            self.calls.append(args)
            return completed(args, stdout=rules if args[0] == "ip6tables-save" else "")

    runner = Ip6Runner()
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=runner)
    watchdog.configure(
        {
            "link_id": 5,
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
            "tunnel_ipv4": "10.77.5.2",
            "failover_label": "pool",
            "failover_port": 51880,
            "backend_port": 50443,
        }
    )

    watchdog._switch(watchdog.links["5"], "2001:db8::20")

    ip6_calls = [call for call in runner.calls if call[0] == "ip6tables"]
    assert ip6_calls
    assert any("[2001:db8::20]:50443" in " ".join(call) for call in ip6_calls)
    assert any("-d 2001:db8::20/128" in " ".join(call) for call in ip6_calls)


def test_watchdog_auto_prefers_ipv6_without_requiring_reverse(tmp_path: Path, monkeypatch):
    class DualRunner:
        def __call__(self, args, timeout=30):
            return completed(args)

    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=DualRunner())
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))

    status = watchdog.apply_transport(
        {
            "link_id": 5,
            "mode": "auto",
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
        }
    )

    # гистерезис: сразу после выбора Auto остаёмся на IPv4 и только копим окно стабильности IPv6
    assert status["active_family"] == "ipv4"
    assert status["recovery_family"] == "ipv6"
    assert switches == []

    status = watchdog.check(5, now=time.time() + 31)

    assert status["active_family"] == "ipv6"
    assert status["enabled"] is True
    assert status["reverse_active"] is False
    assert switches == ["2001:db8::10"]


def test_watchdog_force_ipv4_disables_automatic_switching(tmp_path: Path, monkeypatch):
    class DualRunner:
        def __call__(self, args, timeout=30):
            return completed(args)

    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=DualRunner())
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))
    link = {
        "link_id": 5,
        "direct_ipv4": "203.0.113.10",
        "direct_ipv6": "2001:db8::10",
    }

    watchdog.apply_transport({**link, "mode": "auto"})
    status = watchdog.apply_transport({**link, "mode": "force_ipv4"})

    assert status["active_family"] == "ipv4"
    assert status["enabled"] is False
    assert switches == ["203.0.113.10"]


class FamilyRunner:
    """ping: доступность по семействам задаётся флагами; остальные команды успешны."""

    def __init__(self, ipv4=True, ipv6=True):
        self.ipv4 = ipv4
        self.ipv6 = ipv6

    def __call__(self, args, timeout=30):
        if args[0] == "ping":
            ok = self.ipv4 if args[1] == "-4" else self.ipv6
            return completed(args, returncode=0 if ok else 1)
        return completed(args)


LINK = {"link_id": 7, "direct_ipv4": "203.0.113.10", "direct_ipv6": "2001:db8::10", "tunnel_ipv4": "10.77.0.14"}


def test_configure_keeps_active_reverse_state(tmp_path: Path, monkeypatch):
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=FamilyRunner(ipv4=False, ipv6=False))
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: None)
    watchdog.configure({**LINK, "enabled": True})
    watchdog.check(7, now=1000)
    watchdog.check(7, now=1061)
    assert watchdog.links["7"]["reverse_active"] is True

    status = watchdog.configure({**LINK, "enabled": True})

    assert status["reverse_active"] is True
    assert status["active_family"] == "reverse"


def test_configure_still_resets_state_when_reverse_not_active(tmp_path: Path):
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=FamilyRunner())
    watchdog.configure({**LINK, "enabled": True})
    watchdog.links["7"]["active_family"] = "ipv6"

    assert watchdog.configure({**LINK, "enabled": True})["active_family"] == "ipv4"


def test_prefer_ipv6_does_not_flap_on_unstable_ipv6(tmp_path: Path, monkeypatch):
    runner = FamilyRunner(ipv4=True, ipv6=True)
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=runner)
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))
    watchdog.configure({**LINK, "enabled": True, "prefer_ipv6": True})

    watchdog.check(7, now=1000)  # IPv6 появился - начинаем отсчёт
    runner.ipv6 = False
    watchdog.check(7, now=1020)  # пропал до 30 с - отсчёт сброшен
    runner.ipv6 = True
    watchdog.check(7, now=1040)  # снова отсчёт с нуля
    status = watchdog.check(7, now=1060)  # прошло только 20 с

    assert status["active_family"] == "ipv4"
    assert switches == []
    assert watchdog.check(7, now=1071)["active_family"] == "ipv6"
    assert switches == ["2001:db8::10"]


def test_forced_mode_keeps_passive_monitoring_and_reports_degradation(tmp_path: Path, monkeypatch):
    runner = FamilyRunner(ipv4=True, ipv6=True)
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=runner)
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))
    status = watchdog.apply_transport({**LINK, "mode": "force_ipv6"})
    assert status["forced"] is True and status["degraded"] is False
    switches.clear()

    runner.ipv6 = False
    status = watchdog.check(7)

    assert status["degraded"] is True
    assert status["active_family"] == "ipv6"  # автопереключения нет
    assert switches == []
    runner.ipv6 = True
    assert watchdog.check(7)["degraded"] is False


def test_remove_returns_dnat_to_direct_when_reverse_active(tmp_path: Path):
    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=FamilyRunner())
    switches = []
    watchdog._switch = lambda link, destination: switches.append(destination)
    watchdog.configure({**LINK, "enabled": True})
    watchdog.links["7"]["reverse_active"] = True

    assert watchdog.remove(7) == {"removed": True}

    assert switches == ["203.0.113.10"]
    assert "7" not in watchdog.links
    assert watchdog.remove(7) == {"removed": False}


def test_runtime_teardown_disables_unit_and_removes_config(tmp_path: Path):
    runner = Runner()
    runtime = ReverseTunnelRuntime(runner=runner, config_dir=tmp_path)
    (tmp_path / "azr7.conf").write_text("x")

    runtime.teardown(7)
    runtime.teardown(7)  # идемпотентно

    assert not (tmp_path / "azr7.conf").exists()
    assert ["systemctl", "disable", "--now", "wg-quick@azr7"] in runner.calls


def test_pick_direct_ipv4_validates_and_resolves(monkeypatch):
    assert pick_direct_ipv4([None, "", "198.51.100.4", "x.example"]) == "198.51.100.4"
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, family=0: [(2, 1, 6, "", ("198.51.100.9", 0))])
    assert pick_direct_ipv4(["vpn.example"]) == "198.51.100.9"

    def nxdomain(*args, **kwargs):
        raise OSError("nxdomain")

    monkeypatch.setattr("socket.getaddrinfo", nxdomain)
    try:
        pick_direct_ipv4(["2001:db8::1", "vpn.example"])
    except ValueError:
        pass
    else:
        raise AssertionError("hostname/IPv6 не должны попадать в direct_ipv4")


def test_force_mode_with_unreachable_endpoint_raises_runtime_error_and_does_not_switch(tmp_path: Path, monkeypatch):
    from app.services.dual_stack_runtime import NetworkOperationError

    watchdog = ReverseWatchdog(state_dir=tmp_path, runner=FamilyRunner(ipv4=True, ipv6=False))
    switches = []
    monkeypatch.setattr(watchdog, "_switch", lambda link, destination: switches.append(destination))

    try:
        watchdog.apply_transport({**LINK, "mode": "force_ipv6"})
    except NetworkOperationError:
        pass
    else:
        raise AssertionError("ожидалась NetworkOperationError (RuntimeError)")
    assert switches == []
