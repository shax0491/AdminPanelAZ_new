from __future__ import annotations

import json

from app.services.dual_stack import (
    build_reverse_wg_config,
    endpoint,
    migrate_openvpn_config,
    migrate_xray_config,
    network_capabilities,
    new_ipv6_in_subnet,
    parse_network_snapshot,
)


ADDRS4 = [
    {"ifname": "eth0", "addr_info": [{"local": "91.200.100.5", "prefixlen": 24, "scope": "global"}]},
    {"ifname": "awg1", "addr_info": [{"local": "10.9.0.1", "prefixlen": 24, "scope": "global"}]},
]
ADDRS6 = [
    {
        "ifname": "eth0",
        "addr_info": [
            {
                "local": "2a0d:1234:0:6df::ec5a",
                "prefixlen": 64,
                "scope": "global",
                "dynamic": False,
            }
        ],
    },
    {
        "ifname": "awg1",
        "addr_info": [{"local": "fdaf:2900:9::1", "prefixlen": 64, "scope": "global"}],
    },
]
ROUTES6 = [
    {"dst": "default", "gateway": "2a0d:1234:0:600::1", "dev": "eth0"},
    {"dst": "fdaf:2900:9::/64", "dev": "awg1"},
]


def test_snapshot_uses_default_route_public_interface_not_ula_tunnel():
    result = parse_network_snapshot(ADDRS4, ADDRS6, ROUTES6)

    assert result["has_ipv4"] is True
    assert result["has_ipv6"] is True
    assert result["ipv4_address"] == "91.200.100.5"
    assert result["ipv6_address"] == "2a0d:1234:0:6df::ec5a"
    assert result["ipv6_subnet_prefix"] == "64"
    assert result["ipv6_network"] == "2a0d:1234:0:6df::/64"
    assert result["ipv6_gateway"] == "2a0d:1234:0:600::1"
    assert result["network_interface"] == "eth0"
    assert result["ipv6_rotation_supported"] is True


def test_snapshot_without_global_ipv6_is_explicitly_not_rotatable():
    result = parse_network_snapshot(ADDRS4, [], [])

    assert result["has_ipv4"] is True
    assert result["has_ipv6"] is False
    assert result["ipv6_address"] is None
    assert result["ipv6_rotation_supported"] is False


def test_endpoint_brackets_only_ipv6_literals():
    assert endpoint("de1.example.com", 443) == "de1.example.com:443"
    assert endpoint("198.51.100.7", 443) == "198.51.100.7:443"
    assert endpoint("2a0d:1234:0:6df::ec5a", 443) == "[2a0d:1234:0:6df::ec5a]:443"
    assert endpoint("[2a0d:1234:0:6df::ec5a]", 443) == "[2a0d:1234:0:6df::ec5a]:443"


def test_new_ipv6_is_cryptographically_random_inside_exact_64():
    first = new_ipv6_in_subnet("2a0d:1234:0:6df::/64")
    second = new_ipv6_in_subnet("2a0d:1234:0:6df::/64")

    assert first.startswith("2a0d:1234:0:6df:")
    assert second.startswith("2a0d:1234:0:6df:")
    assert first != second
    assert json.dumps(first)


def test_rotation_rejects_single_address_or_wrong_family():
    for value in ("2a0d:1234:0:6df::ec5a/128", "91.200.100.0/24", "not-an-ip"):
        try:
            new_ipv6_in_subnet(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value} must not be accepted")


def test_capability_payload_contains_both_reachability_families():
    result = network_capabilities(
        ADDRS4,
        ADDRS6,
        ROUTES6,
        ipv4_probe={"reachable": True, "latency_ms": 3.2},
        ipv6_probe={"reachable": False, "error": "timeout"},
    )

    assert result["ipv4_probe"]["reachable"] is True
    assert result["ipv6_probe"]["reachable"] is False
    assert result["ipv6_probe"]["error"] == "timeout"


