"""AmneziaWG 3.0 (userspace amneziawg-go) runtime for the panel.

Separate from AWG 2.0 (native_awg2_runtime.py): own interface names, own
config directory, own subnet, own systemd unit (awg3@<iface>). Read-only in this
module; config generation and peer add/remove come in a later step.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

AWG3_CONF_DIR = Path("/etc/amnezia/amneziawg3")
AWG3_IFACES = {
    "split": "awg1",  # antizapret split tunnel, subnet 10.9.0.0/24
}
AWG3_SUBNET = {"split": "10.9.0.0/24"}
AWG3_PORT = {"split": 51821}
AWG3_OBFUSCATION_KEYS = (
    "Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
    "H1", "H2", "H3", "H4", "HeaderProtectionKey",
)


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _iface_up(name: str) -> bool:
    return bool(_run(["ip", "-br", "link", "show", name]).strip())


def get_awg3_health() -> dict[str, Any]:
    """Binary/config presence and per-interface state for the AWG 3.0 tab."""
    tools_ok = shutil.which("awg") is not None
    go_ok = shutil.which("amneziawg-go") is not None
    ifaces = []
    for label, name in AWG3_IFACES.items():
        conf = AWG3_CONF_DIR / f"{name}.conf"
        ifaces.append({
            "label": label,
            "name": name,
            "port": AWG3_PORT[label],
            "subnet": AWG3_SUBNET[label],
            "conf_present": conf.is_file(),
            "up": _iface_up(name),
        })
    return {
        "tools_present": tools_ok,
        "userspace_present": go_ok,
        "conf_dir": str(AWG3_CONF_DIR),
        "ifaces": ifaces,
    }


def _registry_by_public_key() -> dict[str, tuple[str, dict]]:
    """clients.json (written by the agent) keyed by public key: {pubkey: (name, record)}."""
    try:
        data = json.loads((AWG3_CONF_DIR / "clients.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(rec.get("public_key")): (name, rec)
        for name, rec in data.items()
        if isinstance(rec, dict) and rec.get("public_key")
    }


def get_awg3_monitoring() -> dict[str, Any]:
    """Peers per AWG 3.0 interface, plus `clients` shaped like AWG 2.0 monitoring.

    `clients` items carry the registry name, handshake age in seconds and the
    split/full mode, so the panel maps them to traffic and online status the
    same way it does for AWG 2.0.
    """
    result: dict[str, Any] = {}
    clients: list[dict[str, Any]] = []
    registry = _registry_by_public_key()
    now = int(time.time())
    for label, name in AWG3_IFACES.items():
        peers = []
        out = _run(["awg", "show", name, "dump"])
        for line in out.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            handshake = int(parts[4] or 0)
            rx = int(parts[5] or 0)
            tx = int(parts[6] or 0)
            peers.append({
                "public_key": parts[0],
                "allowed_ips": parts[3],
                "latest_handshake": handshake,
                "rx": rx,
                "tx": tx,
            })
            client_name, record = registry.get(parts[0], (None, {}))
            clients.append({
                "iface": name,
                "name": client_name,
                "pubkey": parts[0],
                "endpoint": None if parts[2] in ("", "(none)") else parts[2],
                "allowed_ips": parts[3],
                "handshake_age_s": max(now - handshake, 0) if handshake > 0 else None,
                "rx": rx,
                "tx": tx,
                "mode": record.get("mode", "split"),
            })
        result[label] = {"name": name, "up": _iface_up(name), "peers": peers}
    return {"ifaces": result, "clients": clients}
