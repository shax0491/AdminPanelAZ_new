from __future__ import annotations

import copy
import ipaddress
import secrets
from typing import Any


def endpoint(host: str, port: int) -> str:
    value = host.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        is_ipv6 = isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
    except ValueError:
        is_ipv6 = False
    return f"[{value}]:{port}" if is_ipv6 else f"{value}:{port}"


def new_ipv6_in_subnet(network: str) -> str:
    try:
        subnet = ipaddress.ip_network(network, strict=False)
    except ValueError as exc:
        raise ValueError("Некорректная IPv6-подсеть") from exc
    if not isinstance(subnet, ipaddress.IPv6Network) or subnet.prefixlen != 64:
        raise ValueError("Ротация поддерживается только внутри IPv6 /64")
    address = ipaddress.IPv6Address(int(subnet.network_address) + secrets.randbits(64))
    return str(address)


def _addresses(snapshot: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(item.get("ifname") or ""), info)
        for item in snapshot
        for info in item.get("addr_info", [])
        if info.get("local")
    ]


# туннельные интерфейсы (выход IPv6 через Proton/WARP, клиентские сети): их /128 - не нативный IPv6 сервера
_TUNNEL_IF_PREFIXES = ("proton", "warp", "wg", "awg", "nwg", "antizapret", "vpn", "tun", "tap", "docker", "br-", "veth")


def _public_ipv6(item: tuple[str, dict[str, Any]]) -> bool:
    ifname, info = item
    if ifname.startswith(_TUNNEL_IF_PREFIXES):
        return False
    try:
        address = ipaddress.IPv6Address(str(info["local"]).split("%", 1)[0])
    except ValueError:
        return False
    return info.get("scope", "global") == "global" and address.is_global


def parse_network_snapshot(
    addrs4: list[dict[str, Any]],
    addrs6: list[dict[str, Any]],
    routes6: list[dict[str, Any]],
) -> dict[str, Any]:
    default_route = next((route for route in routes6 if route.get("dst") == "default"), {})
    default_ifname = str(default_route.get("dev") or "")
    public6 = [item for item in _addresses(addrs6) if _public_ipv6(item)]
    selected6 = next((item for item in public6 if item[0] == default_ifname), public6[0] if public6 else None)

    public4 = []
    for item in _addresses(addrs4):
        try:
            address = ipaddress.IPv4Address(str(item[1]["local"]))
        except ValueError:
            continue
        if address.is_global:
            public4.append(item)
    selected4 = public4[0] if public4 else None

    result: dict[str, Any] = {
        "has_ipv4": bool(selected4),
        "has_ipv6": bool(selected6),
        "ipv4_address": selected4[1]["local"] if selected4 else None,
        "ipv6_address": None,
        "ipv6_subnet_prefix": None,
        "ipv6_network": None,
        "ipv6_gateway": default_route.get("gateway"),
        "network_interface": selected6[0] if selected6 else (selected4[0] if selected4 else None),
        "ipv6_rotation_supported": False,
    }
    if selected6:
        address = ipaddress.IPv6Interface(f"{selected6[1]['local']}/{selected6[1].get('prefixlen', 128)}")
        result.update(
            {
                "ipv6_address": str(address.ip),
                "ipv6_subnet_prefix": str(address.network.prefixlen),
                "ipv6_network": str(address.network),
                "ipv6_rotation_supported": address.network.prefixlen == 64,
            }
        )
    return result


def network_capabilities(
    addrs4: list[dict[str, Any]],
    addrs6: list[dict[str, Any]],
    routes6: list[dict[str, Any]],
    *,
    ipv4_probe: dict[str, Any],
    ipv6_probe: dict[str, Any],
) -> dict[str, Any]:
    return {
        **parse_network_snapshot(addrs4, addrs6, routes6),
        "ipv4_probe": ipv4_probe,
        "ipv6_probe": ipv6_probe,
    }


def migrate_xray_config(
    source: dict[str, Any],
    *,
    transport_mode: str,
    ipv6_address: str | None,
) -> dict[str, Any]:
    if transport_mode not in {"auto", "force_ipv6", "force_ipv4"}:
        raise ValueError("Недопустимый режим транспорта Xray")
    result = copy.deepcopy(source)
    for inbound in result.get("inbounds", []):
        inbound["listen"] = "::"
    strategy = {
        "auto": "UseIPv6v4",
        "force_ipv6": "UseIPv6",
        "force_ipv4": "UseIPv4",
    }[transport_mode]
    for outbound in result.get("outbounds", []):
        if outbound.get("protocol") != "freedom" or outbound.get("tag") != "direct":
            continue
        outbound.setdefault("settings", {})["domainStrategy"] = strategy
        if ipv6_address and transport_mode != "force_ipv4":
            outbound["sendThrough"] = str(ipaddress.IPv6Address(ipv6_address))
        else:
            outbound.pop("sendThrough", None)
    return result


def migrate_openvpn_config(
    source: str,
    *,
    transport: str,
    inner_ipv6: bool,
    client_network: str | None = None,
) -> str:
    if transport not in {"udp6", "tcp6"}:
        raise ValueError("OpenVPN transport должен быть udp6 или tcp6")
    if inner_ipv6:
        if not client_network:
            raise ValueError("Для IPv6 внутри OpenVPN нужна клиентская ULA-подсеть")
        network = ipaddress.IPv6Network(client_network)
    lines: list[str] = []
    protocol_written = False
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("proto "):
            if not protocol_written:
                lines.extend((f"proto {transport}", "multihome"))
                protocol_written = True
            continue
        if line == "multihome":
            if not protocol_written:
                lines.append("multihome")
            continue
        if line == "block-ipv6" and inner_ipv6:
            continue
        if line.startswith("server-ipv6 ") or line == 'push "route-ipv6 ::/0"':
            continue
        lines.append(raw)
    if not protocol_written:
        lines[0:0] = [f"proto {transport}", "multihome"]
    if inner_ipv6:
        lines.extend((f"server-ipv6 {network}", 'push "route-ipv6 ::/0"'))
    return "\n".join(lines) + "\n"


def build_reverse_wg_config(
    *,
    private_key: str,
    address4: str,
    address6: str,
    peer_public_key: str,
    peer_endpoint: str,
    allowed_ips: str,
) -> str:
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {address4}, {address6}\n"
        "\n[Peer]\n"
        f"PublicKey = {peer_public_key}\n"
        f"Endpoint = {peer_endpoint}\n"
        f"AllowedIPs = {allowed_ips}\n"
        "PersistentKeepalive = 15\n"
    )
