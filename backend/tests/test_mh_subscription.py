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


def test_subscription_contains_native_v4_ipv6_and_dual_stack_variants(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda host, port: [
            (module.socket.AF_INET, None, None, None, ("203.0.113.10", 0)),
            (module.socket.AF_INET6, None, None, None, ("2001:db8::10", 0, 0, 0)),
        ],
    )

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


def test_subscription_omits_dual_stack_domain_without_matching_dns(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda host, port: [(module.socket.AF_INET, None, None, None, ("203.0.113.10", 0))],
    )

    result = module.build_subscription(
        [node(port_proton=0, ipv6_address="2001:db8::10", dual_stack_host="ds.example.com")]
    )

    assert result.count("type: vless") == 2
    assert "ds.example.com" not in result


def test_subscription_uses_audited_ipv4_address(monkeypatch):
    module = load_builder()

    result = module.build_subscription([node(host="old.example.com", ipv4_address="203.0.113.99", port_proton=0)])

    assert 'server: "203.0.113.99"' in result
    assert "old.example.com" not in result


def test_dual_stack_dns_check_has_hard_timeout_and_is_cached(monkeypatch):
    import time

    module = load_builder()
    module.DNS_TIMEOUT = 0.2
    calls = []

    def hang(host, port):
        calls.append(host)
        time.sleep(3)
        return []

    monkeypatch.setattr(module.socket, "getaddrinfo", hang)
    data = node(ipv4_address="203.0.113.10", ipv6_address="2001:db8::10", dual_stack_host="ds.example.com")

    started = time.monotonic()
    first = module.build_subscription([data])
    second = module.build_subscription([data])

    assert time.monotonic() - started < 1.5
    assert "ds.example.com" not in first and "ds.example.com" not in second
    assert calls == ["ds.example.com"]  # второй вызов - из кэша
