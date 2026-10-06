"""AmneziaWG 3.1 peers wired into the monitoring overview next to AmneziaWG 2.0."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import VpnType
from app.schemas import MonitoringOverview, WireGuardPeer
from app.services import monitoring_overview as mo


def _peer(name: str, *, online: bool, interface: str = "antizapret3", rx: int = 0, tx: int = 0, endpoint="203.0.113.7:51900"):
    seen = datetime.utcnow() - timedelta(seconds=10 if online else 900)
    return WireGuardPeer(
        interface=interface,
        public_key=f"pk-{name}",
        client_name=name,
        endpoint=endpoint,
        latest_handshake=seen.isoformat(),
        transfer_rx=rx,
        transfer_tx=tx,
    )


def test_overview_skips_awg3_when_toggle_off(monkeypatch):
    monkeypatch.setattr(mo, "is_awg3_enabled", lambda _db: False)
    fetch = MagicMock()
    monkeypatch.setattr(mo, "fetch_awg3_peers_for_adapter", fetch)
    assert mo._load_awg3_peers_for_node(MagicMock(), MagicMock()) == []
    fetch.assert_not_called()


def test_overview_loads_awg3_peers_when_enabled(monkeypatch):
    monkeypatch.setattr(mo, "is_awg3_enabled", lambda _db: True)
    peer = _peer("alice", online=True)
    monkeypatch.setattr(mo, "fetch_awg3_peers_for_adapter", lambda _a: [peer])
    assert mo._load_awg3_peers_for_node(MagicMock(), MagicMock()) == [peer]


def test_node_summary_counts_awg3_online_separately_from_awg2():
    payload = {
        "node": SimpleNamespace(id=1, name="n", status="online"),
        "ovpn_clients": [],
        "wireguard_peers": [],
        "amneziawg2_peers": [_peer("old", online=True, interface="antizapret2")],
        "amneziawg3_peers": [_peer("a", online=True), _peer("b", online=True, interface="vpn3"), _peer("c", online=False)],
        "services": [],
        "error": None,
        "cpu_percent": None,
        "memory_percent": None,
        "total_traffic_bytes": None,
        "cidr_routes_count": None,
    }
    summary = mo._build_node_summary(payload)
    assert summary.connected_amneziawg2 == 1
    assert summary.connected_amneziawg3 == 2


def test_node_summary_tolerates_payload_without_awg3_key():
    payload = {
        "node": SimpleNamespace(id=1, name="n", status="online"),
        "ovpn_clients": [],
        "wireguard_peers": [],
        "services": [],
        "error": None,
        "cpu_percent": None,
        "memory_percent": None,
        "total_traffic_bytes": None,
        "cidr_routes_count": None,
    }
    assert mo._build_node_summary(payload).connected_amneziawg3 == 0


def test_lookup_ips_include_awg3_endpoints():
    ips = mo._collect_lookup_ips([], [], amneziawg3_peers=[_peer("a", online=True, endpoint="198.51.100.9:40000")])
    assert ips == ["198.51.100.9"]


def test_single_node_overview_returns_awg3_peers_and_totals(monkeypatch):
    peers = [_peer("alice", online=True, rx=100, tx=200), _peer("bob", online=False)]
    adapter = MagicMock()
    adapter.get_openvpn_status_snapshot.return_value = ([], "status_log")
    adapter.parse_wireguard_status.return_value = []
    adapter.get_service_status.return_value = []
    adapter.get_server_ip.return_value = "185.193.51.13"
    node = SimpleNamespace(id=7, name="nl1", status="online", node_kind="vpn", host="h")

    monkeypatch.setattr(mo, "is_vpn_node", lambda _n: True)
    monkeypatch.setattr(mo, "get_adapter_for_node", lambda _n: adapter)
    monkeypatch.setattr(mo, "_load_awg2_peers_for_node", lambda _db, _a: [])
    monkeypatch.setattr(mo, "_load_awg3_peers_for_node", lambda _db, _a: peers)
    monkeypatch.setattr(mo, "_load_proxy_noc_context", lambda _db: (set(), {}))
    monkeypatch.setattr(mo, "lookup_ips_geo", lambda _ips: {})

    overview = mo.build_monitoring_overview_for_node(MagicMock(), node)

    assert isinstance(overview, MonitoringOverview)
    assert [p.client_name for p in overview.amneziawg3_peers] == ["alice", "bob"]
    assert overview.total_connected_amneziawg3 == 1
    assert overview.total_connected_amneziawg2 == 0
    # the geo/proxy enrichment ran on 3.1 peers too (display address is filled from the endpoint)
    assert overview.amneziawg3_peers[0].client_ip == "203.0.113.7"


def test_overview_without_awg3_keeps_empty_defaults(monkeypatch):
    adapter = MagicMock()
    adapter.get_openvpn_status_snapshot.return_value = ([], "status_log")
    adapter.parse_wireguard_status.return_value = []
    adapter.get_service_status.return_value = []
    adapter.get_server_ip.return_value = None
    node = SimpleNamespace(id=1, name="n", status="online", node_kind="vpn", host="h")
    monkeypatch.setattr(mo, "is_vpn_node", lambda _n: True)
    monkeypatch.setattr(mo, "get_adapter_for_node", lambda _n: adapter)
    monkeypatch.setattr(mo, "_load_awg2_peers_for_node", lambda _db, _a: [])
    monkeypatch.setattr(mo, "_load_awg3_peers_for_node", lambda _db, _a: [])
    monkeypatch.setattr(mo, "_load_proxy_noc_context", lambda _db: (set(), {}))
    monkeypatch.setattr(mo, "lookup_ips_geo", lambda _ips: {})
    overview = mo.build_monitoring_overview_for_node(MagicMock(), node)
    assert overview.amneziawg3_peers == [] and overview.total_connected_amneziawg3 == 0


def test_federated_overview_collects_awg3_across_nodes(monkeypatch):
    nodes = [
        SimpleNamespace(id=1, name="de1", status="online"),
        SimpleNamespace(id=2, name="nl1", status="online"),
    ]
    payloads = [
        {
            "node": nodes[0], "ovpn_clients": [], "wireguard_peers": [], "amneziawg2_peers": [],
            "amneziawg3_peers": [_peer("alice", online=True)], "services": [], "server_ip": "1.1.1.1",
            "error": None, "cpu_percent": None, "memory_percent": None, "total_traffic_bytes": None,
            "cidr_routes_count": None,
        },
        {
            "node": nodes[1], "ovpn_clients": [], "wireguard_peers": [], "amneziawg2_peers": [],
            "amneziawg3_peers": [_peer("bob", online=True), _peer("carol", online=False)], "services": [],
            "server_ip": "2.2.2.2", "error": None, "cpu_percent": None, "memory_percent": None,
            "total_traffic_bytes": None, "cidr_routes_count": None,
        },
    ]
    monkeypatch.setattr(mo, "_collect_nodes_monitoring_data", lambda _db: payloads)
    monkeypatch.setattr(mo, "_load_proxy_noc_context", lambda _db: (set(), {}))
    monkeypatch.setattr(mo, "lookup_ips_geo", lambda _ips: {})
    monkeypatch.setattr(mo, "_build_ha_monitoring_lookup", lambda _db: {})
    monkeypatch.setattr(mo, "get_active_node", lambda _db: nodes[0])

    overview = mo.build_federated_monitoring_overview(MagicMock())

    assert sorted(p.client_name for p in overview.amneziawg3_peers) == ["alice", "bob", "carol"]
    assert {p.node_name for p in overview.amneziawg3_peers} == {"de1", "nl1"}
    assert overview.total_connected_amneziawg3 == 2
    assert [s.connected_amneziawg3 for s in overview.nodes_summary] == [1, 1]

    raw = mo.build_federated_monitoring_overview(MagicMock(), ha_mode="raw")
    assert raw.total_connected_amneziawg3 == 2


def _ha_lookup(node_ids, name="alice"):
    """HA lookup for one AWG 3.1 client that lives on several nodes of one sync group."""
    from app.schemas import VpnConfigHaInfo

    info = VpnConfigHaInfo(
        sync_group_id=1, shared_domain="vpn.example.com", node_count=len(node_ids), sync_status="synced", sync_mode="auto"
    )
    key = ("ha", 1, VpnType.amneziawg3.value, name)
    return {(node_id, name, VpnType.amneziawg3.value): (key, info) for node_id in node_ids}


def test_ha_aggregation_merges_awg3_peer_present_on_two_nodes():
    on_a = _peer("alice", online=True, rx=10).model_copy(update={"node_id": 1, "node_name": "de1"})
    on_b = _peer("alice", online=True, rx=999).model_copy(update={"node_id": 2, "node_name": "nl1"})
    merged = mo._aggregate_ha_amneziawg3_peers([on_a, on_b], _ha_lookup([1, 2]))
    assert len(merged) == 1
    assert merged[0].node_name == "nl1"  # the node with more traffic wins, like for AWG 2.0
    assert merged[0].ha is not None and {n.node_name for n in merged[0].ha_nodes} == {"de1", "nl1"}


def test_ha_aggregation_keeps_awg3_peers_of_different_protocol_apart():
    """An HA record for AWG 2.0 must not merge AWG 3.1 peers with the same client name."""
    on_a = _peer("alice", online=True).model_copy(update={"node_id": 1, "node_name": "de1"})
    on_b = _peer("alice", online=True).model_copy(update={"node_id": 2, "node_name": "nl1"})
    awg2_only = {
        (1, "alice", VpnType.amneziawg2.value): _ha_lookup([1], "alice")[(1, "alice", VpnType.amneziawg3.value)],
        (2, "alice", VpnType.amneziawg2.value): _ha_lookup([2], "alice")[(2, "alice", VpnType.amneziawg3.value)],
    }
    assert len(mo._aggregate_ha_amneziawg3_peers([on_a, on_b], awg2_only)) == 2


def test_raw_ha_mode_annotates_awg3_peers_without_merging():
    on_a = _peer("alice", online=True).model_copy(update={"node_id": 1, "node_name": "de1"})
    on_b = _peer("alice", online=True).model_copy(update={"node_id": 2, "node_name": "nl1"})
    raw = mo._annotate_raw_ha_amneziawg3_peers([on_a, on_b], _ha_lookup([1, 2]))
    assert len(raw) == 2 and all(p.ha is not None for p in raw)
