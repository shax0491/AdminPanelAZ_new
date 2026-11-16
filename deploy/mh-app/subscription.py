from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import socket
import time
from typing import Any


def _quoted(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


DNS_TIMEOUT = 2.0
DNS_CACHE_TTL = 300.0
_DNS_CACHE: dict[tuple[str, str, str], tuple[float, bool]] = {}


def _dual_stack_dns_matches(host: str, ipv4: str, ipv6: str) -> bool:
    key = (host, ipv4, ipv6)
    now = time.monotonic()
    cached = _DNS_CACHE.get(key)
    if cached and now - cached[0] < DNS_CACHE_TTL:
        return cached[1]
    result = _resolve_matches(host, ipv4, ipv6)
    _DNS_CACHE[key] = (now, result)  # отрицательный результат тоже кэшируем: зависший DNS не тормозит каждый запрос
    return result


def _resolve_matches(host: str, ipv4: str, ipv6: str) -> bool:
    try:
        expected4 = ipaddress.IPv4Address(ipv4)
        expected6 = ipaddress.IPv6Address(ipv6)
    except ValueError:
        return False
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        answers = pool.submit(socket.getaddrinfo, host, 0).result(timeout=DNS_TIMEOUT)
    except (OSError, concurrent.futures.TimeoutError):
        return False
    finally:
        pool.shutdown(wait=False)
    resolved4 = {ipaddress.IPv4Address(item[4][0]) for item in answers if item[0] == socket.AF_INET}
    resolved6 = {ipaddress.IPv6Address(item[4][0].split("%")[0]) for item in answers if item[0] == socket.AF_INET6}
    return expected4 in resolved4 and expected6 in resolved6


def _valid_ipv4(value: Any) -> str | None:
    try:
        return str(ipaddress.IPv4Address(str(value)))
    except ValueError:
        return None


def _entry(name: str, host: str, port: int, node: dict[str, Any]) -> str:
    return (
        "  - {name: "
        + _quoted(name)
        + ", type: vless, server: "
        + _quoted(host)
        + f", port: {int(port)}, uuid: "
        + _quoted(node["uuid"])
        + ", flow: xtls-rprx-vision, network: tcp, udp: true, tls: true, servername: "
        + _quoted(node["sni"])
        + ", client-fingerprint: chrome, reality-opts: {public-key: "
        + _quoted(node["pub"])
        + ", short-id: "
        + _quoted(node["sid"])
        + "}}"
    )


def build_subscription(nodes: list[dict[str, Any]], prio: bool = False, ipv4_only: bool = False) -> str:
    """Единая сборка подписки az-vless (её вызывает и production server.py).

    Имена «<флаг> <узел> | VLESS Proton|прямой» не меняются. prio=True («Приоритет IPv6»): тот же узел и вариант,
    но порт со входа routeOnly=false (port_proton_v6 / port_direct_v6) - IPv4-запрос Dual-Stack сайта выходит по IPv6.
    ipv4_only=True: без IPv6 и Dual-Stack вариантов узла. Отдельных видимых ⚡-входов нет: их работу выполняет prio.
    """
    lines = ["proxies:"]
    for node in nodes:
        # ipv4_address, не являющийся IPv4 (в production-узле DE1 там 2001:db8::1), не используем как адрес входа
        ipv4_host = _valid_ipv4(node.get("ipv4_address")) or str(node["host"])
        ipv6 = None if ipv4_only else (node.get("ipv6_address") or node.get("ipv6"))
        if ipv6 and node.get("ipv6_probe_status") != "online":
            ipv6 = None
        dual_stack_host = None if ipv4_only else (node.get("dual_stack_host") or node.get("ds_host"))
        if dual_stack_host and ipv6 and not _dual_stack_dns_matches(str(dual_stack_host), ipv4_host, str(ipv6).split("/")[0]):
            dual_stack_host = None
        for mode, key, key_v6, default in (
            ("Proton", "port_proton", "port_proton_v6", 8445),
            ("прямой", "port_direct", "port_direct_v6", 8443),
        ):
            port = node.get(key, default)
            if not port:  # узел без Proton (port_proton = 0) - только прямой вариант
                continue
            if prio and not ipv4_only:
                port = node.get(key_v6) or port
            variants = [(ipv4_host, mode)]
            if ipv6:
                address = str(ipaddress.IPv6Address(str(ipv6).split("/")[0]))
                variants.append((address, "IPv6" if mode == "прямой" else f"{mode} IPv6"))
            if dual_stack_host:
                variants.append((str(dual_stack_host), "Dual-Stack" if mode == "прямой" else f"{mode} Dual-Stack"))
            for host, variant in variants:
                lines.append(_entry(f"{node['flag']} {node['key']} | VLESS {variant}", host, port, node))
    return "\n".join(lines) + "\n"
