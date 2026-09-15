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
_PROTON_ADDRESS_KEY = {
    "antizapret": "PROTON_ANTIZAPRET_ADDRESS",
    "vpn": "PROTON_VPN_ADDRESS",
}
_IP_ADDR_RE = re.compile(r"inet (\d+\.\d+\.\d+\.\d+)/")


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


def _interface_local_ip(interface: str) -> str | None:
    try:
        result = subprocess.run(
            ["ip", "-o", "addr", "show", interface],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    match = _IP_ADDR_RE.search(result.stdout)
    return match.group(1) if match else None


def _check_tunnel_matches_config(scope: GeoScope, interface: str, antizapret_path: Path | None) -> dict:
    """Сверить реально поднятый IP туннеля с тем, что задан в setup для Proton.

    Реальный найденный случай: конфиг правят вручную (nano setup), но забывают
    выполнить up.sh - туннель продолжает жить со СТАРОЙ регистрацией (например,
    ещё Cloudflare, оставшийся от предыдущего провайдера) сколько угодно долго,
    хотя setup уже говорит Proton с валидными ключами. Без явного сравнения
    это выглядит как настоящий результат геопроверки, хотя на деле проверяется
    не тот провайдер вообще.
    """
    if antizapret_path is None:
        return {}
    raw = _read_setup_file(antizapret_path)
    if raw.get("WARP_PROVIDER", "").strip() != "proton":
        return {}
    expected_key = _PROTON_ADDRESS_KEY.get(scope)
    expected_ip = raw.get(expected_key, "").strip() if expected_key else ""
    if not expected_ip:
        return {}
    actual_ip = _interface_local_ip(interface)
    if actual_ip is None:
        return {}
    matches = actual_ip == expected_ip
    out: dict[str, object] = {"tunnel_matches_config": matches}
    if not matches:
        out["tunnel_mismatch_detail"] = (
            f"Туннель поднят с IP {actual_ip}, а в конфиге для Proton задан {expected_ip} - "
            "похоже, setup правили вручную, но /root/antizapret/up.sh не выполнялся. "
            "Результат проверки ниже может относиться к СТАРОМУ провайдеру, а не к Proton. "
            "Выполните up.sh на узле."
        )
    return out


def check_warp_geo(scope: GeoScope, antizapret_path: Path | None = None) -> dict:
    """Проверить гео исходящего трафика для scope (antizapret/vpn/raw).

    Для antizapret/vpn привязывается через `curl --interface warp-*` к
    реальному WARP-интерфейсу - если он сейчас не поднят (провайдер Proton
    без активного WARP, или ANTIZAPRET_WARP=1/none), возвращает понятную
    ошибку вместо гео сырого IP хоста, которое может ввести в заблуждение.
    """
    interface = _SCOPE_INTERFACE[scope]
    result: dict[str, object] = {"scope": scope, "interface": interface}

    if interface:
        result.update(_check_tunnel_matches_config(scope, interface, antizapret_path))

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
