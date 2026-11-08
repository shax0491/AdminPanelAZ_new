from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from app.services.dual_stack import build_reverse_wg_config, endpoint
from app.services.dual_stack_runtime import NetworkOperationError

Runner = Callable[..., subprocess.CompletedProcess[str]]


def generate_wg_keypair() -> dict[str, str]:
    private = X25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "private_key": base64.b64encode(private_bytes).decode("ascii"),
        "public_key": base64.b64encode(public_bytes).decode("ascii"),
    }


def _validate_interface(link_id: int) -> str:
    if not 1 <= link_id <= 999999:
        raise ValueError("Недопустимый link_id")
    return f"azr{link_id}"


def pick_direct_ipv4(candidates: list[str | None], *, resolve_timeout: float = 3.0) -> str:
    """Первый кандидат, который является IPv4; hostname резолвится (с таймаутом). Иначе ValueError."""
    import concurrent.futures
    import socket

    for value in candidates:
        value = (value or "").strip()
        if not value:
            continue
        try:
            return str(ipaddress.IPv4Address(value))
        except ValueError:
            pass
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            answers = pool.submit(socket.getaddrinfo, value, None, socket.AF_INET).result(timeout=resolve_timeout)
            if answers:
                return str(ipaddress.IPv4Address(answers[0][4][0]))
        except (OSError, concurrent.futures.TimeoutError, ValueError):
            continue
        finally:
            pool.shutdown(wait=False)
    raise ValueError("Не удалось определить IPv4-адрес выхода: выполните сетевой аудит или укажите ipv4_address")


def _validate_ip(value: str, family: int | None = None) -> str:
    address = ipaddress.ip_address(value)
    if family is not None and address.version != family:
        raise ValueError("Несоответствие семейства IP-адресов")
    return str(address)


def build_reverse_relay_config(
    *,
    private_key: str,
    address4: str,
    address6: str,
    listen_port: int,
    peer_public_key: str,
    peer_allowed_ips: str,
) -> str:
    if not 1 <= listen_port <= 65535:
        raise ValueError("Недопустимый WireGuard port")
    _validate_ip(address4.split("/")[0], 4)
    _validate_ip(address6.split("/")[0], 6)
    if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", peer_public_key):
        raise ValueError("Некорректный WireGuard public key")
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {address4}, {address6}\n"
        f"ListenPort = {listen_port}\n\n"
        "[Peer]\n"
        f"PublicKey = {peer_public_key}\n"
        f"AllowedIPs = {peer_allowed_ips}\n"
    )


