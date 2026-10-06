"""AWG 3.1 connection accounting follows the AWG 2.0 path."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.schemas import WireGuardPeer
from app.services import connection_history as ch


def test_aggregate_bucket_includes_amneziawg3():
    sample = SimpleNamespace(
        created_at=datetime.utcnow(),
        node_id=1,
        openvpn_count=1,
        wireguard_count=2,
        amneziawg2_count=3,
        amneziawg3_count=4,
    )
    point = ch._aggregate_bucket([sample], sum_nodes=False)
    assert point["amneziawg3"] == 4
    assert point["total"] == 10


def test_aggregate_bucket_sums_amneziawg3_across_nodes():
    now = datetime.utcnow()
    samples = [
        SimpleNamespace(created_at=now, node_id=1, openvpn_count=0, wireguard_count=0, amneziawg2_count=0, amneziawg3_count=2),
        SimpleNamespace(created_at=now, node_id=2, openvpn_count=0, wireguard_count=0, amneziawg2_count=0, amneziawg3_count=5),
    ]
    assert ch._aggregate_bucket(samples, sum_nodes=True)["amneziawg3"] == 7


def test_collect_samples_counts_online_awg3_peers(monkeypatch):
    monkeypatch.setattr(ch, "is_awg2_enabled", lambda _db: False)
    monkeypatch.setattr(ch, "is_awg3_enabled", lambda _db: True)
    node = SimpleNamespace(id=1, name="n", status="online")
    db = MagicMock()
    db.query.return_value.order_by.return_value.all.return_value = [node]
    adapter = MagicMock()
    monkeypatch.setattr(ch, "get_adapter_for_node", lambda _n: adapter)
    monkeypatch.setattr(adapter, "get_openvpn_status_snapshot", lambda: ([], "status_log"))
    monkeypatch.setattr(adapter, "parse_wireguard_status", lambda: [])
    now = datetime.utcnow()
    fresh = WireGuardPeer(interface="antizapret3", public_key="a", client_name="alice",
                          latest_handshake=(now - timedelta(seconds=5)).isoformat())
    stale = WireGuardPeer(interface="vpn3", public_key="b", client_name="bob",
                          latest_handshake=(now - timedelta(seconds=900)).isoformat())
    monkeypatch.setattr(ch, "fetch_awg3_peers_for_adapter", lambda _a: [fresh, stale])
    persisted = {}

    def fake_persist(db, node_id, *, openvpn_count, wireguard_count, amneziawg2_count=0, amneziawg3_count=0, commit=True):
        persisted.update(amneziawg3_count=amneziawg3_count, commit=commit)
        return MagicMock()

    monkeypatch.setattr(ch, "persist_connection_sample", fake_persist)
    ch.collect_connection_samples(db)
    assert persisted["amneziawg3_count"] == 1
    assert persisted["commit"] is False


def test_collect_samples_awg3_zero_when_toggle_off(monkeypatch):
    monkeypatch.setattr(ch, "is_awg2_enabled", lambda _db: False)
    monkeypatch.setattr(ch, "is_awg3_enabled", lambda _db: False)
    fetch = MagicMock()
    monkeypatch.setattr(ch, "fetch_awg3_peers_for_adapter", fetch)
    node = SimpleNamespace(id=1, name="n", status="online")
    db = MagicMock()
    db.query.return_value.order_by.return_value.all.return_value = [node]
    adapter = MagicMock()
    monkeypatch.setattr(ch, "get_adapter_for_node", lambda _n: adapter)
    monkeypatch.setattr(adapter, "get_openvpn_status_snapshot", lambda: ([], "status_log"))
    monkeypatch.setattr(adapter, "parse_wireguard_status", lambda: [])
    monkeypatch.setattr(ch, "persist_connection_sample", lambda *a, **k: MagicMock())
    ch.collect_connection_samples(db)
    fetch.assert_not_called()


def test_history_api_point_keeps_the_awg3_count_instead_of_dropping_it():
    """ConnectionHistoryPoint(**point) used to discard `amneziawg3`: the chart never saw AWG 3.1."""
    from app.schemas import ConnectionHistoryPoint

    sample = SimpleNamespace(
        created_at=datetime.utcnow(),
        node_id=1,
        openvpn_count=1,
        wireguard_count=2,
        amneziawg2_count=3,
        amneziawg3_count=4,
    )
    point = ch._aggregate_bucket([sample], sum_nodes=False)
    api_point = ConnectionHistoryPoint(**point)
    assert api_point.amneziawg3 == 4
    assert api_point.total == 10
    assert api_point.model_dump()["amneziawg3"] == 4


def test_history_point_defaults_awg3_to_zero_for_old_payloads():
    from app.schemas import ConnectionHistoryPoint

    point = ConnectionHistoryPoint(timestamp=datetime.utcnow(), openvpn=1, wireguard=1, total=2)
    assert point.amneziawg2 == 0 and point.amneziawg3 == 0
