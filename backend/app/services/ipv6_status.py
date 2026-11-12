"""IPv6-состояние узла AntiZapret: настройки setup, эффективный режим (пишет up.sh), выход, карта Fake-IPv6."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from app.services.antizapret_settings import read_setup_env_value

STATE_FILE = Path("/run/antizapret-ipv6.state")
MODE_FILE = Path("/etc/knot-resolver/ipv6-mode")
_FAKE6_RE = re.compile(r"fd00:30::[0-9a-f:]+\s*:", re.I)


def _read_kv(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return {k.strip(): v.strip() for k, _, v in (ln.partition("=") for ln in lines) if k.strip()}


def _run(cmd: list[str], run=subprocess.run) -> str:
    try:
        return run(cmd, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read_ipv6_status(
    setup_path: Path,
    *,
    state_file: Path = STATE_FILE,
    mode_file: Path = MODE_FILE,
    run=subprocess.run,
) -> dict[str, Any]:
    state = _read_kv(state_file)
    effective = state.get("effective") or (mode_file.read_text().strip() if mode_file.exists() else "a")
    default_route = _run(["ip", "-6", "route", "show", "default"], run).strip()
    mapping = _run(["nft", "list", "map", "ip6", "antizapret", "mapping6"], run)
    return {
        "enabled": read_setup_env_value(setup_path, "ENABLE_IPV6", "n").lower() == "y",
        "requested_mode": read_setup_env_value(setup_path, "IPV6_MODE", "a"),
        "egress": read_setup_env_value(setup_path, "IPV6_EGRESS", "native"),
        "profile_default": read_setup_env_value(setup_path, "IPV6_PROFILE_DEFAULT", "dual"),
        "effective_mode": effective,
        "active_egress": state.get("egress", ""),
        "egress_interface": state.get("interface", ""),
        "reason": state.get("reason", ""),
        "updated": state.get("updated", ""),
        "native_default_route": bool(default_route),
        "fake6_mappings": len(_FAKE6_RE.findall(mapping)),
    }
