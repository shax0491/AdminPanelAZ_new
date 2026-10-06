"""Map AmneziaWG 3 monitoring clients into WireGuardPeer for traffic and online surfaces.

Same mapping as AmneziaWG 2 (see awg2_noc): the node reports handshake age per client,
and the peer's interface label carries the mode, so the collector names profiles
`antizapret-awg3` / `vpn-awg3` exactly like `antizapret-awg2` / `vpn-awg2`.
"""

from __future__ import annotations

from typing import Any

from app.schemas import WireGuardPeer
from app.services.awg3_clients import base_name
from app.services.native_awg3_runtime import AWG3_PORT
from app.services.awg2_noc import awg2_client_to_peer


def peers_from_awg3_monitoring(payload: dict) -> list[WireGuardPeer]:
    clients = payload.get("clients") or []
    if not isinstance(clients, list):
        return []
    peers: list[WireGuardPeer] = []
    for client in clients:
        if not isinstance(client, dict):
            continue
        # split -> "antizapret", full -> "vpn": the collector routes on the "antizapret" substring.
        label = "antizapret" if client.get("mode", "split") == "split" else "vpn"
        peers.append(awg2_client_to_peer({**client, "iface": label, "name": base_name(client.get("name") or "")}))
    return peers


def awg3_monitoring_view(raw: dict) -> dict:
    """AWG 2-shaped monitoring payload for the AWG 3 page (same cards, table and filters).

    Interfaces are logical: split peers live on ``antizapret`` and full peers on ``vpn`` (the names AWG 2 uses), both
    served by the one physical ``awg1``. Clients carry the same fields the AWG 2 table reads.
    """
    clients_raw = raw.get("clients") or []
    clients = []
    for c in clients_raw:
        if not isinstance(c, dict):
            continue
        split = c.get("mode", "split") == "split"
        clients.append({
            **c,
            "name": base_name(c.get("name") or ""),
            "iface": "antizapret" if split else "vpn",
            "online": bool(c.get("online")),
        })
    ifaces_raw = raw.get("ifaces") or {}
    awg1 = next(iter(ifaces_raw.values()), {}) if isinstance(ifaces_raw, dict) else {}
    up = bool(awg1.get("up")) if isinstance(awg1, dict) else False
    logical = [
        ("antizapret", "10.9.0.0/24", "split"),
        ("vpn", "10.9.1.0/24", "full"),
    ]
    ifaces = [
        {
            "name": name,
            "port": str(AWG3_PORT["split"]),
            "subnet": subnet,
            "peer_count": sum(1 for c in clients if c["iface"] == name),
            "up": up,
        }
        for name, subnet, _mode in logical
    ]
    return {"ifaces": ifaces, "clients": clients, "stats_available": False}


def fetch_awg3_peers_for_adapter(adapter: Any) -> list[WireGuardPeer]:
    """AWG 3 peers from a node adapter; [] on missing interface or errors."""
    try:
        return peers_from_awg3_monitoring(adapter.awg3_monitoring())
    except Exception:
        return []
