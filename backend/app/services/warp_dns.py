"""DNS через WARP: куда реально уходят запросы kresd к внешним резолверам и сколько DNS-запросов
клиентов перехватывает сервер.

Клиентский DNS отвечает локальный kresd, а его запросы к внешним резолверам - трафик самого
сервера. kresd@1 (российские резолверы) ходит с адреса сервера всегда. kresd@2 (зарубежные:
домены из proxy.rpz/warp.rpz и DNS полного VPN) при ANTIZAPRET_WARP_DNS=y up.sh выводит в WARP:
адрес источника = адрес WARP-интерфейса, правило "from <адрес>" ведёт в его таблицу. По умолчанию
опция выключена, как у апстрима.

Всё читается только из ядра и kresd (ip route get, управляющий сокет kresd, счётчики iptables),
поэтому показывает то, что действует сейчас, а не то, что записано в setup.
"""

from __future__ import annotations

import re
import socket
import subprocess
import time
from pathlib import Path

from app.services.warp_geo import _read_setup_file

KRESD_CONF = Path("/etc/knot-resolver/kresd.conf")
FALLBACK_LUA = Path("/usr/lib/knot-resolver/kres_modules/fallback.lua")
KRESD_CONTROL = {1: "/run/knot-resolver/control/1", 2: "/run/knot-resolver/control/2"}

# Наборы DNS, которые up.sh выводит в WARP: 2 Cloudflare/Quad9/ControlD/UltraDNS, 4 Google, 5 AdGuard.
# Российские (1 MSK-IX/НСДИ/ТТК, 3 Яндекс, 6-8 Comss/XBox/GeoHide) остаются на адресе сервера.
FOREIGN_DNS_SETS = {"2", "4", "5"}

_SET_BLOCK_RE = re.compile(r"dns == (\d+) then(.*?)(?=\n\s*(?:elseif|else)\b)", re.S)
_RESOLVER_RE = re.compile(r"'(\d{1,3}(?:\.\d{1,3}){3})(?:@\d+)?'")
_DNS_CHOICE_RE = re.compile(r"^local (dns[12]) = (\d+)", re.M)
_FALLBACK_RE = re.compile(r"action\s*=\s*policy\.FORWARD\(\{(.*?)\}\)", re.S)
_ROUTE_DEV_RE = re.compile(r"\bdev (\S+)")
_OUTGOING_RE = re.compile(r"'(\d{1,3}(?:\.\d{1,3}){3})'")
# Строка iptables-save -c: "[пакеты:байты] -A PREROUTING -s 10.29.0.0/16 -p udp ... --dport 53 ..."
_SAVE_LINE_RE = re.compile(r"^\[(\d+):\d+\]\s+-A\s+PREROUTING\s+(.*)$")
_SAVE_SOURCE_RE = re.compile(r"(?<!!)\s-s\s+(\S+)")
_SAVE_PROTO_RE = re.compile(r"\s-p\s+(\S+)")


def parse_resolver_sets(conf_text: str) -> dict[str, list[str]]:
    """Наборы резолверов из forward() в kresd.conf: {"1": [ip, ...], ...} без повторов и портов."""
    sets: dict[str, list[str]] = {}
    for number, block in _SET_BLOCK_RE.findall(conf_text):
        sets[number] = list(dict.fromkeys(_RESOLVER_RE.findall(block)))
    return sets


def parse_dns_choice(conf_text: str) -> dict[str, str]:
    """Какие наборы выбраны: {"dns1": "1", "dns2": "2"} (setup.sh правит эти строки при установке)."""
    return {name: value for name, value in _DNS_CHOICE_RE.findall(conf_text)}


def parse_fallback_resolvers(lua_text: str) -> list[str]:
    match = _FALLBACK_RE.search(lua_text)
    return list(dict.fromkeys(_RESOLVER_RE.findall(match.group(1)))) if match else []


