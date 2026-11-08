from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services.dual_stack_runtime import DualStackRuntime, NetworkOperationError


def completed(args, returncode=0, stdout=""):
    return subprocess.CompletedProcess(args, returncode, stdout, "")


class FakeRunner:
    def __init__(self, snapshots, *, rotation_probe_ok=True):
        self.snapshots = snapshots
        self.rotation_probe_ok = rotation_probe_ok
        self.calls = []

    def __call__(self, args, timeout=20):
        self.calls.append(args)
        if args[:4] == ["ip", "-4", "-json", "addr"]:
            return completed(args, stdout=json.dumps(self.snapshots["addrs4"]))
        if args[:4] == ["ip", "-6", "-json", "addr"]:
            return completed(args, stdout=json.dumps(self.snapshots["addrs6"]))
        if args[:4] == ["ip", "-6", "-json", "route"]:
            return completed(args, stdout=json.dumps(self.snapshots["routes6"]))
        if args[0] == "ping":
            ok = self.rotation_probe_ok if "-I" in args else True
            return completed(args, returncode=0 if ok else 1)
        return completed(args)


def snapshots():
    return {
        "addrs4": [{"ifname": "eth0", "addr_info": [{"local": "91.200.100.5", "prefixlen": 24, "scope": "global"}]}],
        "addrs6": [{"ifname": "eth0", "addr_info": [{"local": "2a0d:1234:0:6df::ec5a", "prefixlen": 64, "scope": "global"}]}],
        "routes6": [{"dst": "default", "gateway": "2a0d:1234:0:600::1", "dev": "eth0"}],
    }


def test_runtime_audit_returns_real_snapshot_and_both_probes():
    runner = FakeRunner(snapshots())
    runtime = DualStackRuntime(runner=runner)

    result = runtime.audit()

    assert result["ipv6_address"] == "2a0d:1234:0:6df::ec5a"
    assert result["ipv4_probe"]["reachable"] is True
    assert result["ipv6_probe"]["reachable"] is True
    assert any(call[:3] == ["ping", "-4", "-c"] for call in runner.calls)
    assert any(call[:3] == ["ping", "-6", "-c"] for call in runner.calls)


def test_rotation_verifies_new_source_and_atomically_updates_xray(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"inbounds": [{}], "outbounds": [{"tag": "direct", "protocol": "freedom"}]}))
    runner = FakeRunner(snapshots())
    runtime = DualStackRuntime(
        runner=runner,
        xray_config_path=config,
        xray_binary=Path("/xray"),
        state_dir=tmp_path / "etc",
        systemd_dir=tmp_path / "systemd",
        rotation_script=tmp_path / "rotation.sh",
    )

    result = runtime.rotate_ipv6()

    saved = json.loads(config.read_text())
    assert saved["inbounds"][0]["listen"] == "::"
    assert saved["outbounds"][0]["sendThrough"] == result["ipv6_address"]
    assert saved["outbounds"][0]["settings"]["domainStrategy"] == "UseIPv6v4"
    assert ["systemctl", "restart", "xray-az"] in runner.calls
    assert any(call[:3] == ["ip", "-6", "addr"] and "del" in call for call in runner.calls)
    assert list(tmp_path.glob("config.json.bak-rotation-*"))


def test_failed_rotation_probe_removes_new_address_without_touching_xray(tmp_path: Path):
    original = {"inbounds": [{}], "outbounds": [{"tag": "direct", "protocol": "freedom"}]}
    config = tmp_path / "config.json"
    config.write_text(json.dumps(original))
    runner = FakeRunner(snapshots(), rotation_probe_ok=False)
    runtime = DualStackRuntime(runner=runner, xray_config_path=config, xray_binary=Path("/xray"))

    with pytest.raises(NetworkOperationError, match="исходящ"):
        runtime.rotate_ipv6()

    assert json.loads(config.read_text()) == original
    assert ["systemctl", "restart", "xray-az"] not in runner.calls
    delete_calls = [call for call in runner.calls if call[:4] == ["ip", "-6", "addr", "del"]]
    assert len(delete_calls) == 1


