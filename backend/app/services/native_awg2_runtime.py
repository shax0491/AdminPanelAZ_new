"""Native AmneziaWG 2.0 runtime block/unblock (client.sh's *-am2.conf, real `awg` binary).

Mirrors wg_runtime.py's block/unblock design exactly (same on-disk peer parsing, same
runtime-only remove/restore via `awg set ... peer ... remove` / `awg set ... peer ...
allowed-ips ... preshared-key ...`, same `awg-quick strip` + `awg syncconf` fallback) — the
only difference is the binary name and the native config file locations. Deliberately
separate from the retired third-party az-awg2 overlay's app.services.awg2_runtime, which
targets a different config tree (/etc/amnezia/amneziawg) that native-only nodes never have.
"""

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.services.ip_geo import is_local_geoip_loaded, parse_client_endpoint

NATIVE_AWG2_CONFIG_FILES = {
    "antizapret": Path("/etc/amneziawg/antizapret2.conf"),
    "vpn": Path("/etc/amneziawg/vpn2.conf"),
}

# The `awg`/`awg-quick` binaries need the REAL kernel/systemd interface name — set up by
# client.sh's own `awg syncconf antizapret2`/`awg syncconf vpn2` calls and the
# amneziawg@antizapret2.service/amneziawg@vpn2.service units — which is NOT the same as the
# short "antizapret"/"vpn" labels this module otherwise uses everywhere (config file dict keys
# above, ifaces[].name in /awg2/monitoring, the frontend's lookup keys), kept for parity with
# WireGuard 1.5's actual antizapret/vpn interface names. Passing the label straight to `awg`
# silently no-ops against a nonexistent interface (empty/error output, swallowed) — this used
# to make monitoring always report 0 peers and made block/unblock always fail.
NATIVE_AWG2_IFACE_NAMES = {
    "antizapret": "antizapret2",
    "vpn": "vpn2",
}


def _real_iface_name(label: str) -> str:
    return NATIVE_AWG2_IFACE_NAMES.get(label, label)


COMMAND_TIMEOUT_SECONDS = 10
ONLINE_WINDOW_S = 180


def _normalize_client_name(client_name: str) -> str:
    return (client_name or "").strip().lower()


def _parse_peers(config_path: Path, interface_name: str, client_name: str) -> list[dict]:
    normalized = _normalize_client_name(client_name)
    if not config_path.exists():
        return []
    rows: list[dict] = []
    pending_client = ""
    current: dict | None = None

    def flush():
        nonlocal current
        if current and current.get("peer_public_key") and _normalize_client_name(current.get("client_name", "")) == normalized:
            rows.append({**current, "interface_name": interface_name})
        current = None

    for raw in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m_client = re.match(r"^#\s*Client\s*=\s*(.+)$", line, re.I)
        if m_client:
            pending_client = m_client.group(1).strip()
            continue
        if re.match(r"^\[Peer\]$", line, re.I):
            flush()
            current = {"client_name": pending_client, "peer_public_key": "", "allowed_ips": "", "preshared_key": ""}
            pending_client = ""
            continue
        if current is None:
            continue
        m_pub = re.match(r"^PublicKey\s*=\s*(.+)$", line, re.I)
        if m_pub:
            current["peer_public_key"] = m_pub.group(1).strip()
            continue
        m_ips = re.match(r"^AllowedIPs\s*=\s*(.+)$", line, re.I)
        if m_ips:
            current["allowed_ips"] = m_ips.group(1).strip()
            continue
        m_psk = re.match(r"^PresharedKey\s*=\s*(.+)$", line, re.I)
        if m_psk:
            current["preshared_key"] = m_psk.group(1).strip()
    flush()
    return rows


def _peer_specs_for_client(client_name: str, *, config_files: dict[str, Path] | None = None) -> list[dict]:
    specs: list[dict] = []
    files = config_files or NATIVE_AWG2_CONFIG_FILES
    for iface, path in files.items():
        specs.extend(_parse_peers(path, _real_iface_name(iface), client_name))
    return specs


def _collect_client_peers(client_name: str, *, config_files: dict[str, Path] | None = None) -> list[tuple[str, str]]:
    return [
        (spec["interface_name"], spec["peer_public_key"])
        for spec in _peer_specs_for_client(client_name, config_files=config_files)
        if spec.get("interface_name") and spec.get("peer_public_key")
    ]


