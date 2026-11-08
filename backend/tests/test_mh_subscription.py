from __future__ import annotations

import importlib.util
from pathlib import Path


def load_builder():
    path = Path(__file__).parents[2] / "deploy" / "mh-app" / "subscription.py"
    spec = importlib.util.spec_from_file_location("mh_subscription", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node(**overrides):
    value = {
        "flag": "DE",
        "key": "DE1",
        "host": "203.0.113.10",
        "port_proton": 8445,
        "port_direct": 8443,
        "uuid": "00000000-0000-0000-0000-000000000000",
        "sni": "example.com",
        "pub": "public",
        "sid": "short",
    }
    value.update(overrides)
    return value


def test_subscription_contains_native_v4_ipv6_and_dual_stack_variants():
    module = load_builder()

    result = module.build_subscription(
        [node(ipv6_address="2001:db8::10", dual_stack_host="ds.example.com")]
    )

    assert 'server: "203.0.113.10"' in result
    assert 'server: "2001:db8::10"' in result
    assert 'server: "ds.example.com"' in result
    assert "VLESS IPv6" in result
    assert "VLESS Dual-Stack" in result
    assert result.count("type: vless") == 6


def test_subscription_does_not_invent_ipv6_for_ipv4_only_node():
    module = load_builder()

    result = module.build_subscription([node(port_proton=0)])

    assert result.count("type: vless") == 1
    assert "IPv6" not in result
    assert "Dual-Stack" not in result
