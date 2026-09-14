"""API-level tests for app/routers/failover_pools.py — calling the endpoint
functions directly (same pattern as test_awg2_api.py / test_antizapret_multi_protocol_client.py),
against a real in-memory sqlite DB rather than mocks, since these endpoints do
several real queries/joins.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import pytest
from fastapi import HTTPException

from app.database import Base
from app.models import Node, User, UserRole, VpnConfig, VpnType
from app.routers import failover_pools as router
from app.schemas import (
    FailoverClientLinkCreate,
    FailoverPoolCreate,
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
