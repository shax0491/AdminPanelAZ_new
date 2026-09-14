"""Tests for app/services/failover_front.py — dnat_front strategy: identity
mirroring (reuses the already-tested HA full-sync primitive) + server-side
DNAT switch decision on a dedicated front node. Deliberately NOT testing
anything DNS/shared-domain-like — this feature doesn't touch that code.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    FailoverPool,
    FailoverPoolMember,
    FailoverPoolStrategy,
    Node,
    NodeStatus,
    VpnType,
)
from app.services import failover_front


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _healthy_awg2_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.get_awg2_monitoring.return_value = {
        "ifaces": [{"name": "antizapret", "peer_count": 1, "up": True}, {"name": "vpn", "peer_count": 1, "up": True}]
    }
    return adapter


@pytest.fixture(autouse=True)
def _default_healthy_node_adapter(monkeypatch):
    """_node_healthy() now also confirms the AWG2 interface is live (not just
    Node.status) — default every test to a healthy node-level adapter so only
    tests specifically about that check need to override it. Individual
    tests that patch ``get_adapter_for_node`` themselves simply overwrite
    this default later in the same test body."""
    monkeypatch.setattr(failover_front, "get_adapter_for_node", lambda node: _healthy_awg2_adapter())


def _make_node(
    db,
    name: str,
    host: str,
    *,
    status: NodeStatus = NodeStatus.online,
    node_kind: str = "vpn",
    is_local: bool = False,
) -> Node:
    node = Node(name=name, host=host, is_local=is_local, status=status, node_kind=node_kind)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _make_pool(db, *, front_node_id=None, front_port=None) -> FailoverPool:
    pool = FailoverPool(
        name="test",
        vpn_type=VpnType.amneziawg2,
        strategy=FailoverPoolStrategy.dnat_front,
        front_node_id=front_node_id,
        front_port=front_port,
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return pool


def test_front_label_is_stable_and_pool_scoped():
    pool = FailoverPool(id=7, name="x")
    assert failover_front.front_label(pool) == "pool7"


def test_resolve_destination_ip_literal_ipv4():
    node = Node(name="x", host="203.0.113.10", is_local=False)
    assert failover_front._resolve_destination_ip(node) == "203.0.113.10"


def test_resolve_destination_ip_local_node_uses_name_not_loopback(monkeypatch):
    # Local node's Node.host is a meaningless "127.0.0.1" placeholder (the
    # panel talks to it in-process) — must resolve via Node.name instead.
    node = Node(name="pl2.example.com", host="127.0.0.1", is_local=True)
    monkeypatch.setattr(
        failover_front.socket, "gethostbyname", lambda h: {"pl2.example.com": "83.172.134.6"}[h]
    )
    assert failover_front._resolve_destination_ip(node) == "83.172.134.6"


def test_resolve_destination_ip_domain_host_gets_resolved(monkeypatch):
    node = Node(name="nl1.example.com", host="nl1.example.com", is_local=False)
    monkeypatch.setattr(
        failover_front.socket, "gethostbyname", lambda h: {"nl1.example.com": "185.193.51.13"}[h]
    )
    assert failover_front._resolve_destination_ip(node) == "185.193.51.13"


def test_resolve_destination_ip_raises_when_unresolvable(monkeypatch):
    import socket as socket_module

    node = Node(name="ghost.invalid", host="ghost.invalid", is_local=False)
    monkeypatch.setattr(
        failover_front.socket,
        "gethostbyname",
        lambda h: (_ for _ in ()).throw(socket_module.gaierror("nope")),
    )
    with pytest.raises(failover_front.FailoverFrontError):
        failover_front._resolve_destination_ip(node)


def test_require_front_raises_for_client_sync_strategy(monkeypatch):
    db = _make_db()
    node = _make_node(db, "front", "9.9.9.9", node_kind="proxy")
    pool = FailoverPool(name="x", strategy="client_sync", front_node_id=node.id, front_port=39001)
    with pytest.raises(failover_front.FailoverFrontError):
        failover_front.require_front(pool)


def test_require_front_raises_without_front_assigned():
    pool = _make_pool(_make_db())
    with pytest.raises(failover_front.FailoverFrontError):
        failover_front.require_front(pool)


def test_mirror_member_identity_calls_sync_and_stamps_timestamp(monkeypatch):
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")
    pool = _make_pool(db)
    primary_member = FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1)
    replica_member = FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2)
    db.add_all([primary_member, replica_member])
    db.commit()
    db.refresh(pool)

    primary_adapter = MagicMock()
    replica_adapter = MagicMock()
    replica_adapter.apply_amneziawg2_runtime.return_value = {"success": True}
    adapters = {primary_node.id: primary_adapter, replica_node.id: replica_adapter}
    monkeypatch.setattr(failover_front, "get_adapter_for_node", lambda node: adapters[node.id])

    sync_mock = MagicMock()
    monkeypatch.setattr(failover_front, "sync_amneziawg2_state_from_primary", sync_mock)

    assert replica_member.identity_mirrored_at is None
    failover_front.mirror_member_identity(db, pool, replica_member)

    sync_mock.assert_called_once_with(primary_adapter, replica_adapter, db=db, replica_node=replica_node)
    db.refresh(replica_member)
    assert replica_member.identity_mirrored_at is not None


def test_mirror_member_identity_fails_loudly_when_runtime_apply_fails(monkeypatch):
    # sync_amneziawg2_state_from_primary itself only logs a warning on a
    # partial runtime-apply failure (shared with HA, which tolerates that) —
    # mirror_member_identity must not inherit that silence: a config file
    # written but not actually applied to the live interface must not be
    # reported as a successful clone (observed for real on LV1: config had
    # the right clients, live interface still had one unrelated stale peer).
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")
    pool = _make_pool(db)
    primary_member = FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1)
    replica_member = FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2)
    db.add_all([primary_member, replica_member])
    db.commit()
    db.refresh(pool)

    primary_adapter = MagicMock()
    replica_adapter = MagicMock()
    replica_adapter.apply_amneziawg2_runtime.return_value = {
        "success": False,
        "errors": [{"interface": "antizapret2", "stderr": "awg syncconf failed"}],
    }
    adapters = {primary_node.id: primary_adapter, replica_node.id: replica_adapter}
    monkeypatch.setattr(failover_front, "get_adapter_for_node", lambda node: adapters[node.id])
    monkeypatch.setattr(failover_front, "sync_amneziawg2_state_from_primary", MagicMock())

    with pytest.raises(failover_front.FailoverFrontError, match="syncconf failed"):
        failover_front.mirror_member_identity(db, pool, replica_member)

    db.refresh(replica_member)
    assert replica_member.identity_mirrored_at is None


def test_mirror_member_identity_rejects_primary_as_its_own_target(monkeypatch):
    db = _make_db()
    node = _make_node(db, "primary", "1.1.1.1")
    pool = _make_pool(db)
    member = FailoverPoolMember(pool_id=pool.id, node_id=node.id, priority=1)
    db.add(member)
    db.commit()
    db.refresh(pool)

    with pytest.raises(failover_front.FailoverFrontError):
        failover_front.mirror_member_identity(db, pool, member)


def test_mirror_member_identity_wraps_sync_failure(monkeypatch):
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")
    pool = _make_pool(db)
    primary_member = FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1)
    replica_member = FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2)
    db.add_all([primary_member, replica_member])
    db.commit()
    db.refresh(pool)

    monkeypatch.setattr(failover_front, "get_adapter_for_node", lambda node: MagicMock())
    monkeypatch.setattr(
        failover_front,
        "sync_amneziawg2_state_from_primary",
        MagicMock(side_effect=RuntimeError("agent unreachable")),
    )

    with pytest.raises(failover_front.FailoverFrontError, match="agent unreachable"):
        failover_front.mirror_member_identity(db, pool, replica_member)


def _pool_with_two_members(db, *, replica_mirrored: bool = True):
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")
    front_node = _make_node(db, "front", "9.9.9.9", node_kind="proxy")
    pool = _make_pool(db, front_node_id=front_node.id, front_port=39001)
    primary_member = FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1)
    from datetime import datetime

    replica_member = FailoverPoolMember(
        pool_id=pool.id,
        node_id=replica_node.id,
        priority=2,
        identity_mirrored_at=datetime.utcnow() if replica_mirrored else None,
    )
    db.add_all([primary_member, replica_member])
    db.commit()
    db.refresh(pool)
    return pool, primary_member, replica_member


def test_evaluate_and_switch_installs_first_rule_when_primary_healthy(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db)

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": None, "installed": False}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is True
    assert result["active_member_id"] == primary_member.id
    adapter.failover_set_destination.assert_called_once_with("pool%d" % pool.id, 39001, "1.1.1.1")
    db.refresh(pool)
    assert pool.active_member_id == primary_member.id
    assert pool.last_switch_error is None


def test_evaluate_and_switch_is_noop_when_already_pointed_correctly(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db)

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": "1.1.1.1", "installed": True}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is False
    assert result["active_member_id"] == primary_member.id
    adapter.failover_set_destination.assert_not_called()


def test_evaluate_and_switch_fails_over_to_mirrored_replica_when_primary_unhealthy(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=True)
    primary_member.node.status = NodeStatus.offline
    db.commit()

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": "1.1.1.1", "installed": True}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is True
    assert result["active_member_id"] == replica_member.id
    adapter.failover_set_destination.assert_called_once_with(f"pool{pool.id}", 39001, "2.2.2.2")


def test_evaluate_and_switch_refuses_unmirrored_replica_even_if_only_healthy_one(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=False)
    primary_member.node.status = NodeStatus.offline
    db.commit()

    adapter = MagicMock()
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is False
    assert result["errors"]
    adapter.failover_set_destination.assert_not_called()


def test_evaluate_and_switch_excludes_node_whose_awg2_interface_is_down(monkeypatch):
    # Node.status can say "online" (node_agent answered) while AmneziaWG 2.0
    # itself is not actually up on that box — found for real on LV1. The
    # primary here is control-plane "online" but its AWG2 interface reports
    # down, so the auto picker must skip it in favor of the mirrored replica.
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=True)

    def get_adapter_for_node(node):
        adapter = MagicMock()
        if node.id == primary_member.node_id:
            adapter.get_awg2_monitoring.return_value = {
                "ifaces": [{"name": "antizapret", "peer_count": 0, "up": False}]
            }
        else:
            adapter.get_awg2_monitoring.return_value = {
                "ifaces": [{"name": "antizapret", "peer_count": 1, "up": True}]
            }
        return adapter

    monkeypatch.setattr(failover_front, "get_adapter_for_node", get_adapter_for_node)

    front_adapter = MagicMock()
    front_adapter.failover_status.return_value = {"destination_ip": "1.1.1.1", "installed": True}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: front_adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is True
    assert result["active_member_id"] == replica_member.id


def test_evaluate_and_switch_treats_monitoring_error_as_unhealthy(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=True)

    def get_adapter_for_node(node):
        adapter = MagicMock()
        if node.id == primary_member.node_id:
            adapter.get_awg2_monitoring.side_effect = RuntimeError("node unreachable")
        else:
            adapter.get_awg2_monitoring.return_value = {
                "ifaces": [{"name": "antizapret", "peer_count": 1, "up": True}]
            }
        return adapter

    monkeypatch.setattr(failover_front, "get_adapter_for_node", get_adapter_for_node)

    front_adapter = MagicMock()
    front_adapter.failover_status.return_value = {"destination_ip": "1.1.1.1", "installed": True}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: front_adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is True
    assert result["active_member_id"] == replica_member.id


def test_force_switch_member_ignores_awg2_liveness_too(monkeypatch):
    # Manual override means manual — it must not silently re-apply the
    # health filter through the back door.
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=True)

    def get_adapter_for_node(node):
        adapter = MagicMock()
        adapter.get_awg2_monitoring.return_value = {
            "ifaces": [{"name": "antizapret", "peer_count": 0, "up": False}]
        }
        return adapter

    monkeypatch.setattr(failover_front, "get_adapter_for_node", get_adapter_for_node)

    front_adapter = MagicMock()
    front_adapter.failover_status.return_value = {"destination_ip": "1.1.1.1", "installed": True}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: front_adapter)

    result = failover_front.force_switch_member(db, pool, replica_member)

    assert result["switched"] is True
    assert result["active_member_id"] == replica_member.id


def test_evaluate_and_switch_records_error_when_front_unreachable(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db)

    adapter = MagicMock()
    adapter.failover_status.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is False
    assert "connection refused" in result["errors"][0]
    db.refresh(pool)
    assert "connection refused" in pool.last_switch_error


def test_evaluate_and_switch_missing_front_is_recorded_not_raised():
    db = _make_db()
    pool = _make_pool(db)  # no front assigned

    result = failover_front.evaluate_and_switch(db, pool)

    assert result["switched"] is False
    assert result["errors"]


def test_force_switch_member_switches_to_unhealthy_but_mirrored_replica(monkeypatch):
    # Primary is healthy (would win the automatic picker) but the admin
    # explicitly wants the replica live anyway — force_switch_member must not
    # apply any health filtering, only the identity-readiness one.
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=True)

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": "1.1.1.1", "installed": True}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.force_switch_member(db, pool, replica_member)

    assert result["switched"] is True
    assert result["active_member_id"] == replica_member.id
    adapter.failover_set_destination.assert_called_once_with(f"pool{pool.id}", 39001, "2.2.2.2")


def test_force_switch_member_refuses_unmirrored_member(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=False)

    adapter = MagicMock()
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    with pytest.raises(failover_front.FailoverFrontError, match="identity"):
        failover_front.force_switch_member(db, pool, replica_member)
    adapter.failover_set_destination.assert_not_called()


def test_force_switch_member_allows_primary_always(monkeypatch):
    db = _make_db()
    pool, primary_member, replica_member = _pool_with_two_members(db, replica_mirrored=False)

    adapter = MagicMock()
    adapter.failover_status.return_value = {"destination_ip": None, "installed": False}
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    result = failover_front.force_switch_member(db, pool, primary_member)

    assert result["switched"] is True
    assert result["active_member_id"] == primary_member.id


def test_force_switch_member_rejects_member_from_other_pool():
    db = _make_db()
    pool, primary_member, _replica_member = _pool_with_two_members(db)
    other_pool = _make_pool(db)
    other_node = _make_node(db, "other", "5.5.5.5")
    other_member = FailoverPoolMember(pool_id=other_pool.id, node_id=other_node.id, priority=1)
    db.add(other_member)
    db.commit()

    with pytest.raises(failover_front.FailoverFrontError, match="не принадлежит"):
        failover_front.force_switch_member(db, pool, other_member)


def test_teardown_front_calls_adapter_teardown(monkeypatch):
    db = _make_db()
    pool, _primary, _replica = _pool_with_two_members(db)

    adapter = MagicMock()
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    failover_front.teardown_front(pool)

    adapter.failover_teardown.assert_called_once_with(f"pool{pool.id}", 39001)


def test_teardown_front_noop_without_front_assigned():
    db = _make_db()
    pool = _make_pool(db)
    # Must not raise even though front_node is None.
    failover_front.teardown_front(pool)


def test_teardown_front_swallows_adapter_errors(monkeypatch):
    db = _make_db()
    pool, _primary, _replica = _pool_with_two_members(db)

    adapter = MagicMock()
    adapter.failover_teardown.side_effect = RuntimeError("front is down")
    monkeypatch.setattr(failover_front, "get_proxy_adapter", lambda node: adapter)

    # Must not raise.
    failover_front.teardown_front(pool)
