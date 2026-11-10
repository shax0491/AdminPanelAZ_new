"""Read-only статус egress-guard (az-guard) vpn-ноды.

Ничего не применяет и не меняет на ноде. Все внешние команды идут через runner(argv),
никакого shell. Конфиг не исполняется как shell; профили Proton в парсер попадают
только строки Address/AllowedIPs (PrivateKey/PresharedKey наружу не уходят).
"""
from __future__ import annotations

import ipaddress
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

Runner = Callable[[List[str]], Tuple[int, str]]

MAX_CONF_SIZE = 64 * 1024

GUARD_TABLE = "100"
GUARD_PRIORITY = "900"
GUARD_FWMARK = "0x1f40"
REVERSE_IFACE = "azr1"

CHAIN_GUARD = "AZ_EGRESS_GUARD"
CHAIN_MARK = "AZ_EGRESS_MARK"
CHAIN_NAT = "AZ_EGRESS_NAT"

_PROFILE_KEY_RE = re.compile(r"^\s*(?:Address|AllowedIPs)\s*=", re.IGNORECASE)


# ---------------------------------------------------------------------------
# конфиг /etc/az-guard.conf
# ---------------------------------------------------------------------------

def _split_words(s: str) -> List[str]:
    """Разбиение shell-like значения на слова с учётом кавычек (без glob/раскрытия)."""
    out: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    for ch in s:
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _strip_comment(val: str) -> str:
    for marker in (" #", "\t#"):
        idx = val.find(marker)
        if idx != -1:
            return val[:idx].strip()
    return val.strip()


def _parse_shell_vars(text: str) -> Dict[str, str]:
    """Минимальный парсер `KEY=VALUE` (в т.ч. многострочных значений в кавычках).

    НЕ исполняет конфиг: только выделение присваиваний верхнего уровня.
    """
    vars_: Dict[str, str] = {}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') or val.startswith("'"):
            q = val[0]
            body = val[1:]
            while True:
                idx = body.find(q)
                if idx != -1:
                    vars_[key] = body[:idx]
                    break
                if i >= n:
                    vars_[key] = body
                    break
                body += "\n" + lines[i]
                i += 1
        else:
            vars_[key] = _strip_comment(val)
    return vars_


def _parse_layers(raw: str, errors: List[str]) -> List[dict]:
    """LAYERS: 'name:iface:v4cidr[,..]:v6cidr[,..]' через пробел.

    Невалидные записи (нет name/iface) пропускаются и кладутся в errors.
    """
    layers: List[dict] = []
    for entry in _split_words(raw):
        parts = entry.split(":", 3)
        if len(parts) < 3 or not parts[0] or not parts[1]:
            errors.append(f"bad LAYERS entry: {entry}")
            continue
        v4 = [x for x in parts[2].split(",") if x]
        v6 = [x for x in parts[3].split(",") if x] if len(parts) > 3 else []
        layers.append({"name": parts[0], "iface": parts[1], "v4": v4, "v6": v6})
    return layers


_FAM_ALIASES = {
    "-4": "inet", "4": "inet", "ipv4": "inet", "inet": "inet",
    "-6": "inet6", "6": "inet6", "ipv6": "inet6", "inet6": "inet6",
}


def _parse_base_rules(raw: str) -> List[dict]:
    """BASE_RULES: строки '<fam>|<args>' (inet/inet6; -4/-6 нормализуются)."""
    out: List[dict] = []
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        fam, _, args = line.partition("|")
        fam = _FAM_ALIASES.get(fam.strip().lower())
        if fam is None:
            continue
        out.append({"family": fam, "args": args.strip()})
    return out


