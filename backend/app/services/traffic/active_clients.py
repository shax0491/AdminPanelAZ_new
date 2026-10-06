"""Resolve currently online traffic client names for a node."""

from __future__ import annotations

import time
from threading import Lock

from sqlalchemy.orm import Session

from app.models import Node, TrafficSessionState
from app.services.node_manager import is_vpn_node, get_adapter_for_node
from app.services.wireguard_status import wireguard_peer_is_online

# Coalesce rapid /traffic/active-clients + overview?live=true probes (UI + Telegram).
_LIVE_ACTIVE_TTL_SECONDS = 8.0
_live_active_lock = Lock()
_live_active_cache: dict[int, tuple[float, frozenset[str]]] = {}


def clear_live_active_names_cache() -> None:
    """Test helper — drop TTL cache for live online-name probes."""
    with _live_active_lock:
        _live_active_cache.clear()


def db_active_traffic_client_names(db: Session, node_id: int) -> set[str]:
    rows = (
        db.query(TrafficSessionState.common_name)
        .filter(TrafficSessionState.node_id == node_id, TrafficSessionState.is_active.is_(True))
        .distinct()
        .all()
    )
    return {name for (name,) in rows if name}


def live_active_names_for_node(
    db: Session,
    node: Node,
    *,
    ttl_seconds: float | None = _LIVE_ACTIVE_TTL_SECONDS,
) -> set[str]:
    """Live OVPN/WG/AWG2 online names, with DB session fallback if probe is empty.

    Short TTL cache avoids duplicate adapter status reads when TrafficPage,
    Telegram, or overview?live=true hit the same node within a few seconds.
    """
    if not is_vpn_node(node):
        return db_active_traffic_client_names(db, node.id)

    ttl = 0.0 if ttl_seconds is None else max(0.0, float(ttl_seconds))
    now = time.monotonic()
    if ttl > 0:
        with _live_active_lock:
            entry = _live_active_cache.get(int(node.id))
            if entry is not None and now < entry[0]:
                return set(entry[1])

    active_names: set[str] = set()
    try:
        adapter = get_adapter_for_node(node)
        ovpn = adapter.parse_openvpn_status()
        wg = adapter.parse_wireguard_status()
        active_names = {c.common_name for c in ovpn if c.common_name}
        active_names.update(
            p.client_name for p in wg if p.client_name and wireguard_peer_is_online(p)
        )
        try:
            from app.services.awg2_noc import fetch_awg2_peers_for_adapter
            from app.services.awg3_noc import fetch_awg3_peers_for_adapter
            from app.services.feature_toggles import is_awg2_enabled, is_awg3_enabled

            if is_awg2_enabled(db):
                awg2 = fetch_awg2_peers_for_adapter(adapter)
                active_names.update(
                    p.client_name for p in awg2 if p.client_name and wireguard_peer_is_online(p)
                )
            if is_awg3_enabled(db):
                awg3 = fetch_awg3_peers_for_adapter(adapter)
                active_names.update(
                    p.client_name for p in awg3 if p.client_name and wireguard_peer_is_online(p)
                )
        except Exception:
            pass
    except Exception:
        active_names = set()

    if not active_names:
        active_names = db_active_traffic_client_names(db, node.id)

    if ttl > 0:
        with _live_active_lock:
            _live_active_cache[int(node.id)] = (now + ttl, frozenset(active_names))

    return active_names


def protocol_family(protocol: str | None) -> str:
    """Protocol family used for online status: openvpn-udp/openvpn-tcp/openvpn -> openvpn."""
    value = (protocol or "").strip().lower()
    if value.startswith("openvpn"):
        return "openvpn"
    return value or "openvpn"


def session_key(client_name: str, family: str) -> str:
    """Online status key: one client on one protocol family (never the client name alone)."""
    return f"{(client_name or '').strip().lower()}|{family}"


def db_active_traffic_sessions(db: Session, node_id: int) -> set[str]:
    from app.services.traffic.collector import protocol_type_from_profile

    rows = (
        db.query(TrafficSessionState.common_name, TrafficSessionState.profile)
        .filter(TrafficSessionState.node_id == node_id, TrafficSessionState.is_active.is_(True))
        .distinct()
        .all()
    )
    return {
        session_key(name, protocol_family(protocol_type_from_profile(profile)))
        for name, profile in rows
        if name
    }


def live_active_sessions_for_node(
    db: Session,
    node: Node,
    *,
    ttl_seconds: float | None = _LIVE_ACTIVE_TTL_SECONDS,
) -> set[str]:
    """Live online sessions as ``client|family`` keys, probed per protocol.

    A client counts as online on a protocol only when that protocol's own peer has a fresh
    handshake (OpenVPN: connected now). Old sessions on other protocols stay offline.
    """
    if not is_vpn_node(node):
        return db_active_traffic_sessions(db, node.id)

    ttl = 0.0 if ttl_seconds is None else max(0.0, float(ttl_seconds))
    now = time.monotonic()
    cache_key = int(node.id) + 10**9  # separate namespace from the name-level cache
    if ttl > 0:
        with _live_active_lock:
            entry = _live_active_cache.get(cache_key)
            if entry is not None and now < entry[0]:
                return set(entry[1])

    sessions: set[str] = set()
    try:
        adapter = get_adapter_for_node(node)
        for c in adapter.parse_openvpn_status():
            if c.common_name:
                sessions.add(session_key(c.common_name, "openvpn"))
        for p in adapter.parse_wireguard_status():
            if p.client_name and wireguard_peer_is_online(p):
                sessions.add(session_key(p.client_name, "wireguard"))
        try:
            from app.services.awg2_noc import fetch_awg2_peers_for_adapter
            from app.services.awg3_noc import fetch_awg3_peers_for_adapter
            from app.services.feature_toggles import is_awg2_enabled, is_awg3_enabled

            if is_awg2_enabled(db):
                for p in fetch_awg2_peers_for_adapter(adapter):
                    if p.client_name and wireguard_peer_is_online(p):
                        sessions.add(session_key(p.client_name, "amneziawg2"))
            if is_awg3_enabled(db):
                for p in fetch_awg3_peers_for_adapter(adapter):
                    if p.client_name and wireguard_peer_is_online(p):
                        sessions.add(session_key(p.client_name, "amneziawg3"))
        except Exception:
            pass
    except Exception:
        sessions = set()

    if not sessions:
        sessions = db_active_traffic_sessions(db, node.id)

    if ttl > 0:
        with _live_active_lock:
            _live_active_cache[cache_key] = (now + ttl, frozenset(sessions))
    return sessions