def parse_outgoing(reply: str) -> str | None:
    """Ответ управляющего сокета на net.outgoing_v4(): "> '10.2.0.2'" или "> nil"."""
    match = _OUTGOING_RE.search(reply)
    return match.group(1) if match else None


def read_kresd_outgoing(instance: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    """(удалось ли спросить kresd, адрес источника или None = адрес сервера)."""
    path = KRESD_CONTROL[instance]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(b"net.outgoing_v4()\n")
            chunks: list[bytes] = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
                if b"'" in data or b"nil" in data:
                    break
    except OSError:
        return False, None
    return True, parse_outgoing(b"".join(chunks).decode("utf-8", errors="replace"))


def route_dev(ip: str, source: str | None = None) -> str | None:
    """Интерфейс, через который ядро отправит пакет к ip (с адреса source, если задан)."""
    cmd = ["ip", "-o", "-4", "route", "get", ip]
    if source:
        cmd += ["from", source]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = _ROUTE_DEV_RE.search(result.stdout) if result.returncode == 0 else None
    return match.group(1) if match else None


def _iptables_save(table: str) -> str:
    try:
        result = subprocess.run(["iptables-save", "-c", "-t", table], capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _subnet_label(source: str, client_prefix: str) -> str:
    labels = {
        f"{client_prefix}.29.0.0/16": "AntiZapret VPN",
        f"{client_prefix}.28.0.0/16": "Полный VPN",
        f"{client_prefix}.28.0.0/15": "Все VPN",
        "10.9.0.0/24": "AmneziaWG 3 — AntiZapret",
        "10.9.1.0/24": "AmneziaWG 3 — полный VPN",
    }
    return labels.get(source, source)


def _add_counter(rows: dict[str, dict], rule: str, packets: int, client_prefix: str) -> None:
    source_match = _SAVE_SOURCE_RE.search(" " + rule)
    proto_match = _SAVE_PROTO_RE.search(" " + rule)
    if not source_match or not proto_match or proto_match.group(1) not in ("udp", "tcp"):
        return
    source = source_match.group(1)
    row = rows.setdefault(source, {"subnet": source, "label": _subnet_label(source, client_prefix), "udp": 0, "tcp": 0})
    row[proto_match.group(1)] += packets


def parse_dns_counters(nat_save: str, mangle_save: str, client_prefix: str = "10") -> dict:
    """Счётчики DNS клиентов по подсетям (вывод iptables-save -c).

    intercepted - правила DNAT порта 53 на локальный kresd: в таблице nat считается только первый
    пакет соединения, поэтому это число DNS-потоков, а не запросов. foreign - учётные правила
    az-dns-foreign (без цели) из up.sh: пакеты на порт 53 к чужим DNS, а не к DNS туннеля. Так
    видно устройства и роутеры, которые резолвят мимо DNS AntiZapret (их запросы всё равно
    перехватываются, если попали в туннель). Если таких правил на узле нет (скрипты старее),
    foreign = None.
    """
    intercepted: dict[str, dict] = {}
    for line in nat_save.splitlines():
        match = _SAVE_LINE_RE.match(line.strip())
        if not match:
            continue
        rule = match.group(2)
        if "--dport 53" not in rule or "-j DNAT" not in rule or "--to-destination 127." not in rule:
            continue
        _add_counter(intercepted, rule, int(match.group(1)), client_prefix)

    foreign: dict[str, dict] | None = None
    for line in mangle_save.splitlines():
        match = _SAVE_LINE_RE.match(line.strip())
        if not match or "az-dns-foreign" not in match.group(2):
            continue
        foreign = foreign if foreign is not None else {}
        _add_counter(foreign, match.group(2), int(match.group(1)), client_prefix)

    return {
        "intercepted": list(intercepted.values()),
        "foreign": list(foreign.values()) if foreign is not None else None,
    }


def _warp_active(raw: dict[str, str]) -> bool:
    return raw.get("ANTIZAPRET_WARP", "1") in ("2", "3", "4") or raw.get("VPN_WARP", "1") in ("2", "3", "4")


def evaluate_status(
    *, enabled: bool, warp_active: bool, dns2: str, outgoing2: str | None, kresd2_devs: list[str | None]
) -> tuple[str, str]:
    """(код, пояснение). Коды: off / no_warp / russian_set / ok / leak."""
    if not enabled:
        return "off", "Выключено (как у апстрима): kresd ходит к резолверам с IP сервера. Включить — ANTIZAPRET_WARP_DNS=y"
    if not warp_active:
        return "no_warp", "Включено, но WARP не используется (режимы WARP = 1): выводить DNS некуда"
    if dns2 not in FOREIGN_DNS_SETS:
        return "russian_set", f"Набор DNS kresd@2 = {dns2 or '?'} (российский): через WARP не выводится, иначе резолверы не отвечают"
    devs = [dev for dev in kresd2_devs if dev]
    if outgoing2 and devs and all(dev.startswith("warp-") for dev in devs):
        return "ok", f"Зарубежный DNS (kresd@2) идёт через WARP с адреса {outgoing2}"
    if not outgoing2:
        return "leak", "Утечка: kresd@2 отправляет запросы с IP сервера (адрес WARP не задан — WARP не поднят или up.sh не применён)"
    return "leak", "Утечка: часть запросов kresd@2 уходит мимо WARP"


def dns_diagnostics(antizapret_path: Path) -> dict:
    raw = _read_setup_file(antizapret_path)
    enabled = raw.get("ANTIZAPRET_WARP_DNS", "").strip().lower() == "y"
    warp_active = _warp_active(raw)
    client_prefix = "172" if raw.get("ALTERNATIVE_CLIENT_IP", "n") == "y" else "10"

    conf_text = KRESD_CONF.read_text(encoding="utf-8", errors="replace") if KRESD_CONF.is_file() else ""
    fallback_text = FALLBACK_LUA.read_text(encoding="utf-8", errors="replace") if FALLBACK_LUA.is_file() else ""
    sets = parse_resolver_sets(conf_text)
    choice = parse_dns_choice(conf_text)
    fallback = parse_fallback_resolvers(fallback_text)

    reachable1, outgoing1 = read_kresd_outgoing(1)
    reachable2, outgoing2 = read_kresd_outgoing(2)

    resolvers: list[dict] = []
    kresd2_devs: list[str | None] = []
    for instance, set_key, outgoing in ((1, "dns1", outgoing1), (2, "dns2", outgoing2)):
        set_number = choice.get(set_key, "")
        for role, ips in (("основной", sets.get(set_number, [])), ("fallback", fallback)):
            for ip in ips:
                dev = route_dev(ip, outgoing)
                if instance == 2 and role == "основной":
                    kresd2_devs.append(dev)
                resolvers.append({
                    "instance": f"kresd@{instance}",
                    "set": set_number,
                    "role": role,
                    "ip": ip,
                    "interface": dev,
                    "via_warp": bool(dev and dev.startswith("warp-")),
                })

    status_code, status_text = evaluate_status(
        enabled=enabled, warp_active=warp_active, dns2=choice.get("dns2", ""),
        outgoing2=outgoing2, kresd2_devs=kresd2_devs,
    )
    counters = parse_dns_counters(_iptables_save("nat"), _iptables_save("mangle"), client_prefix)
    return {
        "status": status_code,
        "status_text": status_text,
        "enabled": enabled,
        "warp_active": warp_active,
        "dns1": choice.get("dns1", ""),
        "dns2": choice.get("dns2", ""),
        "kresd": {
            "kresd@1": {"reachable": reachable1, "outgoing": outgoing1},
            "kresd@2": {"reachable": reachable2, "outgoing": outgoing2},
        },
        "resolvers": resolvers,
        "counters": counters,
        "checked_at": int(time.time()),
    }
