"""Every traffic collector builds its input through one helper, so AWG 3 is not collected by one path and skipped by another."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import app.services.traffic.worker as worker
from app.schemas import WireGuardPeer
from app.services.traffic import collector as col
from app.services.traffic import maintenance


def _peer(interface: str, name: str, *, fresh: bool = True) -> WireGuardPeer:
    seen = datetime.utcnow() - timedelta(seconds=5 if fresh else 900)
    return WireGuardPeer(
        interface=interface,
        public_key=f"pk-{name}",
        client_name=name,
        endpoint="203.0.113.7:51900",
        allowed_ips="10.9.0.2/32",
        latest_handshake=seen.isoformat(),
        transfer_rx=1000,
        transfer_tx=2000,
    )


def _adapter(awg3_peers=(), awg2_peers=()):
    adapter = MagicMock()
    adapter.parse_openvpn_status.return_value = []
    adapter.parse_wireguard_status.return_value = []
    adapter.get_awg2_monitoring.return_value = {}
    adapter.awg3_monitoring.return_value = {}
    return adapter, list(awg2_peers), list(awg3_peers)


def _patch_fetch(monkeypatch, awg2, awg3):
    import app.services.awg2_noc as awg2_noc
    import app.services.awg3_noc as awg3_noc

    monkeypatch.setattr(awg2_noc, "fetch_awg2_peers_for_adapter", lambda _a: awg2)
    monkeypatch.setattr(awg3_noc, "fetch_awg3_peers_for_adapter", lambda _a: awg3)


def _profiles(rows):
    return sorted(row["profile"] for row in rows)


def test_helper_includes_awg3_rows_next_to_awg2(monkeypatch):
    adapter, awg2, awg3 = _adapter(
        awg3_peers=[_peer("antizapret", "alice"), _peer("vpn", "bob")],
        awg2_peers=[_peer("antizapret2", "carol")],
    )
    _patch_fetch(monkeypatch, awg2, awg3)
    rows = col.build_status_rows_for_adapter(MagicMock(), adapter, awg2_enabled=True, awg3_enabled=True)
    assert _profiles(rows) == ["antizapret-awg2", "antizapret-awg3", "vpn-awg3"]
    awg3_row = next(row for row in rows if row["profile"] == "antizapret-awg3")["traffic_clients"][0]
    assert awg3_row["common_name"] == "alice" and awg3_row["session_kind"] == "amneziawg3"
    assert awg3_row["bytes_received"] == 1000 and awg3_row["bytes_sent"] == 2000


def test_disabled_toggle_removes_only_that_protocol(monkeypatch):
    adapter, awg2, awg3 = _adapter(awg3_peers=[_peer("antizapret", "alice")], awg2_peers=[_peer("antizapret2", "carol")])
    _patch_fetch(monkeypatch, awg2, awg3)
    only2 = col.build_status_rows_for_adapter(MagicMock(), adapter, awg2_enabled=True, awg3_enabled=False)
    only3 = col.build_status_rows_for_adapter(MagicMock(), adapter, awg2_enabled=False, awg3_enabled=True)
    assert _profiles(only2) == ["antizapret-awg2"]
    assert _profiles(only3) == ["antizapret-awg3"]


def test_helper_reads_toggles_when_flags_are_not_given(monkeypatch):
    adapter, awg2, awg3 = _adapter(awg3_peers=[_peer("antizapret", "alice")])
    _patch_fetch(monkeypatch, awg2, awg3)
    import app.services.feature_toggles as toggles

    monkeypatch.setattr(toggles, "is_awg2_enabled", lambda _db=None: False)
    monkeypatch.setattr(toggles, "is_awg3_enabled", lambda _db=None: True)
    rows = col.build_status_rows_for_adapter(MagicMock(), adapter)
    assert _profiles(rows) == ["antizapret-awg3"]


def test_stale_awg3_peer_is_not_reported_online(monkeypatch):
    adapter, awg2, awg3 = _adapter(awg3_peers=[_peer("antizapret", "alice", fresh=False)])
    _patch_fetch(monkeypatch, awg2, awg3)
    assert col.build_status_rows_for_adapter(MagicMock(), adapter, awg2_enabled=True, awg3_enabled=True) == []


def test_background_worker_persists_awg3_rows(monkeypatch):
    """The reported symptom: AWG 3 clients had no traffic because the worker never passed their peers on."""
    node = SimpleNamespace(id=1, name="n1")
    db = MagicMock()
    db.query.return_value.all.return_value = [node]
    adapter, awg2, awg3 = _adapter(awg3_peers=[_peer("antizapret", "alice")])
    _patch_fetch(monkeypatch, awg2, awg3)

    persisted = {}

    class FakeCollector:
        def __init__(self, _db, node_id):
            persisted["node_id"] = node_id

        def persist_snapshot(self, rows):
            persisted["rows"] = rows

    import app.services.feature_toggles as toggles

    monkeypatch.setattr(toggles, "is_awg2_enabled", lambda _db=None: False)
    monkeypatch.setattr(worker, "is_awg2_enabled", lambda _db=None: False)
    monkeypatch.setattr(worker, "is_awg3_enabled", lambda _db=None: True)
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "is_vpn_node", lambda _n: True)
    monkeypatch.setattr(worker, "get_adapter_for_node", lambda _n: adapter)
    monkeypatch.setattr(worker, "TrafficCollectorService", FakeCollector)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(traffic_limit_reconcile_after_sync=False))

    worker._collect_all_nodes()

    assert persisted["node_id"] == 1
    assert _profiles(persisted["rows"]) == ["antizapret-awg3"]


def test_maintenance_snapshot_uses_the_same_helper(monkeypatch):
    adapter, awg2, awg3 = _adapter(awg3_peers=[_peer("vpn", "bob")])
    _patch_fetch(monkeypatch, awg2, awg3)
    import app.services.feature_toggles as toggles

    monkeypatch.setattr(toggles, "is_awg2_enabled", lambda _db=None: False)
    monkeypatch.setattr(toggles, "is_awg3_enabled", lambda _db=None: True)
    service = maintenance.TrafficMaintenanceService.__new__(maintenance.TrafficMaintenanceService)
    service.db = MagicMock()
    assert _profiles(service.collect_status_rows_for_snapshot(adapter)) == ["vpn-awg3"]
