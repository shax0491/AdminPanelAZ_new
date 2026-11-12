from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Node, ReverseTunnelLink
from app.services.node_manager import get_adapter_for_node, get_proxy_adapter
from app.services.reverse_tunnel import pick_direct_ipv4

TRANSPORT_MODES = {"auto", "force_ipv6", "force_ipv4", "reverse"}
EGRESS_MODES = {"native", "proton", "warp", "ipv4_only", "ipv6_prefer"}
MH_NODES_FILE = Path(os.environ.get("MH_NODES_FILE", "/opt/mh-app/nodes.json"))


def _sync_mh_registry(node: Node) -> None:
    if node.node_kind != "vpn" or not MH_NODES_FILE.is_file():
        return
    data = json.loads(MH_NODES_FILE.read_text(encoding="utf-8"))
    changed = False
    for item in data.get("nodes", []):
        if item.get("key") != node.name:
            continue
        item["ipv4_address"] = node.ipv4_address or node.host
        if node.has_ipv6 and node.ipv6_address:
            item["ipv6_address"] = node.ipv6_address
        else:
            item.pop("ipv6_address", None)
            item.pop("ipv6", None)
        changed = True
    if changed:
        temporary = MH_NODES_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, MH_NODES_FILE.stat().st_mode & 0o777)
        os.replace(temporary, MH_NODES_FILE)


def _adapter(node: Node):
    if node.node_kind in {"proxy", "mtproxy"}:
        return get_proxy_adapter(node)
    return get_adapter_for_node(node)


def _probe_state(probe: Any) -> tuple[str, float | None]:
    if not isinstance(probe, dict) or not isinstance(probe.get("reachable"), bool):
        return "unknown", None
    latency = probe.get("latency_ms")
    return ("online" if probe["reachable"] else "offline"), (float(latency) if latency is not None else None)


def audit_node_network(db: Session, node: Node) -> dict[str, Any]:
    result = _adapter(node).dual_stack_capabilities()
    for field in (
        "has_ipv4",
        "has_ipv6",
        "ipv4_address",
        "ipv6_address",
        "ipv6_subnet_prefix",
        "ipv6_network",
        "ipv6_gateway",
        "network_interface",
        "ipv6_rotation_supported",
    ):
        if field in result:
            setattr(node, field, result[field])
    node.ipv4_probe_status, node.ipv4_latency_ms = _probe_state(result.get("ipv4_probe"))
    node.ipv6_probe_status, node.ipv6_latency_ms = _probe_state(result.get("ipv6_probe"))
    audited_at = result.get("audited_at")
    try:
        node.last_network_audit_at = datetime.fromisoformat(str(audited_at).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        node.last_network_audit_at = datetime.utcnow()
    db.add(node)
    db.commit()
    db.refresh(node)
    _sync_mh_registry(node)
    return result


def probe_relay_link(db: Session, relay: Node) -> None:
    if relay.node_kind != "proxy" or not relay.linked_vpn_node_id:
        return
    exit_node = db.query(Node).filter(Node.id == relay.linked_vpn_node_id).first()
    if not exit_node:
        return
    adapter = get_proxy_adapter(relay)
    target4 = exit_node.ipv4_address or exit_node.host
    result4 = adapter.probe_dual_stack(4, target4)
    relay.ipv4_probe_status, relay.ipv4_latency_ms = _probe_state(result4)
    if exit_node.ipv6_address:
        result6 = adapter.probe_dual_stack(6, exit_node.ipv6_address)
        relay.ipv6_probe_status, relay.ipv6_latency_ms = _probe_state(result6)
    else:
        relay.ipv6_probe_status, relay.ipv6_latency_ms = "unknown", None
    relay.last_network_audit_at = datetime.utcnow()
    db.add(relay)
    db.commit()
    db.refresh(relay)


def set_node_transport_mode(db: Session, node: Node, mode: str) -> Node:
    if mode not in TRANSPORT_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый режим Dual-Stack")
    if mode == "reverse":
        if node.node_kind != "proxy":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reverse Tunnel задаётся на связке proxy-релея с VPN-нодой",
            )
        from app.services.reverse_panel import activate_reverse_tunnel

        activate_reverse_tunnel(db, node)
        return node
    if node.node_kind == "vpn":
        _adapter(node).set_dual_stack_transport(mode)
    elif node.node_kind == "proxy":
        if not node.linked_vpn_node_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proxy-узел не связан с VPN-нодой")
        exit_node = db.query(Node).filter(Node.id == node.linked_vpn_node_id).first()
        if not exit_node:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Связанная VPN-нода не найдена")
        link = db.query(ReverseTunnelLink).filter(ReverseTunnelLink.proxy_node_id == node.id).first()
        try:
            direct_ipv4 = pick_direct_ipv4([exit_node.ipv4_address, node.destination_ip, exit_node.host])
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        # ключ watchdog = id proxy-узла: так же, как link_id в reverse_panel (relay.id)
        get_proxy_adapter(node).set_dual_stack_transport(
            mode,
            link_id=node.id,
            direct_ipv4=direct_ipv4,
            direct_ipv6=exit_node.ipv6_address,
            tunnel_ipv4=link.tunnel_ipv4 if link else None,
        )
        if link:
            link.state = "standby"
            node.reverse_state = "standby"
            db.add(link)
    node.transport_mode = mode
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def rotate_node_ipv6(db: Session, node: Node) -> dict[str, Any]:
    if node.node_kind != "vpn" or not node.ipv6_rotation_supported:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ротация доступна только VPN-узлу с подтверждённой публичной IPv6 /64",
        )
    result = _adapter(node).rotate_ipv6()
    node.ipv6_address = result["ipv6_address"]
    node.ipv6_probe_status = "unknown"
    node.ipv6_latency_ms = None
    node.last_network_audit_at = datetime.utcnow()
    db.add(node)
    db.commit()
    db.refresh(node)
    _sync_mh_registry(node)
    return result


def set_node_egress_mode(db: Session, node: Node, mode: str) -> Node:
    if mode not in EGRESS_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый egress-режим")
    if node.node_kind != "vpn":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Egress настраивается на VPN-узле")
    adapter = _adapter(node)
    if mode in {"native", "proton", "warp"}:
        # селектор карточки = IPV6_EGRESS (выход IPv6 антизапрета); режимы WARP/Proton туннелей (ANTIZAPRET_WARP/VPN_WARP)
        # настраиваются отдельно на вкладке «Конфиг антизапрета» и здесь не меняются
        adapter.update_antizapret_settings({"IPV6_EGRESS": mode})
        adapter.restart_service("antizapret")
    elif mode == "ipv4_only":
        adapter.set_dual_stack_transport("force_ipv4")
    else:
        adapter.set_dual_stack_transport("auto")
    node.egress_mode = mode
    db.add(node)
    db.commit()
    db.refresh(node)
    return node
