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


def build_subscription(nodes: list[dict[str, Any]]) -> str:
    lines = ["proxies:"]
    for node in nodes:
        ipv4_host = str(node.get("ipv4_address") or node["host"])
        ipv6 = node.get("ipv6_address") or node.get("ipv6")
        if ipv6 and node.get("ipv6_probe_status") != "online":
            ipv6 = None
        dual_stack_host = node.get("dual_stack_host") or node.get("ds_host")
        if dual_stack_host and ipv6 and not _dual_stack_dns_matches(str(dual_stack_host), ipv4_host, str(ipv6).split("/")[0]):
            dual_stack_host = None
        for mode, port in (("Proton", node.get("port_proton", 8445)), ("прямой", node.get("port_direct", 8443))):
            if not port:
                continue
            variants = [(ipv4_host, mode)]
            if ipv6:
                address = str(ipaddress.IPv6Address(str(ipv6).split("/")[0]))
                variants.append((address, "IPv6" if mode == "прямой" else f"{mode} IPv6"))
            if dual_stack_host:
                variants.append((str(dual_stack_host), "Dual-Stack" if mode == "прямой" else f"{mode} Dual-Stack"))
            for host, variant in variants:
                name = f"{node['flag']} {node['key']} | VLESS {variant}"
                lines.append(
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
    return "\n".join(lines) + "\n"