def _run(args: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _sync_interface_from_stripped_config(interface_name: str, *, timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[bool, str]:
    strip_result = _run(["awg-quick", "strip", interface_name], timeout=timeout)
    if strip_result.returncode != 0:
        return False, (strip_result.stderr or "").strip() or "awg-quick strip failed"

    stripped_config = strip_result.stdout or ""
    if not stripped_config.strip():
        return False, "empty stripped config"

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as temp_file:
            temp_file.write(stripped_config)
            temp_path = temp_file.name
        sync_result = _run(["awg", "syncconf", interface_name, temp_path], timeout=timeout)
        if sync_result.returncode == 0:
            return True, ""
        return False, (sync_result.stderr or "").strip() or "awg syncconf failed"
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _restore_peer_spec(spec: dict, *, timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[bool, str]:
    interface_name = (spec.get("interface_name") or "").strip()
    peer_public_key = (spec.get("peer_public_key") or "").strip()
    if not interface_name or not peer_public_key:
        return False, "missing interface or public key"

    args = ["awg", "set", interface_name, "peer", peer_public_key]
    allowed_ips = (spec.get("allowed_ips") or "").strip()
    if allowed_ips:
        args.extend(["allowed-ips", allowed_ips])

    preshared_key = (spec.get("preshared_key") or "").strip()
    psk_path = None
    try:
        if preshared_key:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".psk", delete=False) as psk_file:
                psk_file.write(preshared_key.encode("ascii"))
                psk_path = psk_file.name
            args.extend(["preshared-key", psk_path])

        result = _run(args, timeout=timeout)
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or "").strip() or "awg set failed"
    finally:
        if psk_path:
            try:
                os.unlink(psk_path)
            except OSError:
                pass


def _load_native_peer_info() -> dict[str, dict[str, str]]:
    """pubkey -> {"name", "allowed_ips"} sourced from native server confs' `# Client =` comments.

    Mirrors _parse_peers' block structure (name/comments precede `[Peer]`, PublicKey/AllowedIPs
    follow it) but is not filtered to one client — monitoring needs every peer at once.
    """
    mapping: dict[str, dict[str, str]] = {}
    for path in NATIVE_AWG2_CONFIG_FILES.values():
        if not path.is_file():
            continue
        pending_name = ""
        current: dict[str, str] | None = None

        def flush():
            nonlocal current
            if current and current.get("pubkey"):
                mapping[current["pubkey"]] = {
                    "name": current.get("name") or current["pubkey"][:8],
                    "allowed_ips": current.get("allowed_ips", ""),
                }
            current = None

        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            m_client = re.match(r"^#\s*Client\s*=\s*(.+)$", line, re.I)
            if m_client:
                pending_name = m_client.group(1).strip()
                continue
            if re.match(r"^\[Peer\]$", line, re.I):
                flush()
                current = {"name": pending_name, "pubkey": "", "allowed_ips": ""}
                pending_name = ""
                continue
            if current is None:
                continue
            m_pub = re.match(r"^PublicKey\s*=\s*(.+)$", line, re.I)
            if m_pub:
                current["pubkey"] = m_pub.group(1).strip()
                continue
            m_ips = re.match(r"^AllowedIPs\s*=\s*(.+)$", line, re.I)
            if m_ips:
                current["allowed_ips"] = m_ips.group(1).strip()
        flush()
    return mapping


def _awg_show_dump(interface_name: str, *, timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    result = _run(["awg", "show", interface_name, "dump"], timeout=timeout)
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def get_monitoring() -> dict[str, Any]:
    """Live `awg show <iface> dump` snapshot for both native AWG2 interfaces.

    No stats.db equivalent exists natively, so this always does a live dump (same fallback
    path the az-awg2 overlay used when its stats.db was empty) — acceptable at NOC/traffic
    polling cadence since `awg show dump` is a cheap local syscall, not a network round trip.
    """
    peer_info = _load_native_peer_info()
    now = int(time.time())
    clients: list[dict[str, Any]] = []
    ifaces: list[dict[str, Any]] = []

    for interface_name in sorted(NATIVE_AWG2_CONFIG_FILES):
        dump_text = _awg_show_dump(_real_iface_name(interface_name))
        peer_count = 0
        for i, raw in enumerate(dump_text.splitlines()):
            if i == 0:
                continue
            fields = raw.split("\t")
            if len(fields) < 8:
                continue
            peer_count += 1
            pubkey = fields[0]
            try:
                handshake = int(fields[4] or 0)
                rx = int(fields[5] or 0)
                tx = int(fields[6] or 0)
            except ValueError:
                continue
            info = peer_info.get(pubkey, {})
            age = (now - handshake) if handshake else None
            online = bool(handshake and age is not None and age < ONLINE_WINDOW_S)
            raw_endpoint = fields[2] if len(fields) > 2 else ""
            endpoint = None if (not raw_endpoint or raw_endpoint == "(none)") else raw_endpoint
            clients.append(
                {
                    "name": info.get("name") or pubkey[:8],
                    "iface": interface_name,
                    "online": online,
                    "handshake_age_s": age,
                    "rx": rx,
                    "tx": tx,
                    "pubkey": pubkey,
                    "endpoint": endpoint,
                    "allowed_ips": info.get("allowed_ips") or None,
                }
            )
        ifaces.append({"name": interface_name, "peer_count": peer_count})

    return {"ifaces": ifaces, "clients": clients, "stats_available": False}


def get_client_stats(client_name: str) -> dict[str, Any] | None:
    normalized = _normalize_client_name(client_name)
    monitoring = get_monitoring()
    rx_life = 0
    tx_life = 0
    online = False
    handshake_age_s: int | None = None
    endpoint: str | None = None
    best_age: int | None = None
    matched = False

    for client in monitoring["clients"]:
        if _normalize_client_name(str(client.get("name", ""))) != normalized:
            continue
        matched = True
        rx_life += int(client.get("rx") or 0)
        tx_life += int(client.get("tx") or 0)
        age = client.get("handshake_age_s")
        if isinstance(age, int):
            if handshake_age_s is None or age < handshake_age_s:
                handshake_age_s = age
            if age < ONLINE_WINDOW_S:
                online = True
            if client.get("endpoint") and (best_age is None or age < best_age):
                endpoint = client.get("endpoint")
                best_age = age

    if not matched:
        return None

    return {
        "name": client_name,
        "online": online,
        "endpoint": endpoint,
        "handshake_age_s": handshake_age_s,
        "rx_life": rx_life,
        "tx_life": tx_life,
        "daily": [],
        "geo": _lookup_local_geo_for_endpoint(endpoint),
    }


def _lookup_local_geo_for_endpoint(endpoint: str | None) -> dict[str, str | None] | None:
    parsed = parse_client_endpoint(endpoint)
    lookup_ip = parsed.get("lookup_ip")
    if not lookup_ip or not is_local_geoip_loaded():
        return None
    from app.services import geoip_local

    return geoip_local.lookup_geo_local(lookup_ip)


def block_client_runtime(client_name: str) -> dict:
    peers = _collect_client_peers(client_name)
    if not peers:
        return {
            "success": False,
            "removed_count": 0,
            "blocked": 0,
            "error_count": 1,
            "errors": [{"interface": None, "stderr": "Пиры клиента не найдены"}],
        }

    removed: list[tuple[str, str]] = []
    errors: list[dict] = []
    for interface_name, peer_public_key in peers:
        result = _run(["awg", "set", interface_name, "peer", peer_public_key, "remove"])
        if result.returncode == 0:
            removed.append((interface_name, peer_public_key))
        else:
            errors.append(
                {
                    "interface": interface_name,
                    "peer_public_key": peer_public_key,
                    "stderr": (result.stderr or "").strip() or "awg set failed",
                }
            )

    removed_count = len(removed)
    return {
        "success": removed_count > 0,
        "removed_count": removed_count,
        "blocked": removed_count,
        "error_count": len(errors),
        "errors": errors,
    }


def sync_all_native_awg2_interfaces(*, timeout: int = COMMAND_TIMEOUT_SECONDS) -> dict:
    """Apply on-disk native AmneziaWG 2.0 server configs to running interfaces via awg syncconf."""
    synced: list[str] = []
    errors: list[dict] = []
    for interface_name in sorted(NATIVE_AWG2_CONFIG_FILES):
        ok, stderr = _sync_interface_from_stripped_config(_real_iface_name(interface_name), timeout=timeout)
        if ok:
            synced.append(interface_name)
        else:
            errors.append({"interface": interface_name, "stderr": stderr})
    return {
        "success": not errors,
        "synced": synced,
        "error_count": len(errors),
        "errors": errors,
    }


def unblock_client_runtime(client_name: str) -> dict:
    config_files = NATIVE_AWG2_CONFIG_FILES
    specs = _peer_specs_for_client(client_name, config_files=config_files)
    restored: list[str] = []
    errors: list[dict] = []

    if specs:
        for spec in specs:
            ok, stderr = _restore_peer_spec(spec)
            if ok:
                restored.append(spec["interface_name"])
            else:
                errors.append({"interface": spec.get("interface_name"), "stderr": stderr})
        synced_count = len(restored)
        return {
            "success": synced_count > 0,
            "synced_count": synced_count,
            "restored": synced_count,
            "error_count": len(errors),
            "errors": errors,
        }

    peers = _collect_client_peers(client_name, config_files=config_files)
    interfaces = sorted({iface for iface, _ in peers if iface})
    if not interfaces:
        interfaces = sorted(_real_iface_name(label) for label in config_files)

    synced: list[str] = []
    for interface_name in interfaces:
        ok, stderr = _sync_interface_from_stripped_config(interface_name)
        if ok:
            synced.append(interface_name)
        else:
            errors.append({"interface": interface_name, "stderr": stderr})

    synced_count = len(synced)
    return {
        "success": synced_count > 0,
        "synced_count": synced_count,
        "restored": synced_count,
        "error_count": len(errors),
        "errors": errors,
    }
