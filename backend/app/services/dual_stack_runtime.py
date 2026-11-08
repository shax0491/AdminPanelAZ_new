from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.dual_stack import (
    endpoint,
    migrate_openvpn_config,
    migrate_xray_config,
    network_capabilities,
    new_ipv6_in_subnet,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


class NetworkOperationError(RuntimeError):
    pass


class DualStackRuntime:
    def __init__(
        self,
        *,
        runner: Runner | None = None,
        xray_config_path: Path | None = None,
        xray_binary: Path | None = None,
        openvpn_config_dir: Path | None = None,
        state_dir: Path | None = None,
        systemd_dir: Path | None = None,
        rotation_script: Path | None = None,
        sysctl_path: Path | None = None,
        profile_root: Path | None = None,
    ):
        self._runner = runner or self._subprocess_runner
        self.xray_config_path = xray_config_path or Path("/opt/xray-az/config.json")
        self.xray_binary = xray_binary or Path("/opt/xray-az/xray")
        self.openvpn_config_dir = openvpn_config_dir or Path("/etc/openvpn/server")
        self.state_dir = state_dir or Path("/etc/adminpanelaz")
        self.systemd_dir = systemd_dir or Path("/etc/systemd/system")
        self.rotation_script = rotation_script or Path("/usr/local/sbin/adminpanelaz-ipv6-rotation")
        self.sysctl_path = sysctl_path or Path("/etc/sysctl.d/60-adminpanelaz-dual-stack.conf")
        self.profile_root = profile_root or Path("/root/antizapret/client")

    @staticmethod
    def _subprocess_runner(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def _run(self, args: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
        try:
            result = self._runner(args, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NetworkOperationError(f"Команда {args[0]} не выполнена: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            raise NetworkOperationError(f"{' '.join(args)}: {detail}")
        return result

    def _snapshot(self) -> tuple[list[dict], list[dict], list[dict]]:
        addrs4 = json.loads(self._run(["ip", "-4", "-json", "addr", "show", "scope", "global"]).stdout)
        addrs6 = json.loads(self._run(["ip", "-6", "-json", "addr", "show", "scope", "global"]).stdout)
        routes6 = json.loads(self._run(["ip", "-6", "-json", "route", "show"]).stdout)
        return addrs4, addrs6, routes6

    def probe(self, family: int, target: str) -> dict[str, Any]:
        if family not in (4, 6):
            raise ValueError("family должен быть 4 или 6")
        address = ipaddress.ip_address(target)
        if address.version != family:
            raise ValueError("Адрес probe не соответствует семейству")
        started = time.monotonic()
        try:
            result = self._runner(
                ["ping", f"-{family}", "-c", "3", "-W", "2", target],
                timeout=12,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"reachable": False, "target": target, "error": str(exc)}
        latency = round((time.monotonic() - started) * 1000 / 3, 2)
        return {
            "reachable": result.returncode == 0,
            "target": target,
            "latency_ms": latency if result.returncode == 0 else None,
            "error": None if result.returncode == 0 else (result.stderr or result.stdout or "unreachable").strip(),
        }

    def audit(self) -> dict[str, Any]:
        addrs4, addrs6, routes6 = self._snapshot()
        snapshot = network_capabilities(
            addrs4,
            addrs6,
            routes6,
            ipv4_probe={"reachable": False, "error": "not-run"},
            ipv6_probe={"reachable": False, "error": "not-run"},
        )
        snapshot["ipv4_probe"] = self.probe(4, "1.1.1.1") if snapshot["has_ipv4"] else {"reachable": False}
        snapshot["ipv6_probe"] = (
            self.probe(6, "2606:4700:4700::1111") if snapshot["has_ipv6"] else {"reachable": False}
        )
        snapshot["audited_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return snapshot

    def enable_ipv6_sysctl(self) -> None:
        values = {
            "net.ipv6.conf.all.disable_ipv6": "0",
            "net.ipv6.conf.default.disable_ipv6": "0",
            "net.ipv6.conf.all.forwarding": "1",
            "net.ipv6.conf.all.proxy_ndp": "1",
        }
        self._atomic_write(
            self.sysctl_path,
            "".join(f"{key}={value}\n" for key, value in values.items()),
            mode=0o644,
        )
        for key, value in values.items():
            self._run(["sysctl", "-w", f"{key}={value}"])

    def activate_ipv6(
        self,
        *,
        interface: str,
        address: str,
        prefixlen: int,
        gateway: str,
        netplan_path: Path | None = None,
    ) -> dict[str, Any]:
        if not interface or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in interface):
            raise ValueError("Некорректное имя сетевого интерфейса")
        ipv6 = ipaddress.IPv6Address(address)
        ipv6_gateway = ipaddress.IPv6Address(gateway)
        if not 0 <= prefixlen <= 128:
            raise ValueError("Некорректная длина IPv6-префикса")
        path = netplan_path or Path("/etc/netplan/60-adminpanelaz-ipv6.yaml")
        content = (
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            f"    {interface}:\n"
            "      dhcp6: false\n"
            f"      addresses: [{json.dumps(f'{ipv6}/{prefixlen}')}]\n"
            "      routes:\n"
            "        - to: default\n"
            f"          via: {json.dumps(str(ipv6_gateway))}\n"
        )
        self._atomic_write(path, content, mode=0o600)
        self.enable_ipv6_sysctl()
        self._run(["netplan", "apply"], timeout=60)
        return self.audit()

    def apply_transport(self, transport_mode: str) -> dict[str, Any]:
        capabilities = self.audit()
        if transport_mode == "force_ipv6" and not capabilities["ipv6_probe"].get("reachable"):
            raise NetworkOperationError("Force IPv6 невозможен: на узле нет рабочего IPv6")
        self.enable_ipv6_sysctl()
        changed: list[str] = []
        if self.xray_config_path.exists():
            self._apply_xray(transport_mode, capabilities.get("ipv6_address"))
            changed.append("xray-az")
        for name in ("antizapret-udp", "antizapret-tcp", "vpn-udp", "vpn-tcp"):
            path = self.openvpn_config_dir / f"{name}.conf"
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            transport = "udp6" if "proto udp" in source else "tcp6"
            migrated = migrate_openvpn_config(source, transport=transport, inner_ipv6=False)
            if migrated == source:
                continue
            self._replace_with_backup(path, migrated, "dual-stack")
            try:
                self._run(["openvpn", "--config", str(path), "--test-crypto"], timeout=20)
                self._run(["systemctl", "restart", f"openvpn-server@{name}"], timeout=40)
                self._run(["systemctl", "is-active", "--quiet", f"openvpn-server@{name}"], timeout=10)
            except NetworkOperationError:
                path.write_text(source, encoding="utf-8")
                self._runner(["systemctl", "restart", f"openvpn-server@{name}"], timeout=40)
                raise
            changed.append(f"openvpn-server@{name}")
        return {"transport_mode": transport_mode, "changed": changed, "capabilities": capabilities}

    def _apply_xray(
        self,
        transport_mode: str,
        ipv6_address: str | None,
        *,
        backup_reason: str = "dual-stack",
    ) -> None:
        original_text = self.xray_config_path.read_text(encoding="utf-8")
        original = json.loads(original_text)
        migrated = migrate_xray_config(original, transport_mode=transport_mode, ipv6_address=ipv6_address)
        self._replace_with_backup(
            self.xray_config_path,
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
            backup_reason,
        )
        try:
            self._run([str(self.xray_binary), "run", "-test", "-config", str(self.xray_config_path)], timeout=30)
            self._run(["systemctl", "restart", "xray-az"], timeout=40)
            self._run(["systemctl", "is-active", "--quiet", "xray-az"], timeout=10)
        except NetworkOperationError:
            self.xray_config_path.write_text(original_text, encoding="utf-8")
            self._runner(["systemctl", "restart", "xray-az"], timeout=40)
            raise

    def _rewrite_profile_endpoints(self, address: str) -> int:
        if not self.profile_root.is_dir():
            return 0
        changed = 0
        for path in self.profile_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".conf", ".ovpn"}:
                continue
            source = path.read_text(encoding="utf-8")
            lines = []
            dirty = False
            for line in source.splitlines():
                if path.suffix == ".conf" and line.strip().startswith("Endpoint ="):
                    current = line.split("=", 1)[1].strip()
                    if ":" not in current:
                        continue
                    port = current.rsplit(":", 1)[1]
                    if not port.isdigit():
                        continue
                    line = f"Endpoint = {endpoint(address, int(port))}"
                    dirty = True
                elif path.suffix == ".ovpn" and line.strip().startswith("remote "):
                    fields = line.split()
                    if len(fields) >= 3 and fields[2].isdigit():
                        fields[1] = address
                        line = " ".join(fields)
                        dirty = True
                lines.append(line)
            if dirty:
                self._atomic_write(path, "\n".join(lines) + "\n", mode=path.stat().st_mode & 0o777)
                changed += 1
        return changed

    def rotate_ipv6(self) -> dict[str, Any]:
        capabilities = self.audit()
        if not capabilities.get("ipv6_rotation_supported"):
            raise NetworkOperationError("Ротация невозможна: на внешнем интерфейсе нет публичной IPv6 /64")
        if not capabilities["ipv6_probe"].get("reachable"):
            raise NetworkOperationError("Ротация отменена: текущий IPv6 не имеет исходящей связности")
        old = str(capabilities["ipv6_address"])
        new = new_ipv6_in_subnet(str(capabilities["ipv6_network"]))
        interface = str(capabilities["network_interface"])
        self._run(["ip", "-6", "addr", "add", f"{new}/64", "dev", interface])
        try:
            probe = self._runner(
                ["ping", "-6", "-I", new, "-c", "3", "-W", "2", "2606:4700:4700::1111"],
                timeout=12,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._runner(["ip", "-6", "addr", "del", f"{new}/64", "dev", interface], timeout=20)
            raise NetworkOperationError(f"Проверка нового IPv6 не выполнена: {exc}") from exc
        if probe.returncode != 0:
            self._runner(["ip", "-6", "addr", "del", f"{new}/64", "dev", interface], timeout=20)
            raise NetworkOperationError("Новый IPv6 не прошёл исходящую проверку; адрес удалён, конфиги не изменены")
        gateway = capabilities.get("ipv6_gateway")
        try:
            if self.xray_config_path.exists():
                self._apply_xray("auto", new, backup_reason="rotation")
            if gateway:
                self._run(
                    ["ip", "-6", "route", "replace", "default", "via", gateway, "dev", interface, "src", new]
                )
            self._rewrite_profile_endpoints(new)
            self._persist_rotation(interface, new, gateway)
        except Exception:
            if self.xray_config_path.exists():
                try:
                    self._apply_xray("auto", old, backup_reason="rotation-rollback")
                except Exception:
                    pass
            if gateway:
                self._runner(
                    ["ip", "-6", "route", "replace", "default", "via", gateway, "dev", interface, "src", old],
                    timeout=20,
                )
            self._runner(["ip", "-6", "addr", "del", f"{new}/64", "dev", interface], timeout=20)
            raise
        if old != new:
            self._run(["ip", "-6", "addr", "del", f"{old}/64", "dev", interface])
        return {
            "old_ipv6_address": old,
            "ipv6_address": new,
            "ipv6_network": capabilities["ipv6_network"],
            "ipv6_gateway": gateway,
            "network_interface": interface,
            "rotated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def _persist_rotation(self, interface: str, address: str, gateway: str | None) -> None:
        state = self.state_dir / "ipv6-rotation.json"
        self._atomic_write(
            state,
            json.dumps({"interface": interface, "address": address, "gateway": gateway}, indent=2) + "\n",
            mode=0o600,
        )
        commands = [f"ip -6 addr replace {address}/64 dev {interface}"]
        if gateway:
            commands.append(f"ip -6 route replace default via {gateway} dev {interface} src {address}")
        self._atomic_write(
            self.rotation_script,
            "#!/bin/sh\nset -e\n" + "\n".join(commands) + "\n",
            mode=0o755,
        )
        unit = self.systemd_dir / "adminpanelaz-ipv6-rotation.service"
        self._atomic_write(
            unit,
            "[Unit]\nAfter=network-online.target\nWants=network-online.target\n\n"
            f"[Service]\nType=oneshot\nExecStart={self.rotation_script}\n\n"
            "[Install]\nWantedBy=multi-user.target\n",
            mode=0o644,
        )
        self._run(["systemctl", "daemon-reload"])
        self._run(["systemctl", "enable", "adminpanelaz-ipv6-rotation.service"])

    @staticmethod
    def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _replace_with_backup(self, path: Path, content: str, reason: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{reason}-{stamp}")
        backup.write_bytes(path.read_bytes())
        os.chmod(backup, 0o600)
        self._atomic_write(path, content, mode=path.stat().st_mode & 0o777)
