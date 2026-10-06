"""AmneziaWG 3.1 in the NOC report, and AmneziaWG sessions kept out of the OpenVPN/WireGuard buckets."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ConnectionCountSample, Node, NodeStatus, TrafficSessionState
from app.services import alert_rules, noc_report as nr


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _node(db, node_id=1, name="nl1"):
    node = Node(id=node_id, name=name, host=f"{name}.example", status=NodeStatus.online)
    db.add(node)
    db.commit()
    return node


def _session(db, node_id, profile, key, *, active=True):
    db.add(
        TrafficSessionState(
            node_id=node_id,
            session_key=key,
            profile=profile,
            common_name=key,
            is_active=active,
            last_seen_at=datetime.utcnow(),
        )
    )


def _toggles(monkeypatch, *, awg2, awg3):
    monkeypatch.setattr(nr, "is_awg2_enabled", lambda _db: awg2)
    monkeypatch.setattr(nr, "is_awg3_enabled", lambda _db: awg3)


def test_awg_sessions_do_not_inflate_openvpn_or_wireguard_counts(db, monkeypatch):
    _node(db)
    _session(db, 1, "antizapret-udp", "ovpn1")
    _session(db, 1, "antizapret-wg", "wg1")
    _session(db, 1, "antizapret-awg2", "a2")
    _session(db, 1, "antizapret-awg3", "a3")
    _session(db, 1, "vpn-awg3", "a3b")
    db.commit()
    _toggles(monkeypatch, awg2=True, awg3=True)

    summary = nr.build_noc_summary(db)

    assert summary["total_openvpn"] == 1
    assert summary["total_wireguard"] == 1


def test_alert_rules_session_counts_skip_awg(db):
    _node(db)
    _session(db, 1, "vpn-udp", "ovpn1")
    _session(db, 1, "vpn-wg", "wg1")
    _session(db, 1, "vpn-awg2", "a2")
    _session(db, 1, "vpn-awg3", "a3")
    db.commit()
    assert alert_rules._session_counts(db) == (1, 1)


def test_summary_reads_latest_awg3_count_per_node(db, monkeypatch):
    _node(db, 1, "de1")
    _node(db, 2, "nl1")
    now = datetime.utcnow()
    for node_id, awg2, awg3, age in ((1, 1, 2, 30), (1, 1, 5, 5), (2, 0, 3, 5)):
        db.add(
            ConnectionCountSample(
                node_id=node_id,
                openvpn_count=0,
                wireguard_count=0,
                amneziawg2_count=awg2,
                amneziawg3_count=awg3,
                created_at=now - timedelta(minutes=age),
            )
        )
    db.commit()
    _toggles(monkeypatch, awg2=True, awg3=True)

    summary = nr.build_noc_summary(db)

    assert summary["awg3_enabled"] is True
    assert summary["total_amneziawg3"] == 5 + 3
    assert {n["name"]: n["amneziawg3"] for n in summary["nodes"]} == {"de1": 5, "nl1": 3}
    assert summary["total_amneziawg2"] == 1  # untouched AWG 2.0 numbers


def test_summary_zeroes_awg3_when_toggle_off(db, monkeypatch):
    _node(db)
    db.add(ConnectionCountSample(node_id=1, openvpn_count=0, wireguard_count=0, amneziawg3_count=7, created_at=datetime.utcnow()))
    db.commit()
    _toggles(monkeypatch, awg2=False, awg3=False)
    summary = nr.build_noc_summary(db)
    assert summary["awg3_enabled"] is False
    assert summary["total_amneziawg3"] == 0 and summary["nodes"][0]["amneziawg3"] == 0


def test_period_stats_for_awg3_use_their_own_column():
    now = datetime.utcnow()
    samples = [
        SimpleNamespace(node_id=1, created_at=now - timedelta(hours=2), amneziawg2_count=9, amneziawg3_count=1),
        SimpleNamespace(node_id=1, created_at=now - timedelta(hours=1), amneziawg2_count=9, amneziawg3_count=4),
        SimpleNamespace(node_id=2, created_at=now - timedelta(hours=1), amneziawg2_count=9, amneziawg3_count=2),
    ]
    by_node, fleet = nr._awg_stats_from_connection_samples(
        samples, since=now - timedelta(hours=3), until=now, field="amneziawg3"
    )
    assert by_node[1]["amneziawg3"] == 2.5 and by_node[1]["amneziawg3_peak"] == 4
    assert fleet == {"amneziawg3_peak": 6}
    # the AWG 2.0 wrapper still reads its own column
    by2, fleet2 = nr._awg2_stats_from_connection_samples(samples, since=now - timedelta(hours=3), until=now)
    assert by2[1]["amneziawg2"] == 9 and fleet2["amneziawg2_peak"] == 18


def test_period_stats_without_samples_return_field_specific_empty_peak():
    now = datetime.utcnow()
    assert nr._awg_stats_from_connection_samples([], since=now - timedelta(hours=1), until=now, field="amneziawg3") == (
        {},
        {"amneziawg3_peak": 0},
    )


def test_enrich_period_averages_for_awg3_leaves_awg2_flag_alone():
    summary = {"awg2_enabled": True, "nodes": [{"node_id": 1}, {"node_id": 2}]}
    nr._enrich_summary_with_period_awg_averages(
        summary,
        stats_by_node={1: {"amneziawg3": 2.5, "amneziawg3_peak": 4}},
        fleet_peaks={"amneziawg3_peak": 6},
        enabled=True,
        field="amneziawg3",
    )
    assert summary["awg2_enabled"] is True and summary["awg3_enabled"] is True
    assert summary["total_amneziawg3"] == 2.5 and summary["total_amneziawg3_peak"] == 6
    assert summary["nodes"][1]["amneziawg3"] == 0.0 and summary["nodes"][1]["amneziawg3_peak"] == 0


def _summary(awg3):
    return {
        "nodes_online": 1,
        "nodes_total": 1,
        "total_openvpn": 1,
        "total_wireguard": 2,
        "total_amneziawg2": 3,
        "total_amneziawg3": 4,
        "total_openvpn_peak": 1,
        "total_wireguard_peak": 2,
        "total_amneziawg2_peak": 3,
        "total_amneziawg3_peak": 6,
        "awg2_enabled": True,
        "awg3_enabled": awg3,
        "nodes": [
            {
                "name": "n1",
                "status": "online",
                "openvpn": 1,
                "wireguard": 2,
                "amneziawg2": 3,
                "amneziawg3": 4,
                "openvpn_peak": 1,
                "wireguard_peak": 2,
                "amneziawg2_peak": 3,
                "amneziawg3_peak": 6,
            }
        ],
    }


def test_text_report_shows_awg3_totals_and_per_node_when_enabled():
    text = nr.format_noc_report_message({"period": "daily", "summary": _summary(True)})
    assert "AWG3 <b>4</b>" in text and "AWG3 <b>6</b>" in text
    assert "AWG3 4 (макс. 6)" in text
    assert "AWG2 <b>3</b>" in text


def test_text_report_omits_awg3_when_disabled():
    text = nr.format_noc_report_message({"period": "daily", "summary": _summary(False)})
    assert "AWG3" not in text and "AWG2 <b>3</b>" in text


@pytest.mark.parametrize("awg2,awg3", [(False, False), (True, False), (False, True), (True, True)])
def test_weekly_image_renders_for_every_awg_toggle_combination(awg2, awg3):
    from app.services.noc_report_image import generate_weekly_image

    summary = _summary(awg3)
    summary["awg2_enabled"] = awg2
    summary.update({"period_traffic_bytes": 1000, "traffic_delta_pct": 1.0})
    png = generate_weekly_image(
        {"period": {}, "compare_label": "к прошлой неделе", "summary": summary, "top_clients": [], "incidents": [], "cidr_failures": []}
    )
    assert png[:4] == b"\x89PNG" and len(png) > 10_000


def test_image_estimated_height_grows_with_each_extra_awg_kpi_card():
    from app.services.noc_report_image import NocWeeklyImageRenderer

    def height(awg2, awg3):
        summary = _summary(awg3)
        summary["awg2_enabled"] = awg2
        return NocWeeklyImageRenderer({"summary": summary, "top_clients": []})._estimate_height()

    assert height(False, False) <= height(True, False) <= height(True, True)
    assert height(False, True) >= height(False, False)