def test_force_ipv6_refuses_node_without_working_ipv6(tmp_path: Path):
    data = snapshots()
    data["addrs6"] = []
    data["routes6"] = []
    runtime = DualStackRuntime(runner=FakeRunner(data), xray_config_path=tmp_path / "missing.json")

    with pytest.raises(NetworkOperationError, match="IPv6"):
        runtime.apply_transport("force_ipv6")


def test_openvpn_restart_failure_restores_original_config(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "openvpn"
    config_dir.mkdir()
    config = config_dir / "antizapret-udp.conf"
    original = "proto udp4\nport 1194\nblock-ipv6\n"
    config.write_text(original)

    class RestartFailureRunner(FakeRunner):
        def __call__(self, args, timeout=20):
            if args == ["systemctl", "restart", "openvpn-server@antizapret-udp"]:
                self.calls.append(args)
                return completed(args, returncode=1, stdout="restart failed")
            return super().__call__(args, timeout=timeout)

    runtime = DualStackRuntime(
        runner=RestartFailureRunner(snapshots()),
        xray_config_path=tmp_path / "missing.json",
        openvpn_config_dir=config_dir,
    )
    monkeypatch.setattr(runtime, "enable_ipv6_sysctl", lambda: None)

    with pytest.raises(NetworkOperationError, match="restart failed"):
        runtime.apply_transport("auto")

    assert config.read_text() == original


def test_rotation_xray_failure_removes_new_address_and_keeps_old(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"inbounds": [], "outbounds": []}))

    class XrayFailureRunner(FakeRunner):
        def __call__(self, args, timeout=20):
            if args == ["systemctl", "restart", "xray-az"]:
                self.calls.append(args)
                return completed(args, returncode=1, stdout="xray restart failed")
            return super().__call__(args, timeout=timeout)

    runner = XrayFailureRunner(snapshots())
    runtime = DualStackRuntime(runner=runner, xray_config_path=config, xray_binary=Path("/xray"))

    with pytest.raises(NetworkOperationError, match="xray restart failed"):
        runtime.rotate_ipv6()

    added = next(call[4].split("/")[0] for call in runner.calls if call[:4] == ["ip", "-6", "addr", "add"])
    assert ["ip", "-6", "addr", "del", f"{added}/64", "dev", "eth0"] in runner.calls
    assert not any(call[:4] == ["ip", "-6", "addr", "del"] and call[4].startswith("2a0d:1234:0:6df::ec5a/") for call in runner.calls)


def test_activate_ipv6_writes_persistent_netplan_and_applies_it(tmp_path: Path):
    runner = FakeRunner(snapshots())
    netplan = tmp_path / "60-adminpanelaz-ipv6.yaml"
    runtime = DualStackRuntime(runner=runner, sysctl_path=tmp_path / "sysctl.conf")

    runtime.activate_ipv6(
        interface="eth0",
        address="2a0d:1234:0:6df::ec5a",
        prefixlen=64,
        gateway="2a0d:1234:0:600::1",
        netplan_path=netplan,
    )

    saved = netplan.read_text()
    assert "2a0d:1234:0:6df::ec5a/64" in saved
    assert "2a0d:1234:0:600::1" in saved
    assert ["netplan", "apply"] in runner.calls


def test_rotation_rewrites_awg_and_openvpn_client_endpoints(tmp_path: Path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    awg = profiles / "client.conf"
    ovpn = profiles / "client.ovpn"
    awg.write_text("[Peer]\nEndpoint = 91.200.100.5:51443\n")
    ovpn.write_text("remote 91.200.100.5 50443\nproto udp\n")
    runtime = DualStackRuntime(
        runner=FakeRunner(snapshots()),
        xray_config_path=tmp_path / "missing.json",
        state_dir=tmp_path / "state",
        systemd_dir=tmp_path / "systemd",
        rotation_script=tmp_path / "rotation.sh",
        profile_root=profiles,
    )

    result = runtime.rotate_ipv6()

    expected = f"[{result['ipv6_address']}]"
    assert f"Endpoint = {expected}:51443" in awg.read_text()
    assert f"remote {result['ipv6_address']} 50443" in ovpn.read_text()
