"""Failover pool: peer-sync + device-facing config generation for client-side
AmneziaWG 2.0 failover (AZ AutoSwitch on Android, the OpenWrt/Linux router watchdog —
see the separate `panel_auto_reverce` repo for those clients).

Deliberately NOT built on the HA Sync Group machinery (`node_sync/*`) — that does
byte-identical wipe-and-replace of a whole node's crypto state, tied to a "group"
whose disbanding drops all state. This module only ever *appends* one client's peer
to another node's already-independent AmneziaWG 2.0 config, allocating a fresh IP in
that node's own subnet (never assumes two nodes' address pools line up) — reuses the
same read/write/apply-runtime primitives HA sync uses, but skips its lifecycle
entirely. A pool (`FailoverPool`) can be deleted without touching any node's config.

Only the "vpn" (full-tunnel) AmneziaWG 2.0 interface is supported for now — that's
the natural fit for "give me a complete, working failover VPN". The narrower
"antizapret" (blocked-domains-only) interface can be added later the same way.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import FailoverClientLink, FailoverPool, FailoverPoolMember, Node
from app.services.node_manager import get_adapter_for_node

NATIVE_AWG2_IFACE = "vpn"  # full-tunnel interface only, for now
NATIVE_AWG2_OBFUSCATION_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4")
NATIVE_AWG2_CLIENT_MTU = 1280

_CLIENT_BLOCK_RE = (
    r"^# Client = {name}$\r?\n"
    r"(?:# PrivateKey = (?P<privkey>.*)\r?\n)?"
    r"\[Peer\]\r?\n"
    r"PublicKey = (?P<pubkey>.*)\r?\n"
    r"PresharedKey = (?P<psk>.*)\r?\n"
    r"AllowedIPs = (?P<ip>[0-9.]+)/32\r?\n?"
)


@dataclass
class PeerSpec:
    client_name: str
    public_key: str
    preshared_key: str
    private_key: str | None


class FailoverPoolError(Exception):
    """Raised for pool/link/sync problems the API layer maps to 4xx responses."""


def new_device_token() -> str:
    return secrets.token_urlsafe(24)


def _extract_peer_spec(conf_text: str, client_name: str) -> PeerSpec | None:
    pattern = _CLIENT_BLOCK_RE.format(name=re.escape(client_name))
    match = re.search(pattern, conf_text, re.MULTILINE)
    if not match:
        return None
    return PeerSpec(
        client_name=client_name,
        public_key=match.group("pubkey").strip(),
        preshared_key=match.group("psk").strip(),
        private_key=(match.group("privkey").strip() if match.group("privkey") else None),
    )


def _peer_exists(conf_text: str, client_name: str) -> bool:
    return re.search(rf"^# Client = {re.escape(client_name)}$", conf_text, re.MULTILINE) is not None


def _allocate_ip(conf_text: str) -> str:
    match = re.search(r"^Address\s*=\s*([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+", conf_text, re.MULTILINE)
    if not match:
        raise FailoverPoolError("В серверном конфиге не найден Address — интерфейс не инициализирован на этом узле")
    base = match.group(1)
    for i in range(2, 255):
        candidate = f"{base}.{i}"
        if candidate not in conf_text:
            return candidate
    raise FailoverPoolError("Подсеть AmneziaWG 2.0 на этом узле исчерпана (253 клиента)")


def _append_peer_block(conf_text: str, spec: PeerSpec, ip: str) -> str:
    lines = [f"# Client = {spec.client_name}"]
    if spec.private_key:
        lines.append(f"# PrivateKey = {spec.private_key}")
    lines += [
        "[Peer]",
        f"PublicKey = {spec.public_key}",
        f"PresharedKey = {spec.preshared_key}",
        f"AllowedIPs = {ip}/32",
    ]
    block = "\n".join(lines)
    sep = "" if (not conf_text or conf_text.endswith("\n")) else "\n"
    return f"{conf_text}{sep}\n{block}\n"


def sync_client_peer_to_pool(db: Session, pool: FailoverPool, client_name: str) -> dict:
    """Push client_name's AWG2 peer from the link's primary node onto every other
    pool member, allocating a fresh IP on each target. Best-effort per member/
    interface — one member failing doesn't stop the others."""
    link = (
        db.query(FailoverClientLink)
        .filter(FailoverClientLink.pool_id == pool.id, FailoverClientLink.client_name == client_name)
        .first()
    )
    if link is None:
        raise FailoverPoolError(f"Клиент '{client_name}' не привязан к пулу '{pool.name}'")

    primary_node = db.query(Node).filter(Node.id == link.primary_node_id).first()
    if primary_node is None:
        raise FailoverPoolError("Основной узел клиента не найден")
    primary_adapter = get_adapter_for_node(primary_node)

    source_conf = primary_adapter.read_amneziawg2_server_config(NATIVE_AWG2_IFACE)
    spec = _extract_peer_spec(source_conf, client_name)
    if spec is None:
        raise FailoverPoolError(
            f"У клиента '{client_name}' нет пира AmneziaWG 2.0 (vpn) на основном узле — "
            "сначала создайте клиента с протоколом AmneziaWG 2.0"
        )

    members = (
        db.query(FailoverPoolMember)
        .filter(FailoverPoolMember.pool_id == pool.id, FailoverPoolMember.node_id != primary_node.id)
        .all()
    )

    synced: list[str] = []
    errors: list[str] = []
    for member in members:
        target_node = db.query(Node).filter(Node.id == member.node_id).first()
        if target_node is None:
            continue
        try:
            target_adapter = get_adapter_for_node(target_node)
            target_conf = target_adapter.read_amneziawg2_server_config(NATIVE_AWG2_IFACE)
            if not _peer_exists(target_conf, client_name):
                ip = _allocate_ip(target_conf)
                new_conf = _append_peer_block(target_conf, spec, ip)
                target_adapter.write_amneziawg2_server_config(NATIVE_AWG2_IFACE, new_conf)
            target_adapter.apply_amneziawg2_runtime()
            synced.append(target_node.name)
        except Exception as exc:  # noqa: BLE001 — collect, keep syncing the rest
            errors.append(f"{target_node.name}: {exc}")

    link.last_synced_at = datetime.utcnow()
    link.last_sync_error = "; ".join(errors) if errors else None
    db.commit()

    return {"synced_nodes": synced, "errors": errors}


