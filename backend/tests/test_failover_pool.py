"""Tests for app/services/failover_pool.py — peer-sync + device-config generation
for the failover pool feature. Deliberately NOT testing anything HA-Sync-Group-like;
this feature doesn't touch that code at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    FailoverClientLink,
    FailoverPool,
    FailoverPoolMember,
    FailoverPoolMode,
    Node,
    VpnType,
)
from app.services.failover_pool import (
    FailoverPoolError,
    _allocate_ip,
    _append_peer_block,
    _extract_peer_spec,
    _peer_exists,
    build_member_client_conf,
    sync_client_peer_to_pool,
)
from app.services.failover_pool import PeerSpec


ANTIZAPRET2_CONF_WITH_PEER = """[Interface]
PrivateKey = server-priv==
Address = 10.28.9.1/24
ListenPort = 53443
Jc = 5
Jmin = 20
Jmax = 50
S1 = 88
S2 = 121
S3 = 41
S4 = 8
H1 = 1
H2 = 2
H3 = 3
H4 = 4

# Client = alice
# PrivateKey = alice-priv==
[Peer]
PublicKey = alice-pub==
PresharedKey = alice-psk==
AllowedIPs = 10.28.9.2/32
"""

EMPTY_SERVER_CONF = """[Interface]
PrivateKey = other-priv==
Address = 10.28.9.1/24
ListenPort = 53443
Jc = 5
Jmin = 20
Jmax = 50
"""


def test_extract_peer_spec_reads_keys_and_private_key_comment():
    spec = _extract_peer_spec(ANTIZAPRET2_CONF_WITH_PEER, "alice")
    assert spec is not None
    assert spec.public_key == "alice-pub=="
    assert spec.preshared_key == "alice-psk=="
    assert spec.private_key == "alice-priv=="


def test_extract_peer_spec_missing_client_returns_none():
    assert _extract_peer_spec(ANTIZAPRET2_CONF_WITH_PEER, "bob") is None


def test_peer_exists():
    assert _peer_exists(ANTIZAPRET2_CONF_WITH_PEER, "alice") is True
    assert _peer_exists(ANTIZAPRET2_CONF_WITH_PEER, "bob") is False


def test_allocate_ip_skips_used_addresses():
    conf = EMPTY_SERVER_CONF + "\nAllowedIPs = 10.28.9.2/32\nAllowedIPs = 10.28.9.3/32\n"
    ip = _allocate_ip(conf)
    assert ip == "10.28.9.4"


def test_allocate_ip_raises_without_address_line():
    with pytest.raises(FailoverPoolError):
        _allocate_ip("[Interface]\nPrivateKey = x\n")


def test_append_peer_block_includes_private_key_comment_when_present():
    spec = PeerSpec(client_name="bob", public_key="pub==", preshared_key="psk==", private_key="priv==")
    result = _append_peer_block(EMPTY_SERVER_CONF, spec, "10.28.9.7")
    assert "# Client = bob" in result
    assert "# PrivateKey = priv==" in result
    assert "PublicKey = pub==" in result
    assert "AllowedIPs = 10.28.9.7/32" in result
    # Original content untouched, new block appended after it
    assert result.startswith(EMPTY_SERVER_CONF.rstrip("\n"))


def test_append_peer_block_omits_private_key_comment_when_absent():
    spec = PeerSpec(client_name="bob", public_key="pub==", preshared_key="psk==", private_key=None)
    result = _append_peer_block(EMPTY_SERVER_CONF, spec, "10.28.9.7")
    assert "# PrivateKey" not in result


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_node(db, name: str, host: str) -> Node:
    node = Node(name=name, host=host, is_local=False)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


class _FakeAdapter:
    """Stands in for a NodeAdapter — in-memory dict instead of real node_agent calls."""

    def __init__(self, conf: str, server_key: str = "PRIVATE_KEY=srv-priv==\nPUBLIC_KEY=srv-pub=="):
        self.conf = conf
        self.server_key = server_key
        self.applied = False

    def read_amneziawg2_server_config(self, iface: str) -> str:
        return self.conf

    def write_amneziawg2_server_config(self, iface: str, content: str) -> None:
        self.conf = content

    def apply_amneziawg2_runtime(self) -> dict:
        self.applied = True
        return {"success": True}

    def read_amneziawg2_server_key(self) -> str:
        return self.server_key


def test_sync_client_peer_to_pool_appends_missing_peer_and_applies_runtime(monkeypatch):
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")

    pool = FailoverPool(name="test", vpn_type=VpnType.amneziawg2, mode=FailoverPoolMode.auto)
    db.add(pool)
    db.commit()
    db.refresh(pool)

    db.add(FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1))
    db.add(FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2))
    db.add(
        FailoverClientLink(
            pool_id=pool.id, client_name="alice", primary_node_id=primary_node.id, access_token="tok123"
        )
    )
    db.commit()

    primary_adapter = _FakeAdapter(ANTIZAPRET2_CONF_WITH_PEER)
    replica_adapter = _FakeAdapter(EMPTY_SERVER_CONF)

    def fake_get_adapter(node):
        return primary_adapter if node.id == primary_node.id else replica_adapter

    monkeypatch.setattr("app.services.failover_pool.get_adapter_for_node", fake_get_adapter)

    result = sync_client_peer_to_pool(db, pool, "alice")

    assert result["synced_nodes"] == ["replica"]
    assert result["errors"] == []
    assert "# Client = alice" in replica_adapter.conf
    assert "AllowedIPs = 10.28.9.2/32" in replica_adapter.conf  # first free IP on replica's own subnet
    assert replica_adapter.applied is True

    link = db.query(FailoverClientLink).filter(FailoverClientLink.client_name == "alice").first()
    assert link.last_synced_at is not None
    assert link.last_sync_error is None


def test_sync_client_peer_to_pool_skips_already_synced_member(monkeypatch):
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")

    pool = FailoverPool(name="test", vpn_type=VpnType.amneziawg2, mode=FailoverPoolMode.auto)
    db.add(pool)
    db.commit()
    db.refresh(pool)
    db.add(FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1))
    db.add(FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2))
    db.add(
        FailoverClientLink(
            pool_id=pool.id, client_name="alice", primary_node_id=primary_node.id, access_token="tok123"
        )
    )
    db.commit()

    primary_adapter = _FakeAdapter(ANTIZAPRET2_CONF_WITH_PEER)
    # Replica already has alice — different IP than primary would suggest, on purpose,
    # to prove sync does NOT overwrite an already-present peer.
    already_synced_conf = EMPTY_SERVER_CONF + "\n# Client = alice\n[Peer]\nPublicKey = alice-pub==\nPresharedKey = alice-psk==\nAllowedIPs = 10.28.9.9/32\n"
    replica_adapter = _FakeAdapter(already_synced_conf)

    def fake_get_adapter(node):
        return primary_adapter if node.id == primary_node.id else replica_adapter

    monkeypatch.setattr("app.services.failover_pool.get_adapter_for_node", fake_get_adapter)

    result = sync_client_peer_to_pool(db, pool, "alice")

    assert result["synced_nodes"] == ["replica"]
    assert replica_adapter.conf.count("# Client = alice") == 1
    assert "10.28.9.9/32" in replica_adapter.conf  # untouched


def test_sync_client_peer_to_pool_raises_when_client_not_linked():
    db = _make_db()
    pool = FailoverPool(name="test", vpn_type=VpnType.amneziawg2, mode=FailoverPoolMode.auto)
    db.add(pool)
    db.commit()
    db.refresh(pool)

    with pytest.raises(FailoverPoolError):
        sync_client_peer_to_pool(db, pool, "nobody")


def test_build_member_client_conf_combines_primary_identity_with_member_server(monkeypatch):
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")

    pool = FailoverPool(name="test", vpn_type=VpnType.amneziawg2, mode=FailoverPoolMode.auto)
    db.add(pool)
    db.commit()
    db.refresh(pool)
    member = FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2, label="replica.example.com")
    db.add(FailoverPoolMember(pool_id=pool.id, node_id=primary_node.id, priority=1))
    db.add(member)
    db.add(
        FailoverClientLink(
            pool_id=pool.id, client_name="alice", primary_node_id=primary_node.id, access_token="tok123"
        )
    )
    db.commit()
    db.refresh(member)

    primary_adapter = _FakeAdapter(ANTIZAPRET2_CONF_WITH_PEER)
    replica_conf = EMPTY_SERVER_CONF + "\n# Client = alice\n[Peer]\nPublicKey = alice-pub==\nPresharedKey = alice-psk==\nAllowedIPs = 10.28.9.5/32\n"
    replica_adapter = _FakeAdapter(replica_conf, server_key="PRIVATE_KEY=r-priv==\nPUBLIC_KEY=r-pub==")

    def fake_get_adapter(node):
        return primary_adapter if node.id == primary_node.id else replica_adapter

    monkeypatch.setattr("app.services.failover_pool.get_adapter_for_node", fake_get_adapter)

    conf = build_member_client_conf(db, pool, "alice", member)

    assert "PrivateKey = alice-priv==" in conf  # client's own identity, from primary
    assert "Address = 10.28.9.5/32" in conf  # IP allocated on the replica specifically
    assert "PublicKey = r-pub==" in conf  # replica's OWN server public key, not primary's
    assert "PresharedKey = alice-psk==" in conf
    assert "Endpoint = replica.example.com:53443" in conf
    assert "Jmin = 20" in conf
    assert "AllowedIPs = 0.0.0.0/0" in conf


def test_build_member_client_conf_raises_if_not_synced_yet(monkeypatch):
    db = _make_db()
    primary_node = _make_node(db, "primary", "1.1.1.1")
    replica_node = _make_node(db, "replica", "2.2.2.2")

    pool = FailoverPool(name="test", vpn_type=VpnType.amneziawg2, mode=FailoverPoolMode.auto)
    db.add(pool)
    db.commit()
    db.refresh(pool)
    member = FailoverPoolMember(pool_id=pool.id, node_id=replica_node.id, priority=2)
    db.add(member)
    db.add(
        FailoverClientLink(
            pool_id=pool.id, client_name="alice", primary_node_id=primary_node.id, access_token="tok123"
        )
    )
    db.commit()
    db.refresh(member)

    primary_adapter = _FakeAdapter(ANTIZAPRET2_CONF_WITH_PEER)
    replica_adapter = _FakeAdapter(EMPTY_SERVER_CONF)  # alice NOT synced here yet

    def fake_get_adapter(node):
        return primary_adapter if node.id == primary_node.id else replica_adapter

    monkeypatch.setattr("app.services.failover_pool.get_adapter_for_node", fake_get_adapter)

    with pytest.raises(FailoverPoolError):
        build_member_client_conf(db, pool, "alice", member)