def test_xray_dual_stack_never_uses_ipv6_source_for_direct_ipv4():
    source = {
        "inbounds": [{"tag": "in-direct", "port": 8443, "protocol": "vless"}],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom", "sendThrough": "2a0d:1234:0:6df::old"},
            {"tag": "proton", "protocol": "freedom", "sendThrough": "10.2.0.2"},
        ],
    }

    result = migrate_xray_config(
        source,
        transport_mode="auto",
        ipv6_address="2a0d:1234:0:6df::abcd",
    )

    direct = next(outbound for outbound in result["outbounds"] if outbound["tag"] == "direct")
    direct_v6 = next(outbound for outbound in result["outbounds"] if outbound["tag"] == "direct-v6")
    assert result["inbounds"][0]["listen"] == "::"
    assert "sendThrough" not in direct
    assert direct["settings"]["domainStrategy"] == "UseIPv6v4"
    assert direct_v6["sendThrough"] == "2a0d:1234:0:6df::abcd"
    assert direct_v6["settings"]["domainStrategy"] == "UseIPv6"
    assert next(outbound for outbound in result["outbounds"] if outbound["tag"] == "proton")["sendThrough"] == "10.2.0.2"
    assert {"type": "field", "ip": ["::/0"], "outboundTag": "direct-v6"} in result["routing"]["rules"]
    assert source["inbounds"][0] == {"tag": "in-direct", "port": 8443, "protocol": "vless"}


def test_xray_without_verified_ipv6_keeps_working_ipv4_config():
    source = {
        "inbounds": [{"tag": "in-direct", "listen": "0.0.0.0"}],
        "outbounds": [{"tag": "direct", "protocol": "freedom", "sendThrough": "2a0d:1234:0:6df::old"}],
    }

    result = migrate_xray_config(source, transport_mode="auto", ipv6_address=None)
    assert result["inbounds"] == [{**i, "sniffing": {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True}} for i in source["inbounds"]]
    assert result["inbounds"] == source["inbounds"]
    assert result["outbounds"] == [{
        "tag": "direct",
        "protocol": "freedom",
        "settings": {"domainStrategy": "UseIPv4"},
    }]


def test_xray_force_ipv4_does_not_bind_outbound_to_rotated_ipv6():
    source = {"inbounds": [{}], "outbounds": [{"tag": "direct", "protocol": "freedom"}]}

    result = migrate_xray_config(source, transport_mode="force_ipv4", ipv6_address="2602::1")

    assert result["outbounds"][0]["settings"]["domainStrategy"] == "UseIPv4"
    assert "sendThrough" not in result["outbounds"][0]


def test_openvpn_transport_ipv6_keeps_mode_a_inner_ipv6_block():
    source = "proto udp4\nport 50443\nblock-ipv6\n"

    result = migrate_openvpn_config(source, transport="udp6", inner_ipv6=False)

    assert "proto udp6\n" in result
    assert "multihome\n" in result
    assert "block-ipv6\n" in result
    assert "proto udp4" not in result


def test_openvpn_mode_b_adds_ula_and_ipv6_default_route():
    result = migrate_openvpn_config(
        "proto tcp4\nport 50080\nblock-ipv6\n",
        transport="tcp6",
        inner_ipv6=True,
        client_network="fdaf:2900:28::/64",
    )

    assert "proto tcp6\n" in result
    assert "server-ipv6 fdaf:2900:28::/64\n" in result
    assert 'push "route-ipv6 ::/0"\n' in result
    assert "block-ipv6" not in result


def test_reverse_wireguard_client_is_persistent_and_dual_stack():
    result = build_reverse_wg_config(
        private_key="exit-private",
        address4="10.250.0.2/30",
        address6="fdaf:250::2/64",
        peer_public_key="relay-public",
        peer_endpoint="77.37.174.115:52999",
        allowed_ips="10.250.0.1/32, fdaf:250::1/128",
    )

    assert "PrivateKey = exit-private" in result
    assert "Address = 10.250.0.2/30, fdaf:250::2/64" in result
    assert "Endpoint = 77.37.174.115:52999" in result
    assert "PersistentKeepalive = 15" in result


def test_snapshot_ignores_tunnel_interface_ipv6_as_native():
    """/128 на proton/warp/wg - адрес туннеля, а не нативный IPv6 сервера (иначе ложное «IPv6: offline»)."""
    tunnel_only = [{"ifname": "proton", "addr_info": [{"local": "2a07:b944::2:2", "prefixlen": 128, "scope": "global"}]}]
    assert parse_network_snapshot(ADDRS4, tunnel_only, [])["has_ipv6"] is False

    both = tunnel_only + [{"ifname": "ens3", "addr_info": [{"local": "2a0c:4ac1:4:2a5::d", "prefixlen": 64, "scope": "global"}]}]
    result = parse_network_snapshot(ADDRS4, both, [])
    assert result["ipv6_address"] == "2a0c:4ac1:4:2a5::d"
    assert result["network_interface"] == "ens3"
