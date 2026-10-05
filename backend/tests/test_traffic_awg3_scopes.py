"""Chart / reset scopes must accept and isolate amneziawg3 traffic (same contract as amneziawg2)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import TrafficSessionState, UserTrafficSample, UserTrafficStatProtocol
from app.services.traffic.chart import fetch_traffic_chart
from app.services.traffic.collector import TrafficCollectorService, protocol_type_from_profile
from app.services.traffic.maintenance import (
    TrafficMaintenanceService,
    _profile_matches_protocol_scope,
    normalize_traffic_protocol_scope,
)

NODE_ID = 1
CLIENT = "ivan"


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


def _add_sample(db, *, protocol: str, delta: int, hours_ago: float = 1.0) -> None:
    db.add(
        UserTrafficSample(
            node_id=NODE_ID,
            common_name=CLIENT,
            network_type="vpn",
            protocol_type=protocol,
            delta_received=delta,
            delta_sent=0,
            created_at=datetime.utcnow() - timedelta(hours=hours_ago),
        )
    )


def _add_session(db, *, profile: str, key: str) -> None:
    db.add(
        TrafficSessionState(
            node_id=NODE_ID,
            session_key=key,
            profile=profile,
            common_name=CLIENT,
            connected_since_ts=0,
            is_active=False,
        )
    )


def _add_stat(db, *, protocol: str, received: int) -> None:
    db.add(
        UserTrafficStatProtocol(
            node_id=NODE_ID,
            common_name=CLIENT,
            protocol_type=protocol,
            total_received=received,
            total_sent=0,
            total_sessions=1,
        )
    )


def test_profile_maps_to_amneziawg3_not_wireguard_or_openvpn():
    assert protocol_type_from_profile("antizapret-awg3") == "amneziawg3"
    assert protocol_type_from_profile("vpn-awg3") == "amneziawg3"
    assert protocol_type_from_profile("vpn-awg") == "wireguard"


def test_normalize_scope_accepts_amneziawg3():
    assert normalize_traffic_protocol_scope("amneziawg3") == "amneziawg3"
    assert normalize_traffic_protocol_scope("AmneziaWG3") == "amneziawg3"
    assert normalize_traffic_protocol_scope("bogus") == "all"


def test_profile_matches_amneziawg3_scope():
    assert _profile_matches_protocol_scope("antizapret-awg3", "amneziawg3")
    assert _profile_matches_protocol_scope("vpn-awg3", "amneziawg3")
    assert not _profile_matches_protocol_scope("antizapret-awg2", "amneziawg3")
    assert not _profile_matches_protocol_scope("vpn-awg3", "wireguard")


def test_chart_counts_amneziawg3_as_its_own_series_not_openvpn(db):
    _add_sample(db, protocol="openvpn", delta=10)
    _add_sample(db, protocol="amneziawg2", delta=20)
    _add_sample(db, protocol="amneziawg3", delta=40)
    db.commit()

    chart = fetch_traffic_chart(db, NODE_ID, CLIENT, "7d", "all")
    assert sum(chart["openvpn_bytes"]) == 10
    assert sum(chart["amneziawg2_bytes"]) == 20
    assert sum(chart["amneziawg3_bytes"]) == 40
    assert chart["total"] == 70

    awg3_only = fetch_traffic_chart(db, NODE_ID, CLIENT, "7d", "amneziawg3")
    assert awg3_only["protocol_filter"] == "amneziawg3"
    assert sum(awg3_only["amneziawg3_bytes"]) == 40
    assert sum(awg3_only["openvpn_bytes"]) == 0
    assert sum(awg3_only["amneziawg2_bytes"]) == 0
    assert awg3_only["total"] == 40


def test_delete_persisted_rows_amneziawg3_scope_leaves_others(db):
    _add_sample(db, protocol="openvpn", delta=1)
    _add_sample(db, protocol="amneziawg2", delta=2)
    _add_sample(db, protocol="amneziawg3", delta=3)
    _add_session(db, profile="antizapret-awg2", key="k2")
    _add_session(db, profile="antizapret-awg3", key="k3")
    db.commit()

    result = TrafficMaintenanceService(db, NODE_ID).delete_persisted_traffic_rows_by_scope("amneziawg3")
    db.commit()

    assert result["deleted_samples"] == 1
    assert result["deleted_sessions"] == 1
    assert {row.protocol_type for row in db.query(UserTrafficSample).all()} == {"openvpn", "amneziawg2"}
    assert {row.profile for row in db.query(TrafficSessionState).all()} == {"antizapret-awg2"}


def test_collector_reset_traffic_amneziawg3_scope(db):
    _add_sample(db, protocol="openvpn", delta=1)
    _add_sample(db, protocol="amneziawg3", delta=7)
    _add_stat(db, protocol="openvpn", received=1)
    _add_stat(db, protocol="amneziawg3", received=7)
    db.commit()

    deleted = TrafficCollectorService(db, NODE_ID).reset_traffic(scope="amneziawg3")

    assert deleted == 1
    assert db.query(UserTrafficSample).one().protocol_type == "openvpn"
    assert db.query(UserTrafficStatProtocol).one().protocol_type == "openvpn"
