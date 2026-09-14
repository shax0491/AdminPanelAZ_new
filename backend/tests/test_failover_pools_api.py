"""API-level tests for app/routers/failover_pools.py — calling the endpoint
functions directly (same pattern as test_awg2_api.py / test_antizapret_multi_protocol_client.py),
against a real in-memory sqlite DB rather than mocks, since these endpoints do
several real queries/joins.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import pytest
from fastapi import HTTPException

from app.database import Base
from app.models import Node, NodeStatus, User, UserRole, VpnConfig, VpnType
from app.routers import failover_pools as router
from app.schemas import (
    FailoverClientLinkCreate,
    FailoverPoolCreate,
    FailoverPoolFrontUpdate,
    FailoverPoolMemberCreate,
    FailoverStatusCheckIn,
)


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def db():
    session = _make_db()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def admin(db):
    user = User(username="admin", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class _FakeAdapter:
    def __init__(self, conf: str):
        self.conf = conf
        self.applied = False

    def read_amneziawg2_server_config(self, iface: str) -> str:
        return self.conf

    def write_amneziawg2_server_config(self, iface: str, content: str) -> None:
        self.conf = content

    def apply_amneziawg2_runtime(self) -> dict:
        self.applied = True
        return {"success": True}

    def read_amneziawg2_server_key(self) -> str:
        return "PRIVATE_KEY=p==\nPUBLIC_KEY=pub=="


PRIMARY_CONF = """[Interface]
PrivateKey = server-priv==
Address = 10.28.9.1/24
ListenPort = 53443
Jc = 5
Jmin = 20
Jmax = 50