def _read_obfuscation(conf_text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for raw in conf_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[Peer]"):
            break
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in NATIVE_AWG2_OBFUSCATION_KEYS:
            params[key] = value.strip()
    return params


def build_member_client_conf(db: Session, pool: FailoverPool, client_name: str, member: FailoverPoolMember) -> str:
    """Render a complete, ready-to-import AmneziaWG 2.0 client .conf for one pool
    member — same client identity (private/preshared key) as on every other member,
    that member's own server public key / endpoint / obfuscation / allocated IP."""
    link = (
        db.query(FailoverClientLink)
        .filter(FailoverClientLink.pool_id == pool.id, FailoverClientLink.client_name == client_name)
        .first()
    )
    if link is None:
        raise FailoverPoolError(f"Клиент '{client_name}' не привязан к пулу '{pool.name}'")

    primary_node = db.query(Node).filter(Node.id == link.primary_node_id).first()
    if primary_node is None:
        raise FailoverPoolError("Основной узел клиента не найден")
    primary_adapter = get_adapter_for_node(primary_node)
    primary_conf = primary_adapter.read_amneziawg2_server_config(NATIVE_AWG2_IFACE)
    spec = _extract_peer_spec(primary_conf, client_name)
    if spec is None or not spec.private_key:
        raise FailoverPoolError(
            f"Нет сохранённого приватного ключа клиента '{client_name}' на основном узле — "
            "профиль нельзя собрать (client.sh хранит его в комментарии рядом с пиром)"
        )

    target_node = db.query(Node).filter(Node.id == member.node_id).first()
    if target_node is None:
        raise FailoverPoolError("Узел участника пула не найден")
    target_adapter = get_adapter_for_node(target_node)
    target_conf = target_adapter.read_amneziawg2_server_config(NATIVE_AWG2_IFACE)

    target_spec = _extract_peer_spec(target_conf, client_name)
    if target_spec is None:
        raise FailoverPoolError(
            f"Клиент '{client_name}' ещё не синхронизирован на узел '{target_node.name}' — "
            "сначала вызовите синхронизацию пира"
        )
    ip_match = re.search(
        rf"^# Client = {re.escape(client_name)}$\r?\n(?:# PrivateKey = .*\r?\n)?\[Peer\]\r?\n"
        rf"PublicKey = .*\r?\nPresharedKey = .*\r?\nAllowedIPs = ([0-9.]+)/32",
        target_conf,
        re.MULTILINE,
    )
    client_ip = ip_match.group(1) if ip_match else None
    if not client_ip:
        raise FailoverPoolError(f"Не удалось определить выданный IP на узле '{target_node.name}'")

    server_key_text = target_adapter.read_amneziawg2_server_key()
    server_pubkey_match = re.search(r"^PUBLIC_KEY=(.*)$", server_key_text, re.MULTILINE)
    if not server_pubkey_match:
        raise FailoverPoolError(f"Не удалось прочитать публичный ключ сервера узла '{target_node.name}'")
    server_pubkey = server_pubkey_match.group(1).strip()

    obfuscation = _read_obfuscation(target_conf)
    endpoint_host = (member.label or target_node.host or "").strip() or target_node.host
    endpoint_port_match = re.search(r"^ListenPort\s*=\s*(\d+)", target_conf, re.MULTILINE)
    endpoint_port = endpoint_port_match.group(1) if endpoint_port_match else "53443"

    lines = [
        "[Interface]",
        f"PrivateKey = {spec.private_key}",
        f"Address = {client_ip}/32",
        "DNS = 1.1.1.1, 1.0.0.1",
        f"MTU = {NATIVE_AWG2_CLIENT_MTU}",
    ]
    for key in NATIVE_AWG2_OBFUSCATION_KEYS:
        if key in obfuscation:
            lines.append(f"{key} = {obfuscation[key]}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {server_pubkey}",
        f"PresharedKey = {spec.preshared_key}",
        f"Endpoint = {endpoint_host}:{endpoint_port}",
        "AllowedIPs = 0.0.0.0/0",
        "PersistentKeepalive = 15",
    ]
    return "\n".join(lines) + "\n"
