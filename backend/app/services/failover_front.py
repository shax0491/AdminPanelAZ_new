"""dnat_front strategy: identical-identity pool members behind one front node.

Server-side switching via proxy_agent DNAT (see
``backend/proxy_agent/iptables_dest.py`` ``failover_*`` helpers) — the client
(Keenetic native AmneziaWG GUI, stock Amnezia Android app, anything) holds ONE
static config pointing at ``pool.front_node``; the panel decides which pool
member is actually live and flips the DNAT rule on the front. Client config
never changes.

Deliberately NOT the old HA Sync Group's shared-domain/DNS mechanism: that had
no real health check at all (switching was left to DNS — round-robin/manual
repoint, i.e. random from the operator's point of view), and disbanding a group
left every member with a dangling shared hostname baked into its own setup.
Here the shared identity lives ONLY in the front's iptables rule — member
nodes' own hostnames/configs are never touched — and the switch decision is
the same explicit consecutive-failure-style logic already used by the
client-side watchdog (``router/linux-watchdog/failover-watchdog.sh`` in
panel_auto_reverce), just applied on the front instead of on a device.

Member identity mirroring reuses the already-tested HA full-sync primitive
(``sync_amneziawg2_state_from_primary``) — that half of HA (config
replication: server key + obfuscation + ALL client profiles copied from
primary) was always correct and is exactly what "make this node an
interchangeable clone of the primary" needs; only HA's DNS-based switching
half was the actual problem, and that half is not reused here.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import FailoverPool, FailoverPoolMember, FailoverPoolStrategy, Node, NodeStatus
from app.services.failover_pool import NATIVE_AWG2_IFACE
from app.services.node_manager import get_adapter_for_node, get_proxy_adapter
from app.services.node_sync.vpn_state_sync import sync_amneziawg2_state_from_primary

logger = logging.getLogger(__name__)


class FailoverFrontError(Exception):
    pass


def _resolve_destination_ip(node: Node) -> str:
    """Resolve a pool member's real public IPv4 for the front's DNAT DESTINATION.

    ``Node.host`` is not usable as-is here in two common cases:
    - Local node: stored as ``127.0.0.1`` (the panel talks to it in-process,
      never over the network) — that's a loopback placeholder, not a real
      address a DNAT rule can point external traffic at. Fall back to
      ``Node.name`` (the node's own domain, e.g. set from WIREGUARD_HOST).
    - Remote node registered by hostname rather than raw IP (the normal,
      supported way to add a node) — DNAT needs a literal IPv4, so resolve it.
    """
    candidates = [node.name] if node.is_local else [node.host, node.name]
    last_error: Exception | None = None
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            addr = None
        if addr is not None:
            if addr.version == 4 and not (addr.is_loopback or addr.is_unspecified):
                return str(addr)
            continue
        try:
            resolved = socket.gethostbyname(candidate)
            addr = ipaddress.ip_address(resolved)
        except (socket.gaierror, ValueError) as exc:
            last_error = exc
            continue
        if not (addr.is_loopback or addr.is_unspecified):
            return str(addr)

    raise FailoverFrontError(
        f"Не удалось определить публичный IPv4 для узла {node.name} (host={node.host}): {last_error}"
    )


def front_label(pool: FailoverPool) -> str:
    return f"pool{pool.id}"


def require_front(pool: FailoverPool) -> tuple[Node, int]:
    if pool.strategy != FailoverPoolStrategy.dnat_front:
        raise FailoverFrontError("Пул не в режиме dnat_front")
    if pool.front_node_id is None or pool.front_port is None:
        raise FailoverFrontError("У пула не задан фронт-узел и/или порт")
    front = pool.front_node
    if front is None:
        raise FailoverFrontError("Фронт-узел не найден")
    return front, int(pool.front_port)


def primary_member(pool: FailoverPool) -> FailoverPoolMember | None:
    members = sorted(pool.members, key=lambda m: m.priority)
    return members[0] if members else None


def mirror_member_identity(db: Session, pool: FailoverPool, member: FailoverPoolMember) -> None:
    """Clone the primary member's AmneziaWG 2.0 server identity + ALL client
    profiles onto ``member``.

    Required before ``member`` can ever receive DESTINATION traffic — without
    an identical server key + obfuscation, every client's handshake against it
    fails outright (wrong server, from WireGuard's point of view). Invasive on
    purpose: this OVERWRITES member's own AmneziaWG 2.0 server config and
    client list to match the primary byte-for-byte — only ever run this
    against nodes dedicated to this pool, never a node serving its own
    independent client base under its own identity.
    """
    primary = primary_member(pool)
    if primary is None:
        raise FailoverFrontError("В пуле нет ни одного узла")
    if member.id == primary.id:
        raise FailoverFrontError("Основной узел пула — сам себе источник, клонировать не на что")

    primary_adapter = get_adapter_for_node(primary.node)
    member_adapter = get_adapter_for_node(member.node)
    try:
        sync_amneziawg2_state_from_primary(
            primary_adapter, member_adapter, db=db, replica_node=member.node
        )
    except Exception as exc:
        raise FailoverFrontError(
            f"Клонирование identity на {member.node.name} не удалось: {exc}"
        ) from exc

    # sync_amneziawg2_state_from_primary only LOGS a warning if the runtime
    # apply (awg syncconf) partially fails — by design, since it's shared
    # with the HA feature which tolerates a transient failure there (an admin
    # can re-verify/retry later). For us that would silently leave the live
    # interface out of sync with the config file we just wrote (server keeps
    # whatever peer set it had before — observed once on LV1: an unrelated
    # single stale peer stayed loaded while the config file already had the
    # correct 3 clients) while still reporting "cloned" success. So: apply
    # again here, explicitly, and this time actually fail loudly if it fails.
    runtime = member_adapter.apply_amneziawg2_runtime()
    if not runtime.get("success"):
        errors = runtime.get("errors") or []
        detail = "; ".join(
            str(e.get("stderr") or e.get("error") or e) for e in errors
        ) or "awg syncconf failed"
        raise FailoverFrontError(
            f"Конфиг на {member.node.name} обновлён, но применить к живому интерфейсу не удалось: "
            f"{detail}. Живой интерфейс может отставать от файла — перезапустите "
            f"amneziawg@<interface> на узле или попробуйте клонировать identity ещё раз."
        )

    member.identity_mirrored_at = datetime.utcnow()
    db.commit()


def _node_healthy(node: Node) -> bool:
    """Control-plane reachable (``Node.status``) AND the AWG2 interface this
    pool actually forwards to is confirmed live right now.

    ``Node.status`` alone is not enough — it only means node_agent answered,
    which says nothing about AmneziaWG 2.0 specifically on that box. Found
    for real on LV1: node_agent stayed reachable ("online") while its AWG2
    interface had drifted out of sync with its own config file (see
    ``mirror_member_identity``'s runtime-apply check) — the client would
    have been switched to a server that couldn't actually complete a
    handshake. Best-effort: any failure to reach the monitoring endpoint
    itself (timeout, node just rebooting) counts as unhealthy, not an error
    that blocks the whole switch-check.
    """
    if node.status != NodeStatus.online:
        return False
    try:
        adapter = get_adapter_for_node(node)
        monitoring = adapter.get_awg2_monitoring()
    except Exception:
        return False
    target_label = NATIVE_AWG2_IFACE[:-1] if NATIVE_AWG2_IFACE.endswith("2") else NATIVE_AWG2_IFACE
    for iface in monitoring.get("ifaces") or []:
        if iface.get("name") == target_label:
            return bool(iface.get("up"))
    return False


def _apply_switch_to_target(db: Session, pool: FailoverPool, target: FailoverPoolMember) -> dict:
    """Point the front's DNAT at ``target`` if it isn't already, updating
    ``pool.active_member_id``/``last_switch_at``/``last_switch_error``.

    Shared by the auto health-based picker (``evaluate_and_switch``) and the
    explicit admin override (``force_switch_member``) — both just need to
    agree on a target member first, this part (resolve IP, compare against
    current DESTINATION, flip if needed) is identical either way.

    Best-effort: never raises past this function. Any failure lands in
    ``pool.last_switch_error`` — an unreachable front/panel must never crash
    a caller, same "observer, not a hard dependency" principle already
    applied to the client watchdog's optional status reporting.
    """
    result: dict = {"pool_id": pool.id, "switched": False, "active_member_id": pool.active_member_id, "errors": []}
    try:
        front, port = require_front(pool)
    except FailoverFrontError as exc:
        pool.last_switch_error = str(exc)
        db.commit()
        result["errors"].append(str(exc))
        return result

    label = front_label(pool)
    adapter = get_proxy_adapter(front)

    try:
        target_ip = _resolve_destination_ip(target.node)
    except FailoverFrontError as exc:
        pool.last_switch_error = str(exc)
        db.commit()
        result["errors"].append(str(exc))
        return result

    try:
        status_now = adapter.failover_status(label, port)
    except Exception as exc:
        pool.last_switch_error = f"Фронт недоступен: {exc}"
        db.commit()
        result["errors"].append(pool.last_switch_error)
        return result

    current_ip = status_now.get("destination_ip")
    if current_ip == target_ip:
        if pool.active_member_id != target.id:
            pool.active_member_id = target.id
            pool.last_switch_error = None
            db.commit()
        result["active_member_id"] = target.id
        return result

    try:
        adapter.failover_set_destination(label, port, target_ip)
    except Exception as exc:
        pool.last_switch_error = f"Не удалось переключить фронт: {exc}"
        db.commit()
        result["errors"].append(pool.last_switch_error)
        return result

    pool.active_member_id = target.id
    pool.last_switch_at = datetime.utcnow()
    pool.last_switch_error = None
    db.commit()
    result["switched"] = True
    result["active_member_id"] = target.id
    return result


def _eligible_candidates(pool: FailoverPool) -> list[FailoverPoolMember]:
    """Members that could legally receive traffic right now: the primary
    (source of identity, always eligible) or any member with identity already
    mirrored from it — never a member whose identity hasn't been cloned yet,
    switching there would just break every client's handshake."""
    members = sorted(pool.members, key=lambda m: m.priority)
    primary = members[0] if members else None
    if primary is None:
        return []
    return [m for m in members if m.id == primary.id or m.identity_mirrored_at is not None]


def evaluate_and_switch(db: Session, pool: FailoverPool) -> dict:
    """Pick the highest-priority HEALTHY, identity-ready member and point the
    front's DNAT at it, if that differs from what is live now.

    This is the automatic policy — ignores anything currently unhealthy
    (``Node.status``) even if an admin would prefer it. To force a specific
    member regardless of health (e.g. testing, or overriding a health-check
    you don't trust), use ``force_switch_member`` instead.
    """
    eligible = _eligible_candidates(pool)
    candidates = [m for m in eligible if _node_healthy(m.node)]

    if not candidates:
        pool.last_switch_error = "Нет здоровых узлов с готовой identity (клонируйте identity на резервные узлы)"
        db.commit()
        return {"pool_id": pool.id, "switched": False, "active_member_id": pool.active_member_id, "errors": [pool.last_switch_error]}

    return _apply_switch_to_target(db, pool, candidates[0])


def force_switch_member(db: Session, pool: FailoverPool, member: FailoverPoolMember) -> dict:
    """Explicitly point the front at ``member``, regardless of its current
    health status — manual override for when the admin wants direct control
    (testing, or overriding a health-check that's a false positive/negative).

    Still refuses a member with no cloned identity (mirror_member_identity
    not run yet) — that's not a policy choice, switching there would just
    break every client's handshake outright.
    """
    if member.pool_id != pool.id:
        raise FailoverFrontError("Участник не принадлежит этому пулу")
    eligible_ids = {m.id for m in _eligible_candidates(pool)}
    if member.id not in eligible_ids:
        raise FailoverFrontError(
            f"У узла {member.node.name} ещё нет склонированной identity — сначала «Клонировать identity»"
        )
    return _apply_switch_to_target(db, pool, member)


def teardown_front(pool: FailoverPool) -> None:
    """Remove the front's DNAT rule for this pool (pool deleted / front detached).

    Best-effort — never raises. An unreachable front must never block deleting
    the pool in the panel; worst case a stale rule is left on the front and can
    be cleaned up manually (label is ``pool<id>``, see docs).
    """
    if pool.front_node_id is None or pool.front_port is None or pool.front_node is None:
        return
    try:
        adapter = get_proxy_adapter(pool.front_node)
        adapter.failover_teardown(front_label(pool), int(pool.front_port))
    except Exception as exc:
        logger.warning("Failover front teardown failed for pool %s: %s", pool.id, exc)