# Client = alice
# PrivateKey = alice-priv==
[Peer]
PublicKey = alice-pub==
PresharedKey = alice-psk==
AllowedIPs = 10.28.9.2/32
"""

REPLICA_CONF = """[Interface]
PrivateKey = other-priv==
Address = 10.28.9.1/24
ListenPort = 53443
Jc = 5
Jmin = 20
Jmax = 50
"""


def test_create_pool_and_add_members(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="Мой пул"), db=db, _=admin)
    assert pool.name == "Мой пул"
    assert pool.mode == "auto"
    assert pool.members == []

    node1 = Node(name="node1", host="1.1.1.1", is_local=False)
    node2 = Node(name="node2", host="2.2.2.2", is_local=False)
    db.add_all([node1, node2])
    db.commit()

    updated = router.add_member(pool.id, FailoverPoolMemberCreate(node_id=node1.id, priority=1), db=db, _=admin)
    assert len(updated.members) == 1
    updated = router.add_member(pool.id, FailoverPoolMemberCreate(node_id=node2.id, priority=2), db=db, _=admin)
    assert len(updated.members) == 2
    assert [m.priority for m in updated.members] == [1, 2]


def test_add_duplicate_member_rejected(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P"), db=db, _=admin)
    node = Node(name="node1", host="1.1.1.1", is_local=False)
    db.add(node)
    db.commit()
    router.add_member(pool.id, FailoverPoolMemberCreate(node_id=node.id), db=db, _=admin)
    with pytest.raises(HTTPException) as exc:
        router.add_member(pool.id, FailoverPoolMemberCreate(node_id=node.id), db=db, _=admin)
    assert exc.value.status_code == 400


def test_link_client_requires_existing_amneziawg2_config(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P"), db=db, _=admin)
    with pytest.raises(HTTPException) as exc:
        router.link_client(pool.id, FailoverClientLinkCreate(client_name="ghost"), db=db, _=admin)
    assert exc.value.status_code == 400


def test_link_client_syncs_to_other_members_and_device_can_fetch_config(db, admin, monkeypatch):
    pool = router.create_pool(FailoverPoolCreate(name="P"), db=db, _=admin)
    primary_node = Node(name="primary", host="1.1.1.1", is_local=False)
    replica_node = Node(name="replica", host="2.2.2.2", is_local=False)
    db.add_all([primary_node, replica_node])
    db.commit()
    router.add_member(pool.id, FailoverPoolMemberCreate(node_id=primary_node.id, priority=1), db=db, _=admin)
    router.add_member(
        pool.id,
        FailoverPoolMemberCreate(node_id=replica_node.id, priority=2, label="replica.example.com"),
        db=db,
        _=admin,
    )

    config = VpnConfig(
        node_id=primary_node.id, client_name="alice", vpn_type=VpnType.amneziawg2, owner_id=admin.id
    )
    db.add(config)
    db.commit()

    primary_adapter = _FakeAdapter(PRIMARY_CONF)
    replica_adapter = _FakeAdapter(REPLICA_CONF)

    def fake_get_adapter(node):
        return primary_adapter if node.id == primary_node.id else replica_adapter

    monkeypatch.setattr("app.services.failover_pool.get_adapter_for_node", fake_get_adapter)

    link = router.link_client(pool.id, FailoverClientLinkCreate(client_name="alice"), db=db, _=admin)
    assert link.client_name == "alice"
    assert link.last_sync_error is None
    assert replica_adapter.applied is True
    assert "# Client = alice" in replica_adapter.conf

    # Device-facing: fetch config by token, no admin session
    device_cfg = router.device_get_config(link.access_token, db=db)
    assert device_cfg.client_name == "alice"
    assert len(device_cfg.servers) == 2
    names = {s.name for s in device_cfg.servers}
    assert "primary" in names
    assert "replica.example.com" in names
    for entry in device_cfg.servers:
        assert "PrivateKey = alice-priv==" in entry.conf

    # Device status check-in
    msg = router.device_post_status(
        link.access_token,
        FailoverStatusCheckIn(device_label="Pixel 8", active_node_name="replica", healthy=True, detail="OK"),
        db=db,
    )
    assert msg.message == "ok"

    statuses = router.client_status(pool.id, "alice", db=db, _=admin)
    assert len(statuses) == 1
    assert statuses[0].device_label == "Pixel 8"
    assert statuses[0].active_node_name == "replica"


def test_device_get_config_rejects_unknown_token(db):
    with pytest.raises(HTTPException) as exc:
        router.device_get_config("not-a-real-token", db=db)
    assert exc.value.status_code == 404


def test_delete_pool_does_not_touch_nodes_or_configs(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P"), db=db, _=admin)
    node = Node(name="node1", host="1.1.1.1", is_local=False)
    db.add(node)
    db.commit()
    router.add_member(pool.id, FailoverPoolMemberCreate(node_id=node.id), db=db, _=admin)

    router.delete_pool(pool.id, db=db, _=admin)

    # Pool gone...
    with pytest.raises(HTTPException):
        router.get_pool(pool.id, db=db, _=admin)
    # ...but the node itself is untouched.
    assert db.query(Node).filter(Node.id == node.id).first() is not None


# --- dnat_front strategy: front assignment / identity mirror / switch-check ---


def test_set_front_requires_proxy_kind_node(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    vpn_node = Node(name="vpn-node", host="1.1.1.1", is_local=False, node_kind="vpn")
    db.add(vpn_node)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        router.set_front(pool.id, FailoverPoolFrontUpdate(front_node_id=vpn_node.id, front_port=39001), db=db, _=admin)
    assert exc.value.status_code == 400


def test_set_front_accepts_proxy_kind_node(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    front_node = Node(name="front", host="9.9.9.9", is_local=False, node_kind="proxy")
    db.add(front_node)
    db.commit()

    updated = router.set_front(
        pool.id, FailoverPoolFrontUpdate(front_node_id=front_node.id, front_port=39001), db=db, _=admin
    )
    assert updated.front_node_id == front_node.id
    assert updated.front_port == 39001


def test_mirror_identity_endpoint_stamps_member(db, admin, monkeypatch):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    primary_node = Node(name="primary", host="1.1.1.1", is_local=False)
    replica_node = Node(name="replica", host="2.2.2.2", is_local=False)
    db.add_all([primary_node, replica_node])
    db.commit()
    router.add_member(pool.id, FailoverPoolMemberCreate(node_id=primary_node.id, priority=1), db=db, _=admin)
    updated = router.add_member(
        pool.id, FailoverPoolMemberCreate(node_id=replica_node.id, priority=2), db=db, _=admin
    )
    replica_member_id = [m.id for m in updated.members if m.node_id == replica_node.id][0]

    monkeypatch.setattr("app.services.failover_front.get_adapter_for_node", lambda node: MagicMock())
    monkeypatch.setattr("app.services.failover_front.sync_amneziawg2_state_from_primary", MagicMock())

    result = router.mirror_identity(pool.id, replica_member_id, db=db, _=admin)
    mirrored = [m for m in result.members if m.id == replica_member_id][0]
    assert mirrored.identity_mirrored_at is not None


def test_mirror_identity_unknown_member_404(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    with pytest.raises(HTTPException) as exc:
        router.mirror_identity(pool.id, 999, db=db, _=admin)
    assert exc.value.status_code == 404


def test_switch_check_endpoint_switches_to_primary(db, admin, monkeypatch):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    primary_node = Node(name="primary", host="1.1.1.1", is_local=False, status=NodeStatus.online)
    front_node = Node(name="front", host="9.9.9.9", is_local=False, node_kind="proxy")
    db.add_all([primary_node, front_node])
    db.commit()
    router.add_member(pool.id, FailoverPoolMemberCreate(node_id=primary_node.id, priority=1), db=db, _=admin)
    router.set_front(pool.id, FailoverPoolFrontUpdate(front_node_id=front_node.id, front_port=39001), db=db, _=admin)

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": None, "installed": False}
    monkeypatch.setattr("app.services.failover_front.get_proxy_adapter", lambda node: adapter)
    node_adapter = MagicMock()
    node_adapter.get_awg2_monitoring.return_value = {"ifaces": [{"name": "antizapret", "peer_count": 1, "up": True}]}
    monkeypatch.setattr("app.services.failover_front.get_adapter_for_node", lambda node: node_adapter)

    result = router.switch_check(pool.id, db=db, _=admin)
    assert result.switched is True
    adapter.failover_set_destination.assert_called_once()


def test_force_switch_endpoint_overrides_health(db, admin, monkeypatch):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    primary_node = Node(name="primary", host="1.1.1.1", is_local=False, status=NodeStatus.online)
    replica_node = Node(name="replica", host="2.2.2.2", is_local=False, status=NodeStatus.offline)
    front_node = Node(name="front", host="9.9.9.9", is_local=False, node_kind="proxy")
    db.add_all([primary_node, replica_node, front_node])
    db.commit()
    router.add_member(pool.id, FailoverPoolMemberCreate(node_id=primary_node.id, priority=1), db=db, _=admin)
    updated = router.add_member(
        pool.id, FailoverPoolMemberCreate(node_id=replica_node.id, priority=2), db=db, _=admin
    )
    replica_member_id = [m.id for m in updated.members if m.node_id == replica_node.id][0]
    router.set_front(pool.id, FailoverPoolFrontUpdate(front_node_id=front_node.id, front_port=39001), db=db, _=admin)

    monkeypatch.setattr("app.services.failover_front.get_adapter_for_node", lambda node: MagicMock())
    monkeypatch.setattr("app.services.failover_front.sync_amneziawg2_state_from_primary", MagicMock())
    router.mirror_identity(pool.id, replica_member_id, db=db, _=admin)

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": None, "installed": False}
    monkeypatch.setattr("app.services.failover_front.get_proxy_adapter", lambda node: adapter)

    # replica is offline — evaluate_and_switch would never pick it.
    result = router.force_switch(pool.id, replica_member_id, db=db, _=admin)
    assert result.switched is True
    assert result.active_member_id == replica_member_id
    adapter.failover_set_destination.assert_called_once_with(f"pool{pool.id}", 39001, "2.2.2.2")


def test_force_switch_endpoint_unknown_member_404(db, admin):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    with pytest.raises(HTTPException) as exc:
        router.force_switch(pool.id, 999, db=db, _=admin)
    assert exc.value.status_code == 404


def test_delete_dnat_front_pool_tears_down_front_rule(db, admin, monkeypatch):
    pool = router.create_pool(FailoverPoolCreate(name="P", strategy="dnat_front"), db=db, _=admin)
    front_node = Node(name="front", host="9.9.9.9", is_local=False, node_kind="proxy")
    db.add(front_node)
    db.commit()
    router.set_front(pool.id, FailoverPoolFrontUpdate(front_node_id=front_node.id, front_port=39001), db=db, _=admin)

    adapter = MagicMock()
    monkeypatch.setattr("app.services.failover_front.get_proxy_adapter", lambda node: adapter)

    router.delete_pool(pool.id, db=db, _=admin)
    adapter.failover_teardown.assert_called_once_with(f"pool{pool.id}", 39001)
