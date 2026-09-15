"""WARP geo-check + provider status for a single AntiZapret node.

Читает только безопасные для отображения поля из /root/antizapret/setup
(WARP_PROVIDER, ANTIZAPRET_WARP, VPN_WARP, факт заполненности Proton-ключей —
никогда сами ключи) и умеет проверить, каким гео/страной видят исходящий
трафик сервера внешние сервисы (Cloudflare trace, YouTube GL-поле) —
привязываясь к конкретному WARP-интерфейсу через `curl --interface`, а не
просто к маршруту по умолчанию, чтобы результат отражал то, что реально
увидит клиент через этот egress, а не сырой IP хоста.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal

CURL_TIMEOUT_SECONDS = 10
GeoScope = Literal["antizapret", "vpn", "raw"]

_SCOPE_INTERFACE = {
    "antizapret": "warp-antizapret",
    "vpn": "warp-vpn",
    "raw": None,
}

_YOUTUBE_GL_RE = re.compile(r'"GL"\s*:\s*"([A-Z]{2})"')
_TRACE_FIELD_RE = re.compile(r"^(ip|loc|colo)=(.*)$", re.MULTILINE)

_SAFE_SETUP_KEYS = {
    "WARP_PROVIDER",
    "ANTIZAPRET_WARP",
    "VPN_WARP",
}
_PROTON_PRESENCE_KEYS = {
    "PROTON_ANTIZAPRET_PRIVATE_KEY": "proton_antizapret_configured",
    "PROTON_VPN_PRIVATE_KEY": "proton_vpn_configured",
}


def _read_setup_file(antizapret_path: Path) -> dict[str, str]:
    setup_file = antizapret_path / "setup"
    values: dict[str, str] = {}
    if not setup_file.is_file():
        return values
    for line in setup_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def read_warp_status(antizapret_path: Path) -> dict:
    """Безопасный снимок настроек WARP: без приватных ключей и эндпоинтов."""
    raw = _read_setup_file(antizapret_path)
    status: dict[str, object] = {key.lower(): raw.get(key, "") for key in _SAFE_SETUP_KEYS}
    for src_key, out_key in _PROTON_PRESENCE_KEYS.items():
        status[out_key] = bool(raw.get(src_key, "").strip())
    return status


def _run_curl(url: str, *, interface: str | None) -> tuple[bool, str]:
    args = ["curl", "-s", "-4", "--max-time", str(CURL_TIMEOUT_SECONDS)]
    if interface:
        args += ["--interface", interface]
    args.append(url)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=CURL_TIMEOUT_SECONDS + 5)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, f"curl exit {result.returncode}: {result.stderr.strip()[:200]}"
    return True, result.stdout


def check_warp_geo(scope: GeoScope, antizapret_path: Path | None = None) -> dict:
    """Проверить гео исходящего трафика для scope (antizapret/vpn/raw).

    Для antizapret/vpn привязывается через `curl --interface warp-*` к
    реальному WARP-интерфейсу - если он сейчас не поднят (провайдер Proton
    без активного WARP, или ANTIZAPRET_WARP=1/none), возвращает понятную
    ошибку вместо гео сырого IP хоста, которое может ввести в заблуждение.
    """
    interface = _SCOPE_INTERFACE[scope]
    result: dict[str, object] = {"scope": scope, "interface": interface}

    ok, trace_out = _run_curl("https://1.1.1.1/cdn-cgi/trace", interface=interface)
    if not ok:
        result["error"] = (
            f"Интерфейс {interface} недоступен или не поднят"
            if interface
            else "Не удалось обратиться к 1.1.1.1"
        ) + f" ({trace_out[:200]})"
        return result

    for match in _TRACE_FIELD_RE.finditer(trace_out):
        field, value = match.group(1), match.group(2)
        result[f"cloudflare_{field}"] = value

    ok_yt, yt_out = _run_curl("https://www.youtube.com/", interface=interface)
    youtube_gl = None
    if ok_yt:
        gl_match = _YOUTUBE_GL_RE.search(yt_out)
        if gl_match:
            youtube_gl = gl_match.group(1)
    result["youtube_gl"] = youtube_gl

    country = result.get("cloudflare_loc")
    flagged_values = [v for v in (country, youtube_gl) if v]
    result["flagged_as_ru"] = any(v == "RU" for v in flagged_values)
    result["checked_fields"] = len(flagged_values)
    return result