def parse_az_guard_conf(text: str) -> dict:
    errors: List[str] = []
    vars_ = _parse_shell_vars(text)
    return {
        "has_ipv6": vars_.get("HAS_IPV6", "").strip() in ("1", "yes", "true"),
        "egress_ifaces": _split_words(vars_.get("EGRESS_IFACES", "")),
        "ingress_ips": _split_words(vars_.get("INGRESS_IPS", "")),
        "layers": _parse_layers(vars_.get("LAYERS", ""), errors),
        "base_rules": _parse_base_rules(vars_.get("BASE_RULES", "")),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Proton-профили / слои v6
# ---------------------------------------------------------------------------

def _is_v6(addr: str) -> bool:
    try:
        return ipaddress.ip_interface(addr.strip()).version == 6
    except ValueError:
        return False


def proton_profile_ipv6_info(conf_text: str) -> dict:
    """Только Address/AllowedIPs из WG-профиля; ключи игнорируются."""
    info = {"has_v6": False, "addresses_v6": [], "allowed_v6": []}
    for raw in conf_text.replace("\r", "").split("\n"):
        line = raw.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip().lower(), val.strip()
        if key == "address":
            info["addresses_v6"].extend(a for a in (x.strip() for x in val.split(",")) if a and _is_v6(a))
        elif key == "allowedips":
            info["allowed_v6"].extend(a for a in (x.strip() for x in val.split(",")) if a and _is_v6(a))
    info["has_v6"] = bool(info["addresses_v6"] or info["allowed_v6"])
    return info


def layer_v6_status(*, has_ipv6: bool, layer_v6: List[str], profile_has_v6: Optional[bool]) -> str:
    if not has_ipv6:
        return "disabled"
    if not layer_v6:
        return "no_v6_subnets"
    if profile_has_v6 is False:
        return "profile_without_v6"
    if profile_has_v6 is None:
        return "profile_missing"
    return "active"


# ---------------------------------------------------------------------------
# выводы iptables / ip rule / table 100
# ---------------------------------------------------------------------------

def _jump_first(s: str, parent: str, chain: str) -> bool:
    """Первое '-A <parent>' правило должно быть ровно '-A <parent> -j <chain>'."""
    for raw in s.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line.startswith("-A " + parent + " "):
            continue
        return line.split("/*", 1)[0].strip() == f"-A {parent} -j {chain}"
    return False


def _fwmark_rule_present(ip_rule: str) -> bool:
    for raw in ip_rule.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line.startswith(GUARD_PRIORITY + ":"):
            continue
        if f"fwmark {GUARD_FWMARK}" in line and f"lookup {GUARD_TABLE}" in line:
            return True
    return False


def _blackhole_present(table100: str) -> bool:
    return any("blackhole" in line for line in table100.replace("\r", "").split("\n"))


_ROUTE_RE = re.compile(r"^default\s+dev\s+(\S+)(?:\s+metric\s+(\d+))?")
DEAD_METRIC = 5000


def _egress_routes(table100: str) -> Dict[str, str]:
    """iface -> 'live' (metric < 5000) | 'dead' (metric >= 5000)."""
    routes: Dict[str, str] = {}
    for raw in table100.replace("\r", "").split("\n"):
        m = _ROUTE_RE.match(raw.strip())
        if not m:
            continue
        metric = int(m.group(2)) if m.group(2) is not None else 0
        routes[m.group(1)] = "dead" if metric >= DEAD_METRIC else "live"
    return routes


def evaluate_guard(*, forward_s: str, mangle_pre_s: str, nat_post_s: str,
                   ip_rule: str, table100: str, has_ipv6: bool,
                   ip6_forward_s: str = "", ip6_mangle_pre_s: str = "",
                   ip6_nat_post_s: str = "") -> dict:
    jump_first = {
        "filter": _jump_first(forward_s, "FORWARD", CHAIN_GUARD),
        "mangle": _jump_first(mangle_pre_s, "PREROUTING", CHAIN_MARK),
        "nat": _jump_first(nat_post_s, "POSTROUTING", CHAIN_NAT),
    }
    if has_ipv6:
        jump_first["filter6"] = _jump_first(ip6_forward_s, "FORWARD", CHAIN_GUARD)
        jump_first["mangle6"] = _jump_first(ip6_mangle_pre_s, "PREROUTING", CHAIN_MARK)
        jump_first["nat6"] = _jump_first(ip6_nat_post_s, "POSTROUTING", CHAIN_NAT)
    else:
        jump_first["filter6"] = None
        jump_first["mangle6"] = None
        jump_first["nat6"] = None

    fwmark = _fwmark_rule_present(ip_rule)
    blackhole = _blackhole_present(table100)
    required = [jump_first["filter"], jump_first["mangle"], jump_first["nat"], fwmark, blackhole]
    if has_ipv6:
        required += [jump_first["filter6"], jump_first["mangle6"]]
    return {
        "jump_first": jump_first,
        "fwmark_rule": fwmark,
        "blackhole": blackhole,
        "egress_routes": _egress_routes(table100),
        "guard_active": all(required),
    }


# ---------------------------------------------------------------------------
# wg handshakes
# ---------------------------------------------------------------------------

def parse_handshake_ages(text: str, now: float) -> Dict[str, Optional[float]]:
    """Строки 'iface\\tpeer\\tts'; минимальный возраст по iface; ts==0 -> None."""
    ages: Dict[str, Optional[float]] = {}
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0]:
            continue
        iface, _peer, ts_s = parts
        try:
            ts = float(ts_s)
        except ValueError:
            continue
        if ts <= 0:
            ages.setdefault(iface, None)
            continue
        age = max(0.0, now - ts)
        if ages.get(iface) is None:
            ages[iface] = age
        else:
            ages[iface] = min(ages[iface], age)  # type: ignore[arg-type]
    return ages


# ---------------------------------------------------------------------------
# сборка полного статуса
# ---------------------------------------------------------------------------

def _panel_isolation(input_s: str) -> bool:
    for raw in input_s.replace("\r", "").split("\n"):
        if "az-panel-only" in raw and "DROP" in raw:
            return True
    return False


