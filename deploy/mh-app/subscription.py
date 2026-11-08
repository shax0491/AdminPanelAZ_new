from __future__ import annotations

import ipaddress
import json
from typing import Any


def _quoted(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def build_subscription(nodes: list[dict[str, Any]]) -> str:
    lines = ["proxies:"]
    for node in nodes:
        for mode, port in (("Proton", node.get("port_proton", 8445)), ("прямой", node.get("port_direct", 8443))):
            if not port:
                continue
            variants = [(str(node["host"]), mode)]
            ipv6 = node.get("ipv6_address") or node.get("ipv6")
            if ipv6:
                address = str(ipaddress.IPv6Address(str(ipv6).split("/")[0]))
                variants.append((address, "IPv6" if mode == "прямой" else f"{mode} IPv6"))
            dual_stack_host = node.get("dual_stack_host") or node.get("ds_host")
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
