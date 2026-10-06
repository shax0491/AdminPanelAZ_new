"""AmneziaWG 3 access policy: deadline, temporary and permanent blocks.

Same states and block reasons as AmneziaWG 2 (see access_policy.py), without traffic
limits. A block is applied on the node: the peer is removed from awg1 on suspend and
restored from the node registry on unsuspend (awg3_clients.suspend_client/unsuspend_client).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import AmneziaWg3AccessPolicy, Node
from app.services.access_policy import _as_utc, _now
from app.services.node_manager import get_adapter_for_node

RuntimeApply = Callable[[Node, str, bool], None]


def _adapter_apply(node: Node, client_name: str, is_blocked: bool) -> None:
    adapter = get_adapter_for_node(node)
    if is_blocked:
        adapter.awg3_suspend_client(client_name)
    else:
        adapter.awg3_unsuspend_client(client_name)


def _normalize(client_name: str) -> str:
    return client_name.strip()


def get_row(db: Session, node_id: int, client_name: str, *, create: bool = False) -> AmneziaWg3AccessPolicy | None:
    normalized = _normalize(client_name)
    row = db.query(AmneziaWg3AccessPolicy).filter_by(node_id=node_id, client_name=normalized).first()
    if row is None and create:
        row = AmneziaWg3AccessPolicy(node_id=node_id, client_name=normalized)
        db.add(row)
        db.flush()
    return row


def _target_reason(state: dict[str, Any]) -> str | None:
    mode = state["block_mode"]
    return {
        "access_expired": "access_expired",
        "permanent": "manual_permanent",
        "temp": "manual_temp",
    }.get(mode)


def state_of(row: AmneziaWg3AccessPolicy, now: datetime | None = None) -> dict[str, Any]:
    now = _as_utc(now) or _now()
    access_until = _as_utc(row.access_until)
    access_expired = bool(access_until and access_until <= now)
    block_until = _as_utc(row.block_until)
    temp = bool(row.is_temp_blocked and block_until and block_until > now)
    perm = bool(row.is_permanent_blocked)
    if perm:
        mode = "permanent"
    elif access_expired:
        mode = "access_expired"
    elif temp:
        mode = "temp"
    else:
        mode = "none"
    return {
        "is_blocked": access_expired or temp or perm,
        "block_mode": mode,
        "access_expired": access_expired,
        "access_until": access_until.isoformat() if access_until else None,
        "blocked_days_left": (block_until - now).days if temp and block_until else None,
        "block_duration_days": row.block_days,
        "block_until": block_until.strftime("%Y-%m-%d %H:%M:%S") if block_until else None,
    }


def policy_view(db: Session, node_id: int, client_name: str, now: datetime | None = None) -> dict[str, Any]:
    """Block state for the client list (same keys the UI reads for AmneziaWG 2)."""
    row = get_row(db, node_id, client_name)
    if row is None:
        return {
            "is_blocked": False,
            "block_mode": "none",
            "block_reason": None,
            "access_expired": False,
            "access_until": None,
            "blocked_days_left": None,
            "block_duration_days": None,
            "block_until": None,
            "traffic_limit_exceeded": False,
        }
    state = state_of(row, now)
    return {**state, "block_reason": row.block_reason, "traffic_limit_exceeded": False}


def reconcile(
    db: Session,
    node: Node,
    client_name: str,
    *,
    force_runtime: bool = False,
    apply: RuntimeApply = _adapter_apply,
) -> dict[str, Any] | None:
    """Recompute the block from deadline and manual flags; push the change to the node."""
    normalized = _normalize(client_name)
    row = get_row(db, node.id, normalized)
    if row is None:
        return None
    now = _now()
    before_blocked = bool(state_of(row, now)["is_blocked"])
    before_reason = row.block_reason

    if row.is_temp_blocked and row.block_until and _as_utc(row.block_until) <= now:
        row.is_temp_blocked = False
        row.block_until = None
        row.block_days = None
        row.block_started_at = None
        if row.block_reason == "manual_temp":
            row.block_reason = None

    state = state_of(row, now)
    if not state["access_expired"] and row.block_reason == "access_expired":
        row.block_reason = None
    state = state_of(row, now)
    row.block_reason = _target_reason(state)

    after_blocked = bool(state["is_blocked"])
    if force_runtime or before_blocked != after_blocked or before_reason != row.block_reason:
        apply(node, normalized, after_blocked)
    db.commit()
    return state_of(row, now)


def set_temp_block(
    db: Session, node: Node, client_name: str, days: int, *, actor: str | None, apply: RuntimeApply = _adapter_apply
) -> dict[str, Any]:
    row = get_row(db, node.id, client_name, create=True)
    now = _now()
    row.is_temp_blocked = True
    row.is_permanent_blocked = False
    row.block_started_at = now
    row.block_days = days
    row.block_until = now + timedelta(days=days)
    row.block_reason = "manual_temp"
    row.updated_by = actor
    db.flush()
    return reconcile(db, node, client_name, force_runtime=True, apply=apply) or {}


def set_permanent_block(
    db: Session, node: Node, client_name: str, *, actor: str | None, apply: RuntimeApply = _adapter_apply
) -> dict[str, Any]:
    row = get_row(db, node.id, client_name, create=True)
    row.is_permanent_blocked = True
    row.is_temp_blocked = False
    row.block_until = None
    row.block_days = None
    row.block_started_at = None
    row.block_reason = "manual_permanent"
    row.updated_by = actor
    db.flush()
    return reconcile(db, node, client_name, force_runtime=True, apply=apply) or {}


def unblock(
    db: Session, node: Node, client_name: str, *, actor: str | None, apply: RuntimeApply = _adapter_apply
) -> dict[str, Any]:
    row = get_row(db, node.id, client_name, create=True)
    row.is_permanent_blocked = False
    row.is_temp_blocked = False
    row.block_until = None
    row.block_days = None
    row.block_started_at = None
    row.block_reason = None
    row.updated_by = actor
    db.flush()
    return reconcile(db, node, client_name, force_runtime=True, apply=apply) or {}
