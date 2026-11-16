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
        "ipv6_probe_status": "online",
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


def test_subscription_omits_ipv6_with_failed_probe():
    module = load_builder()
    result = module.build_subscription(
        [node(port_proton=0, ipv6_address="2001:db8::10", ipv6_probe_status="offline")]
    )

    assert result.count("type: vless") == 1
    assert "2001:db8::10" not in result


def prod_node(**overrides):
    """Узел в формате production nodes.json (без ipv6_address): порты v6-входов, как у DE2/DE4/NL2."""
    value = {"host": "de4.example.com", "key": "DE4", "port_direct_v6": 8449, "port_proton_v6": 8450}
    value.update(overrides)
    return node(**value)


def ports_by_name(result):
    import re
    return {m.group(1): int(m.group(2)) for m in re.finditer(r'name: "([^"]+)".*?port: (\d+)', result)}


def test_default_build_matches_production_names_and_ports():
    module = load_builder()
    result = module.build_subscription([prod_node(port_direct=8443)])
    assert ports_by_name(result) == {"DE DE4 | VLESS Proton": 8445, "DE DE4 | VLESS прямой": 8443}
    assert "⚡" not in result  # отдельных видимых ⚡-входов в карточках нет


def test_prio_replaces_ports_with_routeonly_false_inbounds():
    module = load_builder()
    result = module.build_subscription([prod_node(port_direct=443)], prio=True)
    assert ports_by_name(result) == {"DE DE4 | VLESS Proton": 8450, "DE DE4 | VLESS прямой": 8449}
    assert 'server: "de4.example.com"' in result  # прежнее имя и адрес, меняется только порт


def test_prio_keeps_normal_port_when_node_has_no_v6_inbound():
    module = load_builder()
    result = module.build_subscription([node(port_direct=8447)], prio=True)
    assert ports_by_name(result) == {"DE DE1 | VLESS Proton": 8445, "DE DE1 | VLESS прямой": 8447}


def test_all_production_nodes_keep_names_in_both_modes():
    module = load_builder()
    nodes = [prod_node(key=k, flag="X", port_direct=pd) for k, pd in (("DE2", 443), ("DE1", 8443), ("DE4", 8443), ("NL2", 8443), ("FI1", 8443), ("FR1", 8447), ("LV1", 8443))]
    normal = ports_by_name(module.build_subscription(nodes))
    prio = ports_by_name(module.build_subscription(nodes, prio=True))
    assert normal.keys() == prio.keys() and len(normal) == 14
    assert {p for n, p in prio.items() if n.endswith("Proton")} == {8450}
    assert {p for n, p in prio.items() if n.endswith("прямой")} == {8449}
    assert {normal["X FR1 | VLESS прямой"], normal["X DE2 | VLESS прямой"]} == {8447, 443}


def test_ipv4_only_drops_ipv6_dual_stack_and_prio_ports(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(module, "_dual_stack_dns_matches", lambda *a: True)
    n = prod_node(ipv6_address="2001:db8::10", dual_stack_host="ds.example.com")
    result = module.build_subscription([n], prio=True, ipv4_only=True)
    assert ports_by_name(result) == {"DE DE4 | VLESS Proton": 8445, "DE DE4 | VLESS прямой": 8443}
    assert "2001:db8::10" not in result and "ds.example.com" not in result


def test_real_dual_stack_domain_in_prio_uses_v6_ports(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(module, "_dual_stack_dns_matches", lambda *a: True)
    result = module.build_subscription([prod_node(ipv6_address="2001:db8::10", dual_stack_host="ds.example.com")], prio=True)
    got = ports_by_name(result)
    assert got["DE DE4 | VLESS Dual-Stack"] == 8449 and got["DE DE4 | VLESS Proton Dual-Stack"] == 8450
    assert got["DE DE4 | VLESS IPv6"] == 8449


def test_node_without_confirmed_ipv6_has_no_v6_variants():
    module = load_builder()
    result = module.build_subscription([prod_node(ipv6_address="2001:db8::10", ipv6_probe_status="offline")])
    assert "IPv6" not in result and result.count("type: vless") == 2


def test_non_ipv4_ipv4_address_does_not_replace_host():
    module = load_builder()  # в production nodes.json у DE1 ipv4_address = 2001:db8::1
    result = module.build_subscription([prod_node(ipv4_address="2001:db8::1")])
    assert 'server: "de4.example.com"' in result and "2001:db8::1" not in result


def test_server_py_calls_the_same_builder():
    """production server.py не должен иметь собственного builder: тесты проверяют ровно тот код, что он вызывает."""
    server = (Path(__file__).parents[2] / "deploy" / "mh-app" / "server.py").read_text(encoding="utf-8")
    assert "from subscription import build_subscription" in server
    assert "build_subscription(nodes, prio=prio)" in server
    assert "VLESS %s" not in server and "⚡IPv6" not in server
