"""AmneziaWG 3.0 (userspace amneziawg-go) runtime for the panel.

Separate from AWG 2.0 (native_awg2_runtime.py): own interface names, own
config directory, own subnet, own systemd unit (awg3@<iface>). Read-only in this
module; config generation and peer add/remove come in a later step.
"""

from __future__ import annotations

import shutil
import subprocess
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


def get_awg3_monitoring() -> dict[str, Any]:
    """Peers and latest handshake per AWG 3.0 interface (from `awg show dump`)."""
    result: dict[str, Any] = {}
    for label, name in AWG3_IFACES.items():
        peers = []
        out = _run(["awg", "show", name, "dump"])
        for line in out.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            peers.append({
                "public_key": parts[0],
                "allowed_ips": parts[3],
                "latest_handshake": int(parts[4] or 0),
                "rx": int(parts[5] or 0),
                "tx": int(parts[6] or 0),
            })
        result[label] = {"name": name, "up": _iface_up(name), "peers": peers}
    return {"ifaces": result}
