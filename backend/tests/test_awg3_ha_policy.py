"""AmneziaWG 3.0 access policy is copied and replicated to HA replicas like AmneziaWG 2.0."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AmneziaWg3AccessPolicy, Node, VpnType
from app.services import awg3_access
from app.services.node_sync import policy_sync
from app.services.policy_import import copy_single_client_policy


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _node(db, name: str) -> Node:
    node = Node(name=name, host=f"{name}.example")
    db.add(node)
    db.flush()
    return node


def test_copy_single_client_policy_copies_awg3_row(db):
    src = _node(db, "primary")
    dst = _node(db, "replica")
    expires = datetime.utcnow() + timedelta(days=5)
    db.add(AmneziaWg3AccessPolicy(node_id=src.id, client_name="ivan", access_until=expires, is_permanent_blocked=True,
                                  block_reason="manual_permanent"))
    db.flush()

    copied = copy_single_client_policy(db, src, dst, "ivan", vpn_type=VpnType.amneziawg3)

    assert copied == 1
    row = db.query(AmneziaWg3AccessPolicy).filter_by(node_id=dst.id, client_name="ivan").one()
    assert row.is_permanent_blocked is True and row.block_reason == "manual_permanent"
    assert row.access_until == expires


def test_apply_policy_op_routes_awg3_block_ops_to_awg3_access(db, monkeypatch):
    node = _node(db, "replica")
    calls = []
    monkeypatch.setattr(awg3_access, "set_temp_block", lambda db_, n, name, days, actor: calls.append(("temp", name, days, actor)) or {"ok": 1})
    monkeypatch.setattr(awg3_access, "set_permanent_block", lambda db_, n, name, actor: calls.append(("perm", name, actor)) or {"ok": 1})
    monkeypatch.setattr(awg3_access, "unblock", lambda db_, n, name, actor: calls.append(("unblock", name, actor)) or {"ok": 1})
    svc = SimpleNamespace(db=db, node_id=node.id)
    cfg = SimpleNamespace(client_name="ivan", vpn_type=VpnType.amneziawg3)

    policy_sync._apply_policy_op(svc, cfg, "block_temp", days=3, actor="admin")
    policy_sync._apply_policy_op(svc, cfg, "block_permanent", actor="admin")
    policy_sync._apply_policy_op(svc, cfg, "unblock", actor="admin")

    assert calls == [("temp", "ivan", 3, "admin"), ("perm", "ivan", "admin"), ("unblock", "ivan", "admin")]


def test_apply_policy_op_rejects_unknown_awg3_op(db):
    node = _node(db, "replica")
    svc = SimpleNamespace(db=db, node_id=node.id)
    cfg = SimpleNamespace(client_name="ivan", vpn_type=VpnType.amneziawg3)
    with pytest.raises(ValueError):
        policy_sync._apply_policy_op(svc, cfg, "set_traffic_limit", actor="admin")
