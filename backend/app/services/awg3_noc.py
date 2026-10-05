"""Map AmneziaWG 3.0 monitoring clients into WireGuardPeer for traffic and online surfaces.

Same mapping as AmneziaWG 2.0 (see awg2_noc): the node reports handshake age per client,
and the peer's interface label carries the mode, so the collector names profiles
`antizapret-awg3` / `vpn-awg3` exactly like `antizapret-awg2` / `vpn-awg2`.
"""

from __future__ import annotations

from typing import Any

from app.schemas import WireGuardPeer
from app.services.awg2_noc import awg2_client_to_peer


def peers_from_awg3_monitoring(payload: dict) -> list[WireGuardPeer]:
    clients = payload.get("clients") or []
    if not isinstance(clients, list):
        return []
    peers: list[WireGuardPeer] = []
    for client in clients:
        if not isinstance(client, dict):
            continue
        # split -> "antizapret3", full -> "vpn3": the collector routes on the "antizapret" substring.
        label = "antizapret3" if client.get("mode", "split") == "split" else "vpn3"
        peers.append(awg2_client_to_peer({**client, "iface": label}))
    return peers


def fetch_awg3_peers_for_adapter(adapter: Any) -> list[WireGuardPeer]:
    """AWG 3.0 peers from a node adapter; [] on missing interface or errors."""
    try:
        return peers_from_awg3_monitoring(adapter.awg3_monitoring())
    except Exception:
        return []