class ReverseTunnelRuntime:
    def __init__(
        self,
        *,
        runner: Runner | None = None,
        config_dir: Path | None = None,
        state_dir: Path | None = None,
    ):
        self._runner = runner or self._run_subprocess
        self.config_dir = config_dir or Path("/etc/wireguard")
        self.state_dir = state_dir or Path("/etc/adminpanelaz")

    @staticmethod
    def _run_subprocess(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def _run(self, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
        result = self._runner(args, timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            raise RuntimeError(f"{' '.join(args)}: {detail}")
        return result

    def _write(self, path: Path, content: str, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)

    def configure_exit(
        self,
        *,
        link_id: int,
        private_key: str,
        address4: str,
        address6: str,
        peer_public_key: str,
        relay_host: str,
        listen_port: int,
        allowed_ips: str,
    ) -> dict[str, Any]:
        interface = _validate_interface(link_id)
        config = build_reverse_wg_config(
            private_key=private_key,
            address4=address4,
            address6=address6,
            peer_public_key=peer_public_key,
            peer_endpoint=endpoint(relay_host, listen_port),
            allowed_ips=allowed_ips,
        )
        path = self.config_dir / f"{interface}.conf"
        self._write(path, config)
        self._run(["systemctl", "enable", "--now", f"wg-quick@{interface}"], timeout=45)
        self._run(["systemctl", "restart", f"wg-quick@{interface}"], timeout=45)
        return {"interface": interface, "active": True, "peer_endpoint": endpoint(relay_host, listen_port)}

    def configure_relay(
        self,
        *,
        link_id: int,
        private_key: str,
        address4: str,
        address6: str,
        listen_port: int,
        peer_public_key: str,
        peer_allowed_ips: str,
    ) -> dict[str, Any]:
        interface = _validate_interface(link_id)
        config = build_reverse_relay_config(
            private_key=private_key,
            address4=address4,
            address6=address6,
            listen_port=listen_port,
            peer_public_key=peer_public_key,
            peer_allowed_ips=peer_allowed_ips,
        )
        self._write(self.config_dir / f"{interface}.conf", config)
        self._run(["systemctl", "enable", "--now", f"wg-quick@{interface}"], timeout=45)
        self._run(["systemctl", "restart", f"wg-quick@{interface}"], timeout=45)
        return {"interface": interface, "active": True, "listen_port": listen_port}

    def teardown(self, link_id: int) -> dict[str, Any]:
        """Гасит wg-интерфейс связи и удаляет его конфиг; идемпотентно (нет интерфейса — не ошибка)."""
        interface = _validate_interface(link_id)
        self._runner(["systemctl", "disable", "--now", f"wg-quick@{interface}"], timeout=45)
        (self.config_dir / f"{interface}.conf").unlink(missing_ok=True)
        return {"interface": interface, "removed": True}

    def status(self, link_id: int) -> dict[str, Any]:
        interface = _validate_interface(link_id)
        result = self._run(["wg", "show", interface, "latest-handshakes"])
        latest = 0
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].isdigit():
                latest = max(latest, int(fields[1]))
        age = int(time.time()) - latest if latest else None
        return {
            "interface": interface,
            "latest_handshake_at": latest or None,
            "handshake_age_seconds": age,
            "connected": age is not None and age <= 180,
        }


def detect_ip6_proxy_destination(rules_text: str) -> str | None:
    from proxy_agent.iptables_dest import AZ_PROXY_DPORTS

    for raw in rules_text.splitlines():
        line = raw.strip()
        if not line.startswith("-A ") or "DNAT" not in line.upper():
            continue
        port_match = re.search(r"--dport\s+(\d+)", line)
        destination_match = re.search(r"--to-destination\s+\[([0-9A-Fa-f:]+)\](?::\d+)?", line)
        if port_match and destination_match and int(port_match.group(1)) in AZ_PROXY_DPORTS:
            return _validate_ip(destination_match.group(1), 6)
    return None


def plan_ip6_destination_rewrite(rules_text: str, old_ip: str, new_ip: str) -> list[list[str]]:
    old = _validate_ip(old_ip, 6)
    new = _validate_ip(new_ip, 6)
    if old == new:
        raise ValueError("Новый DESTINATION совпадает с текущим")

    old_token = rf"(?<![0-9A-Fa-f:]){re.escape(old)}(?![0-9A-Fa-f:])"
    dnat_pattern = re.compile(rf"(--to-destination\s+\[){old_token}(\](?::\d+)?)", re.IGNORECASE)
    destination_pattern = re.compile(rf"(-d\s+){old_token}(?:/\d+)?", re.IGNORECASE)
    plan: list[list[str]] = []
    for raw in rules_text.splitlines():
        line = raw.strip()
        if not line.startswith("-A "):
            continue
        rewritten = dnat_pattern.sub(lambda match: f"{match.group(1)}{new}{match.group(2)}", line)
        rewritten = destination_pattern.sub(lambda match: f"{match.group(1)}{new}/128", rewritten)
        if rewritten == line:
            continue
        old_args = line.split()
        new_args = rewritten.split()
        plan.append(["ip6tables", "-w", "-t", "nat", "-D", *old_args[1:]])
        plan.append(["ip6tables", "-w", "-t", "nat", "-A", *new_args[1:]])
    return plan


def plan_ip6_failover_switch(
    rules_text: str,
    label: str,
    port: int,
    new_ip: str,
    backend_port: int | None = None,
) -> list[list[str]]:
    from proxy_agent.iptables_dest import validate_failover_label, validate_failover_port

    label = validate_failover_label(label)
    port = validate_failover_port(port)
    backend_port = validate_failover_port(backend_port) if backend_port else port
    new = _validate_ip(new_ip, 6)
    comment = f"az-failover:{label}"
    quoted = f'"{comment}"'
    old = None
    for raw in rules_text.splitlines():
        line = raw.strip()
        if not line.startswith("-A ") or (comment not in line and quoted not in line):
            continue
        match = re.search(r"--to-destination\s+\[([0-9A-Fa-f:]+)\]:(\d+)", line)
        if match:
            old = _validate_ip(match.group(1), 6)
            break
    if old == new:
        return []
    if old:
        labeled_rules = "\n".join(
            raw.strip()
            for raw in rules_text.splitlines()
            if raw.strip().startswith("-A ") and (comment in raw or quoted in raw)
        )
        return plan_ip6_destination_rewrite(labeled_rules, old, new)
    return [
        [
            "ip6tables", "-w", "-t", "nat", "-A", "PREROUTING",
            "-p", "udp", "--dport", str(port),
            "-m", "comment", "--comment", comment,
            "-j", "DNAT", "--to-destination", f"[{new}]:{backend_port}",
        ],
        [
            "ip6tables", "-w", "-t", "nat", "-A", "POSTROUTING",
            "-p", "udp", "-d", f"{new}/128", "--dport", str(backend_port),
            "-m", "comment", "--comment", comment,
            "-j", "MASQUERADE",
        ],
    ]


class ReverseWatchdog:
    def __init__(self, *, state_dir: Path | None = None, runner: Runner | None = None):
        self.state_dir = state_dir or Path("/etc/adminpanelaz")
        self.state_path = self.state_dir / "reverse-watchdog.json"
        self._runner = runner or ReverseTunnelRuntime._run_subprocess
        self.links: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data.get("links"), dict):
                self.links = data["links"]
        except (FileNotFoundError, ValueError, OSError):
            self.links = {}

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"links": self.links}, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)

    def configure(self, link: dict[str, Any]) -> dict[str, Any]:
        link_id = str(int(link["link_id"]))
        direct4 = _validate_ip(str(link["direct_ipv4"]), 4)
        direct6 = _validate_ip(str(link["direct_ipv6"]), 6) if link.get("direct_ipv6") else None
        tunnel = _validate_ip(str(link["tunnel_ipv4"]), 4) if link.get("tunnel_ipv4") else None
        previous = self.links.get(link_id)
        self.links[link_id] = {
            "link_id": int(link_id),
            "enabled": bool(link.get("enabled", True)),
            "direct_ipv4": direct4,
            "direct_ipv6": direct6,
            "tunnel_ipv4": tunnel,
            "failover_label": str(link.get("failover_label") or ""),
            "failover_port": int(link.get("failover_port") or 0),
            "backend_port": int(link.get("backend_port") or 0),
            "prefer_ipv6": bool(link.get("prefer_ipv6", False)),
            "down_since": None,
            "up_since": None,
            "recovery_family": None,
            "active_family": "ipv4",
            "ipv4_reachable": None,
            "ipv6_reachable": None,
            "reverse_active": False,
            "forced": bool(link.get("forced", False)),
            "degraded": False,
            "last_error": None,
        }
        if previous and previous.get("reverse_active") and tunnel:
            # DNAT уже смотрит в туннель: повторная настройка не должна «забыть» это
            for key in ("reverse_active", "active_family", "down_since", "up_since", "recovery_family"):
                self.links[link_id][key] = previous.get(key)
        self._save()
        return self.public_status(int(link_id))

    def public_status(self, link_id: int) -> dict[str, Any]:
        link = self.links[str(link_id)]
        link.setdefault("recovery_family", None)
        link.setdefault("active_family", "reverse" if link.get("reverse_active") else "ipv4")
        link.setdefault("ipv4_reachable", None)
        link.setdefault("ipv6_reachable", None)
        link.setdefault("prefer_ipv6", False)
        link.setdefault("forced", False)
        link.setdefault("degraded", False)
        return {key: value for key, value in link.items() if key != "last_error"}

    def _probe(self, family: int, target: str) -> bool:
        try:
            result = self._runner(["ping", f"-{family}", "-c", "2", "-W", "2", target], timeout=8)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _reset_recovery(link: dict[str, Any]) -> None:
        link["up_since"] = None
        link["recovery_family"] = None

    def _start_recovery(self, link: dict[str, Any], family: str, timestamp: float) -> None:
        if link.get("recovery_family") != family or link.get("up_since") is None:
            link["up_since"] = timestamp
            link["recovery_family"] = family

    def _activate_direct(self, link: dict[str, Any], family: str) -> None:
        destination = link["direct_ipv4"] if family == "ipv4" else link["direct_ipv6"]
        self._switch(link, destination)
        link["active_family"] = family
        link["reverse_active"] = False
        link["down_since"] = None
        self._reset_recovery(link)

    def apply_transport(self, link: dict[str, Any]) -> dict[str, Any]:
        mode = str(link.get("mode") or "auto")
        if mode not in {"auto", "force_ipv6", "force_ipv4"}:
            raise ValueError("Недопустимый режим транспорта")
        configured = dict(link)
        configured["enabled"] = mode == "auto"
        configured["prefer_ipv6"] = mode != "force_ipv4"
        configured["forced"] = mode != "auto"
        self.configure(configured)
        if mode == "auto":
            return self.check(int(link["link_id"]))
        family = "ipv6" if mode == "force_ipv6" else "ipv4"
        target = self.links[str(link["link_id"])][f"direct_{family}"]
        if not target:
            raise ValueError(f"Для {mode} не задан адрес {family}")
        if not self._probe(6 if family == "ipv6" else 4, target):
            raise NetworkOperationError(f"Выбранный {family} endpoint недоступен; переключение отменено")
        current = self.links[str(link["link_id"])]
        self._activate_direct(current, family)
        current["enabled"] = False  # автопереключение выключено, пассивный мониторинг идёт через forced
        self._save()
        return self.public_status(int(link["link_id"]))

    def remove(self, link_id: int) -> dict[str, Any]:
        """Убирает связь из watchdog; если DNAT смотрит в туннель — сначала возвращает на direct IPv4."""
        link = self.links.get(str(int(link_id)))
        if link is None:
            return {"removed": False}
        if link.get("reverse_active") and link.get("direct_ipv4"):
            self._switch(link, link["direct_ipv4"])
        del self.links[str(int(link_id))]
        self._save()
        return {"removed": True}

    def _monitor_forced(self, link: dict[str, Any]) -> dict[str, Any]:
        """Принудительный режим: не переключаем, только фиксируем деградацию выбранного адреса."""
        family = link.get("active_family") if link.get("active_family") in {"ipv4", "ipv6"} else "ipv4"
        target = link.get(f"direct_{family}")
        ok = bool(target) and self._probe(6 if family == "ipv6" else 4, target)
        link[f"{family}_reachable"] = ok
        link["degraded"] = not ok
        link["last_error"] = None if ok else f"Принудительный {family} endpoint {target} недоступен (автопереключение выключено)"
        self._save()
        return self.public_status(int(link["link_id"]))

    def check(self, link_id: int, *, now: float | None = None) -> dict[str, Any]:
        link = self.links[str(link_id)]
        if link.get("forced"):
            return self._monitor_forced(link)
        if not link["enabled"]:
            return self.public_status(link_id)
        timestamp = time.time() if now is None else now
        ipv4_ok = self._probe(4, link["direct_ipv4"])
        ipv6_ok = bool(link["direct_ipv6"]) and self._probe(6, link["direct_ipv6"])
        link["ipv4_reachable"] = ipv4_ok
        link["ipv6_reachable"] = ipv6_ok
        link.setdefault("active_family", "reverse" if link["reverse_active"] else "ipv4")
        link.setdefault("prefer_ipv6", False)

        if link["reverse_active"]:
            if ipv4_ok or ipv6_ok:
                link["down_since"] = None
                family = "ipv6" if link["prefer_ipv6"] and ipv6_ok else ("ipv4" if ipv4_ok else "ipv6")
                self._start_recovery(link, family, timestamp)
                if timestamp - link["up_since"] >= 30:
                    self._activate_direct(link, family)
            else:
                self._reset_recovery(link)
                if link["down_since"] is None:
                    link["down_since"] = timestamp
            self._save()
            return self.public_status(link_id)

        active_family = link["active_family"]
        if active_family == "ipv6" and not ipv6_ok and ipv4_ok:
            self._activate_direct(link, "ipv4")
        elif active_family == "ipv6" and ipv6_ok:
            link["down_since"] = None
            if ipv4_ok and not link["prefer_ipv6"]:
                self._start_recovery(link, "ipv4", timestamp)
                if timestamp - link["up_since"] >= 30:
                    self._activate_direct(link, "ipv4")
            else:
                self._reset_recovery(link)
        elif active_family == "ipv4" and ipv4_ok:
            link["down_since"] = None
            if link["prefer_ipv6"] and ipv6_ok:
                self._start_recovery(link, "ipv6", timestamp)
                if timestamp - link["up_since"] >= 30:
                    self._activate_direct(link, "ipv6")
            else:
                self._reset_recovery(link)
        elif ipv6_ok:
            self._activate_direct(link, "ipv6")
        else:
            self._reset_recovery(link)
            if link["down_since"] is None:
                link["down_since"] = timestamp
            if timestamp - link["down_since"] >= 60:
                if link["tunnel_ipv4"]:
                    self._switch(link, link["tunnel_ipv4"])
                    link["reverse_active"] = True
                    link["active_family"] = "reverse"
                else:
                    link["last_error"] = "Оба direct endpoint недоступны, Reverse Tunnel не настроен"
                self._reset_recovery(link)
        self._save()
        return self.public_status(link_id)

    def _switch(self, link: dict[str, Any], destination: str) -> None:
        try:
            family = ipaddress.ip_address(destination).version
            if family == 6:
                rules_result = self._runner(["ip6tables-save", "-t", "nat"], timeout=30)
                if rules_result.returncode != 0:
                    raise RuntimeError((rules_result.stderr or rules_result.stdout or "ip6tables-save failed").strip())
                rules = rules_result.stdout
            if link["failover_label"] and link["failover_port"]:
                if family == 6:
                    plan = plan_ip6_failover_switch(
                        rules,
                        link["failover_label"],
                        link["failover_port"],
                        destination,
                        link["backend_port"] or None,
                    )
                else:
                    from proxy_agent.iptables_dest import plan_failover_switch

                    rules = self._runner(["iptables-save", "-t", "nat"], timeout=30).stdout
                    plan = plan_failover_switch(
                        rules,
                        link["failover_label"],
                        link["failover_port"],
                        destination,
                        link["backend_port"] or None,
                    )
            elif family == 6:
                current = detect_ip6_proxy_destination(rules)
                if not current:
                    raise RuntimeError("Текущий IPv6 proxy DESTINATION не найден")
                plan = plan_ip6_destination_rewrite(rules, current, destination) if current != destination else []
            else:
                from proxy_agent.iptables_dest import detect_proxy_destination, plan_destination_rewrite

                rules = self._runner(["iptables-save", "-t", "nat"], timeout=30).stdout
                current = detect_proxy_destination(rules)
                if not current:
                    raise RuntimeError("Текущий proxy DESTINATION не найден")
                plan = plan_destination_rewrite(rules, current, destination) if current != destination else []
            for args in plan:
                self._run_checked(args)
            self._runner(["netfilter-persistent", "save"], timeout=60)
            link["last_error"] = None
        except Exception as exc:
            link["last_error"] = str(exc)
            raise RuntimeError(f"Reverse Tunnel switch failed: {exc}") from exc

    def _run_checked(self, args: list[str]) -> None:
        result = self._runner(args, timeout=30)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or f"exit {result.returncode}").strip())