def collect_egress_status(runner: Runner, *, now: Optional[float] = None,
                          conf_path: str = "/etc/az-guard.conf",
                          wg_dir: str = "/etc/wireguard") -> dict:
    """Полный read-only статус. Любая ошибка runner -> None в поле + запись в errors."""
    if now is None:
        now = time.time()
    errors: List[str] = []

    def run(argv: List[str], *, quiet: bool = False) -> Optional[str]:
        try:
            rc, out = runner(list(argv))
        except Exception as exc:  # runner внешний — не роняем сбор
            errors.append(f"{argv[0]}: {exc}")
            return None
        if rc != 0:
            if not quiet:
                errors.append(f"{argv[0]}: exit {rc}: {out.strip()[:200]}")
            return None
        return out

    conf_out = run(["cat", conf_path], quiet=True)
    if conf_out is None:
        return {"installed": False, "errors": errors}

    conf = parse_az_guard_conf(conf_out[:MAX_CONF_SIZE])
    errors.extend(conf["errors"])
    has_ipv6 = conf["has_ipv6"]

    forward_s = run(["iptables", "-w", "-S", "FORWARD"]) or ""
    mangle_s = run(["iptables", "-w", "-t", "mangle", "-S", "PREROUTING"]) or ""
    nat_s = run(["iptables", "-w", "-t", "nat", "-S", "POSTROUTING"]) or ""
    ip_rule = run(["ip", "rule"]) or ""
    table100 = run(["ip", "route", "show", "table", GUARD_TABLE]) or ""
    input_s = run(["iptables", "-w", "-S", "INPUT"]) or ""

    ip6_forward_s = ip6_mangle_s = ip6_nat_s = ""
    if has_ipv6:
        ip6_forward_s = run(["ip6tables", "-w", "-S", "FORWARD"]) or ""
        ip6_mangle_s = run(["ip6tables", "-w", "-t", "mangle", "-S", "PREROUTING"]) or ""
        ip6_nat_s = run(["ip6tables", "-w", "-t", "nat", "-S", "POSTROUTING"]) or ""

    guard = evaluate_guard(
        forward_s=forward_s, mangle_pre_s=mangle_s, nat_post_s=nat_s,
        ip_rule=ip_rule, table100=table100, has_ipv6=has_ipv6,
        ip6_forward_s=ip6_forward_s, ip6_mangle_pre_s=ip6_mangle_s, ip6_nat_post_s=ip6_nat_s,
    )
    routes = dict(guard["egress_routes"])
    for iface in conf["egress_ifaces"]:
        routes.setdefault(iface, "missing")

    hs_parts: List[str] = []
    ifaces = list(dict.fromkeys(conf["egress_ifaces"] + [REVERSE_IFACE]))
    for iface in ifaces:
        out = run(["wg", "show", iface, "latest-handshakes"], quiet=True)
        if not out:
            continue
        for raw in out.replace("\r", "").split("\n"):
            line = raw.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) == 2 and parts[0]:
                hs_parts.append(f"{iface}\t{parts[0]}\t{parts[1]}")
    handshake_ages = parse_handshake_ages("\n".join(hs_parts), now)

    azr1_exists = run(["ip", "link", "show", REVERSE_IFACE], quiet=True) is not None

    egress_ifaces_v6: Dict[str, Optional[bool]] = {}
    for iface in conf["egress_ifaces"]:
        raw = run(["cat", f"{wg_dir}/{iface}.conf"], quiet=True)
        if raw is None:
            egress_ifaces_v6[iface] = None
            continue
        # в парсер — только Address/AllowedIPs, никаких ключей
        filtered = "\n".join(
            line for line in raw.replace("\r", "").split("\n") if _PROFILE_KEY_RE.match(line)
        )[:MAX_CONF_SIZE]
        egress_ifaces_v6[iface] = proton_profile_ipv6_info(filtered)["has_v6"]

    # слой выходит v6 через любой egress: v6 есть, если хотя бы один профиль его несёт
    _v = list(egress_ifaces_v6.values())
    profile_v6 = True if any(x is True for x in _v) else (False if _v and all(x is False for x in _v) else None)
    layers_v6 = [
        {
            "name": layer["name"],
            "iface": layer["iface"],
            "v6_status": layer_v6_status(
                has_ipv6=has_ipv6,
                layer_v6=layer["v6"],
                profile_has_v6=profile_v6,
            ),
        }
        for layer in conf["layers"]
    ]

    return {
        "installed": True,
        "has_ipv6": has_ipv6,
        "egress_ifaces": conf["egress_ifaces"],
        "ingress_ips": conf["ingress_ips"],
        "layers": conf["layers"],
        "base_rules": conf["base_rules"],
        "jump_first": guard["jump_first"],
        "fwmark_rule": guard["fwmark_rule"],
        "blackhole": guard["blackhole"],
        "egress_routes": routes,
        "guard_active": guard["guard_active"],
        "handshake_ages": handshake_ages,
        "reverse": {
            "iface": REVERSE_IFACE,
            "exists": azr1_exists,
            "handshake_age": handshake_ages.get(REVERSE_IFACE),
        },
        "panel_isolation": _panel_isolation(input_s),
        "egress_ifaces_v6": egress_ifaces_v6,
        "layers_v6": layers_v6,
        "errors": errors,
    }
