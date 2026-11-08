from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Node, NodeStatus, ReverseTunnelLink
from app.services.crypto import decrypt_secret
from app.config import get_settings


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


def node(db, name, kind, host, linked=None):
    item = Node(
        name=name,
        host=host,
        port=9100 if kind == "vpn" else 9101,
        api_key_hash="hash",
        api_key_encrypted="enc",
        is_local=False,
        transport="http",
        node_kind=kind,
        linked_vpn_node_id=linked,
        status=NodeStatus.online,
        node_metadata="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class ExitAdapter:
    def __init__(self):
        self.payload = None

    def configure_reverse_exit(self, payload):
        self.payload = payload
        return {"active": True, "interface": "azr5"}

    def reverse_tunnel_status(self, link_id):
        return {"connected": True, "interface": f"azr{link_id}"}


class RelayAdapter:
    def __init__(self):
        self.relay_payload = None
        self.watchdog_payload = None
        self.destination = None

    def configure_reverse_relay(self, payload):
        self.relay_payload = payload
        return {"active": True, "interface": "azr5"}

    def configure_reverse_watchdog(self, payload):
        self.watchdog_payload = payload
        return {"enabled": True, "reverse_active": False}

    def set_destination(self, destination):
        self.destination = destination
        return {"destination_ip": destination}

    def reverse_watchdog_status(self, link_id):
        return {"reverse_active": True, "link_id": link_id}


def linked_nodes(db):
    exit_node = node(db, "DE1", "vpn", "203.0.113.10")
    exit_node.ipv4_address = "203.0.113.10"
    exit_node.ipv6_address = "2001:db8::10"
    relay = node(db, "RU_DE1", "proxy", "198.51.100.10", exit_node.id)
    return relay, exit_node


def test_provision_configures_both_sides_and_encrypts_private_keys(db, monkeypatch):
    from app.services import reverse_panel

    relay, exit_node = linked_nodes(db)
    exit_adapter = ExitAdapter()
    relay_adapter = RelayAdapter()
    monkeypatch.setattr(reverse_panel, "get_adapter_for_node", lambda _node: exit_adapter)
    monkeypatch.setattr(reverse_panel, "get_proxy_adapter", lambda _node: relay_adapter)

    link = reverse_panel.provision_reverse_tunnel(db, relay)

    assert link.proxy_node_id == relay.id
    assert link.exit_node_id == exit_node.id
    assert link.state == "standby"
    assert exit_adapter.payload["relay_host"] == relay.host
    assert relay_adapter.relay_payload["peer_public_key"] == link.exit_public_key
    assert exit_adapter.payload["peer_public_key"] == link.relay_public_key
    assert relay_adapter.watchdog_payload["direct_ipv6"] == "2001:db8::10"
    assert relay.reverse_state == "standby"
    assert "PRIVATE" not in link.relay_private_key_encrypted
    assert decrypt_secret(link.relay_private_key_encrypted, get_settings().secret_key)
    assert decrypt_secret(link.exit_private_key_encrypted, get_settings().secret_key)


def test_activate_reverse_switches_relay_to_tunnel_address(db, monkeypatch):
    from app.services import reverse_panel

    relay, _exit_node = linked_nodes(db)
    exit_adapter = ExitAdapter()
    relay_adapter = RelayAdapter()
    monkeypatch.setattr(reverse_panel, "get_adapter_for_node", lambda _node: exit_adapter)
    monkeypatch.setattr(reverse_panel, "get_proxy_adapter", lambda _node: relay_adapter)
    reverse_panel.provision_reverse_tunnel(db, relay)

    link = reverse_panel.activate_reverse_tunnel(db, relay)

    assert relay_adapter.destination == link.tunnel_ipv4
    assert link.state == "active"
    assert relay.reverse_state == "active"
    assert relay.transport_mode == "reverse"


def test_reverse_response_never_serializes_private_keys(db):
    from app.schemas import ReverseTunnelResponse

    link = ReverseTunnelLink(
        id=1,
        proxy_node_id=5,
        exit_node_id=1,
        listen_port=52005,
        tunnel_ipv4="10.77.5.2",
        tunnel_ipv6="fd77::5:2",
        relay_public_key="relay-public",
        exit_public_key="exit-public",
        relay_private_key_encrypted="encrypted-relay-private",
        exit_private_key_encrypted="encrypted-exit-private",
        state="standby",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    dumped = ReverseTunnelResponse.model_validate(link).model_dump()
    assert "relay_private_key_encrypted" not in dumped
    assert "exit_private_key_encrypted" not in dumped
    assert "encrypted" not in str(dumped)


def test_reverse_tunnel_panel_routes_are_registered():
    from app.routers import nodes as nodes_router

    paths = {route.path for route in nodes_router.router.routes}
    assert "/nodes/{node_id}/reverse-tunnel/provision" in paths
    assert "/nodes/{node_id}/reverse-tunnel/activate" in paths
    assert "/nodes/{node_id}/reverse-tunnel/status" in paths
