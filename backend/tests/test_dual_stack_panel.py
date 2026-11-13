from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Node, NodeStatus


@pytest.fixture(autouse=True)
def isolated_mh_registry(tmp_path, monkeypatch):
    from app.services import dual_stack_panel

    monkeypatch.setattr(dual_stack_panel, "MH_NODES_FILE", tmp_path / "nodes.json")
    monkeypatch.setattr(dual_stack_panel, "MH_REGISTRY_SYNC_TEST_MODE", True, raising=False)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_node(db, *, kind="vpn"):
    node = Node(
        name="DE1" if kind == "vpn" else "RU_DE1",
        host="2001:db8::1" if kind == "vpn" else "203.0.113.10",
        port=9100 if kind == "vpn" else 9101,
        api_key_hash="hash",
        api_key_encrypted="enc",
        is_local=False,
        transport="http",
        node_kind=kind,
        status=NodeStatus.online,
        node_metadata="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


class FakeAdapter:
    def __init__(self):
        self.transport = None
        self.rotations = 0

    def dual_stack_capabilities(self):
        return {
            "has_ipv4": True,
            "has_ipv6": True,
            "ipv4_address": "198.51.100.10",
            "ipv6_address": "2001:db8::1",
            "ipv6_subnet_prefix": "64",
            "ipv6_network": "2001:db8::/64",
            "ipv6_gateway": "2001:db8::ffff",
            "network_interface": "eth0",
            "ipv6_rotation_supported": True,
            "ipv4_probe": {"reachable": True, "latency_ms": 20.1},
            "ipv6_probe": {"reachable": True, "latency_ms": 21.2},
            "audited_at": "2026-01-01T00:00:00Z",
        }

    def set_dual_stack_transport(self, mode):
        self.transport = mode
        return {"transport_mode": mode, "changed": ["xray-az"]}

    def rotate_ipv6(self):
        self.rotations += 1
        return {"ipv6_address": "2001:db8::2", "old_ipv6_address": "2001:db8::1"}


def test_node_response_exposes_dual_stack_health_and_modes(db):
    from app.routers.nodes import _to_response

    node = make_node(db)
    node.has_ipv4 = True
    node.has_ipv6 = True
    node.ipv6_address = "2001:db8::1"
    node.ipv6_subnet_prefix = "64"
    node.ipv4_probe_status = "online"
    node.ipv6_probe_status = "online"
    node.ipv4_latency_ms = 20.1
    node.ipv6_latency_ms = 21.2
    node.transport_mode = "force_ipv6"
    node.egress_mode = "warp"
    node.last_network_audit_at = datetime.utcnow()

    response = _to_response(node)

    assert response.has_ipv6 is True
    assert response.ipv6_subnet_prefix == "64"
    assert response.ipv6_latency_ms == 21.2
    assert response.transport_mode == "force_ipv6"
    assert response.egress_mode == "warp"


def test_audit_persists_real_capabilities_and_both_probes(db, monkeypatch):
    from app.services import dual_stack_panel

    node = make_node(db)
    adapter = FakeAdapter()
    monkeypatch.setattr(dual_stack_panel, "get_adapter_for_node", lambda _node: adapter)

    result = dual_stack_panel.audit_node_network(db, node)
    db.refresh(node)

    assert result["ipv6_address"] == "2001:db8::1"
    assert node.has_ipv4 is True
    assert node.has_ipv6 is True
    assert node.ipv6_gateway == "2001:db8::ffff"
    assert node.ipv6_rotation_supported is True
    assert node.ipv4_probe_status == "online"
    assert node.ipv6_probe_status == "online"
    assert node.ipv4_latency_ms == 20.1
    assert node.ipv6_latency_ms == 21.2
    assert node.last_network_audit_at is not None


def test_transport_change_calls_agent_before_commit(db, monkeypatch):
    from app.services import dual_stack_panel

    node = make_node(db)
    adapter = FakeAdapter()
    monkeypatch.setattr(dual_stack_panel, "get_adapter_for_node", lambda _node: adapter)

    result = dual_stack_panel.set_node_transport_mode(db, node, "force_ipv6")

    assert adapter.transport == "force_ipv6"
    assert result.transport_mode == "force_ipv6"
    assert node.transport_mode == "force_ipv6"


def test_failed_transport_change_does_not_save_mode(db, monkeypatch):
    from app.services import dual_stack_panel

    node = make_node(db)

    class FailedAdapter(FakeAdapter):
        def set_dual_stack_transport(self, mode):
            raise HTTPException(status_code=503, detail="agent unavailable")

    monkeypatch.setattr(dual_stack_panel, "get_adapter_for_node", lambda _node: FailedAdapter())

    with pytest.raises(HTTPException):
        dual_stack_panel.set_node_transport_mode(db, node, "force_ipv4")
    db.refresh(node)
    assert node.transport_mode == "auto"


def test_rotation_updates_node_address_after_agent_success(db, monkeypatch):
    from app.services import dual_stack_panel

    node = make_node(db)
    node.ipv6_rotation_supported = True
    adapter = FakeAdapter()
    monkeypatch.setattr(dual_stack_panel, "get_adapter_for_node", lambda _node: adapter)

    result = dual_stack_panel.rotate_node_ipv6(db, node)

    assert adapter.rotations == 1
    assert result["ipv6_address"] == "2001:db8::2"
    assert node.ipv6_address == "2001:db8::2"
    assert node.ipv6_probe_status == "unknown"


def test_panel_router_registers_dual_stack_management_endpoints():
    from app.routers import nodes as nodes_router

    paths = {route.path for route in nodes_router.router.routes}
    assert "/nodes/{node_id}/dual-stack/audit" in paths
    assert "/nodes/{node_id}/dual-stack/transport" in paths
    assert "/nodes/{node_id}/dual-stack/rotate-ipv6" in paths
    assert "/nodes/{node_id}/egress" in paths


def test_health_worker_refreshes_dual_stack_after_online_health(monkeypatch):
    from app.services import node_health_worker

    calls = []
    monkeypatch.setattr(node_health_worker, "check_node_health", lambda _node: {"status": "online"})
    monkeypatch.setattr(node_health_worker, "update_node_from_health", lambda _node, _health, _db: None)
    monkeypatch.setattr(node_health_worker, "audit_node_network", lambda _db, _node: calls.append("audit"))
    monkeypatch.setattr(node_health_worker, "probe_relay_link", lambda _db, _node: None)

    class FakeDb:
        def __init__(self):
            self.query_node = type("Node", (), {"api_key_pending_encrypted": "", "name": "DE1"})()

        def query(self, _model):
            return self

        def all(self):
            return [self.query_node]

        def close(self):
            pass

    monkeypatch.setattr(node_health_worker, "SessionLocal", FakeDb)
    node_health_worker._poll_all_nodes()
    assert calls == ["audit"]


def test_audit_updates_mh_registry_without_overwriting_other_node_fields(db, monkeypatch, tmp_path):
    import json

    from app.services import dual_stack_panel

    registry = tmp_path / "nodes.json"
    registry.write_text(json.dumps({"nodes": [{"key": "DE1", "uuid": "keep", "host": "198.51.100.10"}]}))
    monkeypatch.setattr(dual_stack_panel, "MH_NODES_FILE", registry)
    node = make_node(db)
    monkeypatch.setattr(dual_stack_panel, "get_adapter_for_node", lambda _node: FakeAdapter())

    dual_stack_panel.audit_node_network(db, node)

    saved = json.loads(registry.read_text())["nodes"][0]
    assert saved["ipv6_address"] == "2001:db8::1"
    assert saved["ipv4_address"] == "198.51.100.10"
    assert saved["ipv4_probe_status"] == "online"
    assert saved["ipv6_probe_status"] == "online"
    assert saved["uuid"] == "keep"


def test_relay_probe_measures_rf_to_exit_for_both_families(db, monkeypatch):
    from app.services import dual_stack_panel

    exit_node = make_node(db)
    exit_node.ipv4_address = "198.51.100.10"
    exit_node.ipv6_address = "2001:db8::1"
    relay = make_node(db, kind="proxy")
    relay.linked_vpn_node_id = exit_node.id

    class RelayAdapter:
        def probe_dual_stack(self, family, target):
            return {
                "reachable": family == 6,
                "latency_ms": 35.5 if family == 6 else None,
                "target": target,
            }

    monkeypatch.setattr(dual_stack_panel, "get_proxy_adapter", lambda _node: RelayAdapter())
    dual_stack_panel.probe_relay_link(db, relay)

    assert relay.ipv4_probe_status == "offline"
    assert relay.ipv6_probe_status == "online"
    assert relay.ipv6_latency_ms == 35.5
    assert relay.last_network_audit_at is not None


def test_proxy_transport_change_applies_real_exit_destination(db, monkeypatch):
    from app.services import dual_stack_panel

    exit_node = make_node(db)
    exit_node.ipv4_address = "198.51.100.10"
    exit_node.ipv6_address = "2001:db8::1"
    relay = make_node(db, kind="proxy")
    relay.linked_vpn_node_id = exit_node.id

    class RelayAdapter:
        def __init__(self):
            self.payload = None

        def set_dual_stack_transport(self, mode, **payload):
            self.payload = {"mode": mode, **payload}
            return {"active_family": "ipv6"}

    adapter = RelayAdapter()
    monkeypatch.setattr(dual_stack_panel, "get_proxy_adapter", lambda _node: adapter)

    result = dual_stack_panel.set_node_transport_mode(db, relay, "auto")

    assert result.transport_mode == "auto"
    assert adapter.payload == {
        "mode": "auto",
        "link_id": relay.id,
        "direct_ipv4": "198.51.100.10",
        "direct_ipv6": "2001:db8::1",
        "tunnel_ipv4": None,
    }


class ProxyCapture:
    def __init__(self):
        self.calls = []
        self.torn = []

    def set_dual_stack_transport(self, mode, **payload):
        self.calls.append((mode, payload))
        return {}

    def teardown_reverse_tunnel(self, link_id):
        self.torn.append(link_id)
        return {}


def _pair(db):
    exit_node = make_node(db)
    exit_node.ipv4_address = "198.51.100.10"
    relay = Node(
        name="RU_DE1", host="203.0.113.50", port=9101, api_key_hash="h", api_key_encrypted="e", is_local=False,
        transport="http", node_kind="proxy", status=NodeStatus.online, node_metadata="{}",
        linked_vpn_node_id=exit_node.id, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(relay)
    db.commit()
    db.refresh(relay)
    return exit_node, relay


def test_proxy_transport_uses_relay_node_id_as_link_id(db, monkeypatch):
    from app.services import dual_stack_panel

    exit_node, relay = _pair(db)
    proxy = ProxyCapture()
    monkeypatch.setattr(dual_stack_panel, "get_proxy_adapter", lambda _node: proxy)

    dual_stack_panel.set_node_transport_mode(db, relay, "force_ipv4")

    mode, payload = proxy.calls[0]
    assert payload["link_id"] == relay.id  # тот же ключ, что reverse_panel передаёт агенту (relay.id)
    assert payload["direct_ipv4"] == "198.51.100.10"


def test_proxy_transport_rejects_non_ip_exit_before_calling_agent(db, monkeypatch):
    from app.services import dual_stack_panel

    exit_node, relay = _pair(db)
    exit_node.ipv4_address = None
    exit_node.host = "vpn.invalid"
    db.commit()
    proxy = ProxyCapture()
    monkeypatch.setattr(dual_stack_panel, "get_proxy_adapter", lambda _node: proxy)

    def nxdomain(*args, **kwargs):
        raise OSError("nxdomain")

    monkeypatch.setattr("socket.getaddrinfo", nxdomain)

    with pytest.raises(HTTPException) as caught:
        dual_stack_panel.set_node_transport_mode(db, relay, "force_ipv4")

    assert caught.value.status_code == 409
    assert proxy.calls == []


def test_teardown_reverse_links_reaches_both_agents_and_collects_failures(db, monkeypatch):
    from app.models import ReverseTunnelLink
    from app.services import reverse_panel

    exit_node, relay = _pair(db)
    db.add(ReverseTunnelLink(proxy_node_id=relay.id, exit_node_id=exit_node.id, listen_port=52002, tunnel_ipv4="10.77.0.4", tunnel_ipv6="fd77::2:2"))
    db.commit()
    proxy, vpn = ProxyCapture(), ProxyCapture()
    monkeypatch.setattr(reverse_panel, "get_proxy_adapter", lambda _node: proxy)
    monkeypatch.setattr(reverse_panel, "get_adapter_for_node", lambda _node: vpn)

    assert reverse_panel.teardown_reverse_links(db, relay.id) == []
    assert proxy.torn == [relay.id] and vpn.torn == [relay.id]

    def boom(_link_id):
        raise RuntimeError("agent down")

    vpn.teardown_reverse_tunnel = boom
    problems = reverse_panel.teardown_reverse_links(db, exit_node.id)
    assert len(problems) == 1 and "agent down" in problems[0]


def test_node_without_audit_reports_no_telemetry_instead_of_defaults(db):
    from app.routers.nodes import _to_response

    response = _to_response(make_node(db))

    assert response.dual_stack_telemetry is False
    assert response.has_ipv4 is None and response.has_ipv6 is None
    assert response.ipv6_rotation_supported is None


def test_egress_selector_writes_ipv6_egress_and_keeps_warp_tunnels(db, monkeypatch):
    """Селектор карточки = IPV6_EGRESS; режимы Proton/WARP туннелей (ANTIZAPRET_WARP/VPN_WARP) он не трогает."""
    from app.services import dual_stack_panel

    calls = []

    class Adapter:
        def update_antizapret_settings(self, updates):
            calls.append(("settings", updates))

        def restart_service(self, name):
            calls.append(("restart", name))

        def set_warp_modes(self, **kwargs):  # pragma: no cover - не должен вызываться
            calls.append(("warp_modes", kwargs))

        def set_warp_provider(self, provider):  # pragma: no cover - не должен вызываться
            calls.append(("warp_provider", provider))

    monkeypatch.setattr(dual_stack_panel, "_adapter", lambda node: Adapter())
    node = make_node(db)

    dual_stack_panel.set_node_egress_mode(db, node, "native")

    assert calls == [("settings", {"IPV6_EGRESS": "native"}), ("restart", "antizapret")]
    assert node.egress_mode == "native"

def test_mh_test_mode_refuses_production_registry_before_any_file_access(db, monkeypatch):
    from pathlib import Path
    from app.services import dual_stack_panel

    monkeypatch.setattr(dual_stack_panel, "MH_NODES_FILE", Path("/opt/mh-app/nodes.json"))
    monkeypatch.setattr(dual_stack_panel, "MH_REGISTRY_SYNC_TEST_MODE", True, raising=False)
    monkeypatch.setattr(Path, "is_file", lambda _self: False)

    with pytest.raises(ValueError, match="тестов"):
        dual_stack_panel._sync_mh_registry(make_node(db))


@pytest.mark.parametrize("field", ["ipv4_address", "ipv6_address"])
def test_mh_production_sync_rejects_documentation_address(db, monkeypatch, tmp_path, field):
    import json
    from app.services import dual_stack_panel

    registry = tmp_path / "nodes.json"
    registry.write_text(json.dumps({"nodes": [{"key": "DE1", "host": "198.51.100.10"}]}))
    monkeypatch.setattr(dual_stack_panel, "MH_NODES_FILE", registry)
    monkeypatch.setattr(dual_stack_panel, "MH_REGISTRY_SYNC_TEST_MODE", False, raising=False)
    node = make_node(db)
    setattr(node, field, "2001:db8::1")
    if field == "ipv6_address":
        node.has_ipv6 = True

    with pytest.raises(ValueError, match="тестов|documentation|IPv4"):
        dual_stack_panel._sync_mh_registry(node)

    assert "ipv4_address" not in json.loads(registry.read_text())["nodes"][0]
