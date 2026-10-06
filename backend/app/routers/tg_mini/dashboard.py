"""Dashboard endpoints for Telegram Mini App."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_tg_mini_admin
from app.database import get_db
from app.models import User, VpnConfig
from app.services.awg2_noc import fetch_awg2_peers_for_adapter
from app.services.awg3_noc import fetch_awg3_peers_for_adapter
from app.services.feature_toggles import is_awg3_enabled
from app.services.node_manager import get_active_adapter, get_active_node
from app.services.wireguard_status import wireguard_peer_is_online

router = APIRouter()


@router.get("/dashboard")
def mini_dashboard(current_user: User = Depends(require_tg_mini_admin), db: Session = Depends(get_db)):
    adapter = get_active_adapter(db)
    ovpn = adapter.parse_openvpn_status()
    wg = adapter.parse_wireguard_status()
    wg_online = [p for p in wg if wireguard_peer_is_online(p)]
    try:
        amneziawg2_peers = fetch_awg2_peers_for_adapter(adapter)
    except Exception:
        amneziawg2_peers = []
    amneziawg2_online = [p for p in amneziawg2_peers if wireguard_peer_is_online(p)]
    try:
        amneziawg3_peers = fetch_awg3_peers_for_adapter(adapter) if is_awg3_enabled(db) else []
    except Exception:
        amneziawg3_peers = []
    amneziawg3_online = [p for p in amneziawg3_peers if wireguard_peer_is_online(p)]
    node = get_active_node(db)
    configs = db.query(VpnConfig).filter(VpnConfig.node_id == node.id).count()
    return {
        "total_configs": configs,
        "connected_openvpn": len(ovpn),
        "connected_wireguard": len(wg_online),
        "total_wireguard_peers": len(wg),
        "connected_amneziawg2": len(amneziawg2_online),
        "total_amneziawg2_peers": len(amneziawg2_peers),
        "connected_amneziawg3": len(amneziawg3_online),
        "total_amneziawg3_peers": len(amneziawg3_peers),
        "server_ip": adapter.get_server_ip(),
        "openvpn_clients": [c.model_dump() if hasattr(c, "model_dump") else c.__dict__ for c in ovpn[:20]],
        "wireguard_peers": [
            {
                "client_name": getattr(p, "client_name", None),
                "public_key": getattr(p, "public_key", ""),
                "transfer_rx": getattr(p, "transfer_rx", 0),
                "transfer_tx": getattr(p, "transfer_tx", 0),
                "latest_handshake": getattr(p, "latest_handshake", None),
            }
            for p in wg_online[:20]
        ],
        "amneziawg2_peers": [
            {
                "client_name": getattr(p, "client_name", None),
                "public_key": getattr(p, "public_key", ""),
                "transfer_rx": getattr(p, "transfer_rx", 0),
                "transfer_tx": getattr(p, "transfer_tx", 0),
                "latest_handshake": getattr(p, "latest_handshake", None),
            }
            for p in amneziawg2_online[:20]
        ],
        "amneziawg3_peers": [
            {
                "client_name": getattr(p, "client_name", None),
                "public_key": getattr(p, "public_key", ""),
                "transfer_rx": getattr(p, "transfer_rx", 0),
                "transfer_tx": getattr(p, "transfer_tx", 0),
                "latest_handshake": getattr(p, "latest_handshake", None),
            }
            for p in amneziawg3_online[:20]
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }
