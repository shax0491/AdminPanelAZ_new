from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Node, ReverseTunnelLink
from app.services.crypto import encrypt_secret
from app.services.node_manager import get_adapter_for_node, get_proxy_adapter
from app.services.reverse_tunnel import generate_wg_keypair


def _link_for(db: Session, relay: Node) -> ReverseTunnelLink | None:
    return db.query(ReverseTunnelLink).filter(ReverseTunnelLink.proxy_node_id == relay.id).first()


def _new_link(db: Session, relay: Node, exit_node: Node) -> ReverseTunnelLink:
    link = _link_for(db, relay)
    if link is None:
        link = ReverseTunnelLink(proxy_node_id=relay.id, exit_node_id=exit_node.id)
        db.add(link)
    link.exit_node_id = exit_node.id
    link.listen_port = 52000 + relay.id
    third_octet = relay.id // 128
    host_base = 2 * (relay.id % 128)
    relay_host = host_base - 1
    exit_host = host_base
    link.tunnel_ipv4 = f"10.77.{third_octet}.{exit_host}"
    link.tunnel_ipv6 = f"fd77::{relay.id:x}:2"
    link.state = "provisioning"
    link.last_error = None
    db.flush()
    return link


def provision_reverse_tunnel(db: Session, relay: Node) -> ReverseTunnelLink:
    if relay.node_kind != "proxy" or not relay.linked_vpn_node_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reverse Tunnel требует proxy-узел со связанной VPN-нодой",
        )
    existing = _link_for(db, relay)
    if existing and existing.state in {"standby", "active"}:
        return existing
    exit_node = db.query(Node).filter(Node.id == relay.linked_vpn_node_id).first()
    if not exit_node or exit_node.node_kind != "vpn":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Связанная VPN-нода не найдена")
    link = _new_link(db, relay, exit_node)
    relay_keys = generate_wg_keypair()
    exit_keys = generate_wg_keypair()
    third_octet = relay.id // 128
    relay_tunnel4 = f"10.77.{third_octet}.{2 * (relay.id % 128) - 1}"
    relay_tunnel6 = f"fd77::{relay.id:x}:1"
    try:
        relay_adapter = get_proxy_adapter(relay)
        exit_adapter = get_adapter_for_node(exit_node)
        relay_adapter.configure_reverse_relay(
            {
                "link_id": relay.id,
                "private_key": relay_keys["private_key"],
                "address4": f"{relay_tunnel4}/32",
                "address6": f"{relay_tunnel6}/128",
                "listen_port": link.listen_port,
                "peer_public_key": exit_keys["public_key"],
                "peer_allowed_ips": f"{link.tunnel_ipv4}/32, {link.tunnel_ipv6}/128",
            }
        )
        exit_adapter.configure_reverse_exit(
            {
                "link_id": relay.id,
                "private_key": exit_keys["private_key"],
                "address4": f"{link.tunnel_ipv4}/32",
                "address6": f"{link.tunnel_ipv6}/128",
                "peer_public_key": relay_keys["public_key"],
                "relay_host": relay.host,
                "listen_port": link.listen_port,
                "allowed_ips": f"{relay_tunnel4}/32, {relay_tunnel6}/128",
            }
        )
        direct_ipv4 = exit_node.ipv4_address or relay.destination_ip or exit_node.host
        relay_adapter.configure_reverse_watchdog(
            {
                "link_id": relay.id,
                "enabled": True,
                "direct_ipv4": direct_ipv4,
                "direct_ipv6": exit_node.ipv6_address,
                "tunnel_ipv4": link.tunnel_ipv4,
            }
        )
        secret = get_settings().secret_key
        link.relay_private_key_encrypted = encrypt_secret(relay_keys["private_key"], secret)
        link.exit_private_key_encrypted = encrypt_secret(exit_keys["private_key"], secret)
        link.relay_public_key = relay_keys["public_key"]
        link.exit_public_key = exit_keys["public_key"]
        link.state = "standby"
        relay.reverse_state = "standby"
        db.add_all([link, relay])
        db.commit()
        db.refresh(link)
        return link
    except Exception as exc:
        link.state = "failed"
        link.last_error = str(exc)[:2000]
        relay.reverse_state = "failed"
        db.add_all([link, relay])
        db.commit()
        raise


def activate_reverse_tunnel(db: Session, relay: Node) -> ReverseTunnelLink:
    link = _link_for(db, relay) or provision_reverse_tunnel(db, relay)
    if link.state != "active":
        get_proxy_adapter(relay).set_destination(link.tunnel_ipv4)
    link.state = "active"
    link.last_error = None
    relay.reverse_state = "active"
    relay.transport_mode = "reverse"
    link.updated_at = datetime.utcnow()
    db.add_all([link, relay])
    db.commit()
    db.refresh(link)
    return link


def reverse_tunnel_status(db: Session, relay: Node) -> dict[str, Any]:
    link = _link_for(db, relay)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reverse Tunnel не настроен")
    exit_node = db.query(Node).filter(Node.id == link.exit_node_id).first()
    return {
        "link_id": link.id,
        "state": link.state,
        "exit": get_adapter_for_node(exit_node).reverse_tunnel_status(relay.id),
        "watchdog": get_proxy_adapter(relay).reverse_watchdog_status(relay.id),
    }
