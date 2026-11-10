"""The owner deadline inherited by a new profile must reach HA replicas.

Before 2.26.6 the fork replicated the create inline and then pushed ``access_until`` itself
(``maybe_replicate_create`` + ``replicate_inherited_access_until``). Upstream queues the create and the
worker copies the whole client policy (block / limit / access_until) onto the fresh shadow row in
``replicate._handle_client_create``. The old tests mocked the removed inline call chain, so they are
replaced by one that pins the ordering guarantee that matters: the policy is copied to each replica only
after that replica's shadow ``VpnConfig`` exists.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Node, NodeStatus, NodeSyncGroup, SyncStatus, User, UserRole, VpnConfig, VpnType
from app.services.node_sync import replicate


def _node(db, name: str, host: str, *, local: bool = False) -> Node:
    node = Node(
        name=name,
        host=host,
        port=9100,
        api_key_hash="",
        api_key_encrypted="",
        status=NodeStatus.online,
        is_local=local,
        node_metadata="{}",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def test_policy_is_copied_to_each_replica_only_after_its_shadow_config_exists(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        owner = User(username="owner", password_hash="x", role=UserRole.user)
        db.add(owner)
        db.commit()
        primary = _node(db, "primary", "127.0.0.1", local=True)
        replica = _node(db, "replica", "127.0.0.2")
        group = NodeSyncGroup(
            name="g",
            shared_domain="vpn.example",
            primary_node_id=primary.id,
            replica_node_ids=json.dumps([replica.id]),
            sync_mode="auto",
            sync_status=SyncStatus.synced,
        )
        db.add(group)
        db.commit()
        db.refresh(group)
        config = VpnConfig(
            node_id=primary.id, client_name="ivan", vpn_type=VpnType.wireguard, owner_id=owner.id, cert_expire_days=3650
        )
        db.add(config)
        db.commit()
        db.refresh(config)

        copies: list[dict] = []

        def fake_copy(session, src_node, dst_node, client_name, *, vpn_type):
            shadow = (
                session.query(VpnConfig)
                .filter(VpnConfig.node_id == dst_node.id, VpnConfig.client_name == client_name)
                .first()
            )
            copies.append(
                {
                    "src": src_node.id,
                    "dst": dst_node.id,
                    "client": client_name,
                    "vpn_type": vpn_type,
                    "shadow_exists": shadow is not None,
                }
            )

        monkeypatch.setattr(replicate, "_primary_adapter", lambda *_a, **_k: MagicMock())
        monkeypatch.setattr(replicate, "get_adapter_for_node", lambda _n: MagicMock())
        monkeypatch.setattr(replicate, "sync_vpn_crypto_from_primary", lambda *_a, **_k: None)
        monkeypatch.setattr(replicate, "copy_single_client_policy", fake_copy)

        result = replicate._handle_client_create(db, group, {"primary_config": config})

        assert result.errors == []
        assert copies == [
            {
                "src": primary.id,
                "dst": replica.id,
                "client": "ivan",
                "vpn_type": VpnType.wireguard,
                "shadow_exists": True,
            }
        ]
    finally:
        db.close()
        engine.dispose()
