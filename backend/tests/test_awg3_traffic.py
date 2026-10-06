"""AWG 3 traffic statistics and online status follow the AWG 2 path."""

import json
import time
from pathlib import Path

from app.services import native_awg3_runtime as rt
from app.services.awg3_noc import fetch_awg3_peers_for_adapter, peers_from_awg3_monitoring
from app.services.traffic.collector import build_session_key, build_status_rows, protocol_type_from_profile

FRESH = int(time.time()) - 30
STALE = int(time.time()) - 3600
PUB_SPLIT = "PUBSPLIT="
PUB_FULL = "PUBFULL="


def _dump(*rows: str) -> str:
    header = "PRIV\t51821\tOFF\n"
    return header + "\n".join(rows) + "\n"


def _peer_line(pub: str, endpoint: str, allowed: str, handshake: int, rx: int, tx: int) -> str:
    return f"{pub}\t(none)\t{endpoint}\t{allowed}\t{handshake}\t{rx}\t{tx}\t15"


def test_monitoring_clients_named_from_registry_with_age_and_mode(tmp_path: Path, monkeypatch):
    (tmp_path / "clients.json").write_text(json.dumps({
        "alice": {"mode": "split", "public_key": PUB_SPLIT, "ip": "10.9.0.2"},
        "bob": {"mode": "full", "public_key": PUB_FULL, "ip": "10.9.1.2"},
    }), encoding="utf-8")
    monkeypatch.setattr(rt, "AWG3_CONF_DIR", tmp_path)
    monkeypatch.setattr(rt, "AWG3_IFACES", {"split": "awg1"})
    monkeypatch.setattr(rt, "_iface_up", lambda name: True)
    dump = _dump(
        _peer_line(PUB_SPLIT, "203.0.113.5:40000", "10.9.0.2/32", FRESH, 100, 200),
        _peer_line(PUB_FULL, "(none)", "10.9.1.2/32", STALE, 5, 6),
    )
    monkeypatch.setattr(rt, "_run", lambda args: dump)

    payload = rt.get_awg3_monitoring()
    clients = {c["pubkey"]: c for c in payload["clients"]}
    assert clients[PUB_SPLIT]["name"] == "alice"
    assert clients[PUB_SPLIT]["mode"] == "split"
    assert clients[PUB_SPLIT]["endpoint"] == "203.0.113.5:40000"
    assert 0 <= clients[PUB_SPLIT]["handshake_age_s"] <= 120
    assert clients[PUB_FULL]["name"] == "bob"
    assert clients[PUB_FULL]["endpoint"] is None
    assert clients[PUB_FULL]["handshake_age_s"] >= 3000
    assert payload["ifaces"]["split"]["peers"][0]["public_key"] == PUB_SPLIT


def test_peer_mapping_uses_split_and_full_interface_labels(tmp_path, monkeypatch):
    payload = {"clients": [
        {"name": "alice", "pubkey": PUB_SPLIT, "mode": "split", "handshake_age_s": 10, "rx": 1, "tx": 2},
        {"name": "bob", "pubkey": PUB_FULL, "mode": "full", "handshake_age_s": 10, "rx": 3, "tx": 4},
    ]}
    peers = {p.client_name: p for p in peers_from_awg3_monitoring(payload)}
    assert peers["alice"].interface == "antizapret"
    assert peers["bob"].interface == "vpn"
    assert peers["alice"].transfer_rx == 1 and peers["bob"].transfer_tx == 4


def test_fetch_returns_empty_when_adapter_fails():
    class Broken:
        def awg3_monitoring(self):
            raise RuntimeError("node down")

    assert fetch_awg3_peers_for_adapter(Broken()) == []


def test_profile_and_protocol_type_for_awg3():
    assert protocol_type_from_profile("antizapret-awg3") == "amneziawg3"
    assert protocol_type_from_profile("vpn-awg3") == "amneziawg3"
    assert protocol_type_from_profile("vpn-awg") == "wireguard"
    assert protocol_type_from_profile("vpn-awg2") == "amneziawg2"


def test_only_fresh_handshakes_become_online_rows(tmp_path, monkeypatch):
    payload = {"clients": [
        {"name": "alice", "pubkey": PUB_SPLIT, "mode": "split", "handshake_age_s": 30, "rx": 7, "tx": 8,
         "endpoint": "203.0.113.5:40000", "allowed_ips": "10.9.0.2/32"},
        {"name": "bob", "pubkey": PUB_FULL, "mode": "full", "handshake_age_s": 900, "rx": 1, "tx": 1},
    ]}
    peers = peers_from_awg3_monitoring(payload)
    rows = build_status_rows([], [], [], peers)

    assert [r["profile"] for r in rows] == ["antizapret-awg3"]
    client = rows[0]["traffic_clients"][0]
    assert client["common_name"] == "alice"
    assert client["session_kind"] == "amneziawg3"
    assert client["bytes_received"] == 7
    # Session identity for AWG 3 is the public key, like the other handshake protocols.
    assert build_session_key("antizapret-awg3", client).startswith("antizapret-awg3|wg|alice|")
