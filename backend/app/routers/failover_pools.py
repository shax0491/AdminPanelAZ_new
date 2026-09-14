"""Failover pools: admin CRUD + peer-sync trigger, and public device-facing endpoints
(server-list fetch + status check-in) authenticated by a per-client token instead of
an admin session — same shape as public_portal.py's `{token}` routes.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import (
    FailoverClientLink,
    FailoverPool,
    FailoverPoolMember,
    FailoverStatusReport,
    Node,
    User,
    VpnConfig,
    VpnType,
)
from app.schemas import (
    FailoverClientLinkCreate,
    FailoverClientLinkResponse,
    FailoverDeviceConfigResponse,
    FailoverPoolCreate,
    FailoverPoolFrontUpdate,
    FailoverPoolMemberCreate,
    FailoverPoolMemberResponse,
    FailoverPoolResponse,
    FailoverPoolUpdate,
    FailoverServerEntry,
    FailoverStatusCheckIn,
    FailoverStatusEntry,
    FailoverSwitchResult,
    FailoverSyncResult,
    MessageResponse,
)
from app.services.failover_front import (
    FailoverFrontError,
    evaluate_and_switch,
    mirror_member_identity,
    teardown_front,
)
from app.services.failover_pool import (
    FailoverPoolError,
    build_member_client_conf,
    new_device_token,
    sync_client_peer_to_pool,
)
from app.services.node_manager import NODE_KIND_PROXY

router = APIRouter(prefix="/failover-pools", tags=["failover-pools"])
public_router = APIRouter(prefix="/public/failover", tags=["failover-pools-device"])


def _pool_response(pool: FailoverPool) -> FailoverPoolResponse:
    members = [
        FailoverPoolMemberResponse(
            id=m.id,
            node_id=m.node_id,
            node_name=m.node.name if m.node else "?",
            node_host=m.node.host if m.node else "?",
            priority=m.priority,
            label=m.label,
            identity_mirrored_at=m.identity_mirrored_at.isoformat() if m.identity_mirrored_at else None,
        )
        for m in sorted(pool.members, key=lambda m: m.priority)
    ]
    return FailoverPoolResponse(
        id=pool.id,
        name=pool.name,
        vpn_type=pool.vpn_type.value if hasattr(pool.vpn_type, "value") else str(pool.vpn_type),
        mode=pool.mode.value if hasattr(pool.mode, "value") else str(pool.mode),
        strategy=pool.strategy.value if hasattr(pool.strategy, "value") else str(pool.strategy),
        health_check_target=pool.health_check_target,
        health_check_interval_s=pool.health_check_interval_s,
        health_check_timeout_s=pool.health_check_timeout_s,
        down_threshold=pool.down_threshold,
        enabled=pool.enabled,
        front_node_id=pool.front_node_id,
        front_port=pool.front_port,
        active_member_id=pool.active_member_id,
        last_switch_at=pool.last_switch_at.isoformat() if pool.last_switch_at else None,
        last_switch_error=pool.last_switch_error,
        members=members,
        client_names=[c.client_name for c in pool.clients],
    )


def _get_pool_or_404(db: Session, pool_id: int) -> FailoverPool:
    pool = db.query(FailoverPool).filter(FailoverPool.id == pool_id).first()
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пул не найден")
    return pool


@router.get("", response_model=list[FailoverPoolResponse])
def list_pools(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    pools = db.query(FailoverPool).order_by(FailoverPool.id).all()
    return [_pool_response(p) for p in pools]


@router.post("", response_model=FailoverPoolResponse, status_code=status.HTTP_201_CREATED)
def create_pool(payload: FailoverPoolCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if payload.vpn_type != "amneziawg2":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пока поддерживается только AmneziaWG 2.0")
    pool = FailoverPool(
        name=payload.name,
        vpn_type=VpnType.amneziawg2,
        mode=payload.mode,
        strategy=payload.strategy,
        health_check_target=payload.health_check_target,
        health_check_interval_s=payload.health_check_interval_s,
        health_check_timeout_s=payload.health_check_timeout_s,
        down_threshold=payload.down_threshold,
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return _pool_response(pool)


@router.get("/{pool_id}", response_model=FailoverPoolResponse)
def get_pool(pool_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _pool_response(_get_pool_or_404(db, pool_id))


@router.put("/{pool_id}", response_model=FailoverPoolResponse)
def update_pool(
    pool_id: int, payload: FailoverPoolUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    pool = _get_pool_or_404(db, pool_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(pool, key, value)
    pool.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pool)
    return _pool_response(pool)


@router.delete("/{pool_id}", response_model=MessageResponse)
def delete_pool(pool_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Removes only the pool's own rows (members/client links cascade) — never
    touches the referenced nodes or any peer already synced onto them. For
    dnat_front pools also best-effort removes the front's DNAT rule (never
    blocks deletion if the front is unreachable)."""
    pool = _get_pool_or_404(db, pool_id)
    teardown_front(pool)
    db.delete(pool)
    db.commit()
    return MessageResponse(message=f"Пул '{pool.name}' удалён (узлы и уже синхронизированные пиры не затронуты)")


@router.put("/{pool_id}/front", response_model=FailoverPoolResponse)
def set_front(
    pool_id: int, payload: FailoverPoolFrontUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    """Assign the dedicated front node (a Proxy Node running proxy_agent) +
    UDP port for dnat_front switching. Does not itself install any rule —
    that happens on the first successful switch-check."""
    pool = _get_pool_or_404(db, pool_id)
    node = db.query(Node).filter(Node.id == payload.front_node_id).first()
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Узел не найден")
    if node.node_kind != NODE_KIND_PROXY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Фронтом может быть только узел типа «Прокси» (там установлен proxy_agent)",
        )
    pool.front_node_id = node.id
    pool.front_port = payload.front_port
    pool.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pool)
    return _pool_response(pool)


@router.post("/{pool_id}/members/{member_id}/mirror-identity", response_model=FailoverPoolResponse)
def mirror_identity(
    pool_id: int, member_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    """Clone the pool's primary member AmneziaWG 2.0 server identity (key +
    obfuscation) + ALL its client profiles onto this member. Required once per
    member before it can ever be switched to — invasive by design (overwrites
    the member's own AWG2 server config), only use on nodes dedicated to this
    pool."""
    pool = _get_pool_or_404(db, pool_id)
    member = (
        db.query(FailoverPoolMember)
        .filter(FailoverPoolMember.id == member_id, FailoverPoolMember.pool_id == pool_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник пула не найден")
    try:
        mirror_member_identity(db, pool, member)
    except FailoverFrontError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.refresh(pool)
    return _pool_response(pool)


@router.post("/{pool_id}/switch-check", response_model=FailoverSwitchResult)
def switch_check(pool_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Evaluate member health and flip the front's DNAT if the top-priority
    healthy, identity-ready member differs from what's live now. Manual
    trigger for now — call periodically (cron) for real automatic failover."""
    pool = _get_pool_or_404(db, pool_id)
    result = evaluate_and_switch(db, pool)
    return FailoverSwitchResult(**result)


@router.post("/{pool_id}/members", response_model=FailoverPoolResponse, status_code=status.HTTP_201_CREATED)
def add_member(
    pool_id: int, payload: FailoverPoolMemberCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    pool = _get_pool_or_404(db, pool_id)
    node = db.query(Node).filter(Node.id == payload.node_id).first()
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Узел не найден")
    existing = (
        db.query(FailoverPoolMember)
        .filter(FailoverPoolMember.pool_id == pool_id, FailoverPoolMember.node_id == payload.node_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Этот узел уже в пуле")
    member = FailoverPoolMember(
        pool_id=pool_id, node_id=payload.node_id, priority=payload.priority, label=payload.label
    )
    db.add(member)
    db.commit()
    db.refresh(pool)
    return _pool_response(pool)


@router.delete("/{pool_id}/members/{member_id}", response_model=FailoverPoolResponse)
def remove_member(
    pool_id: int, member_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    pool = _get_pool_or_404(db, pool_id)
    member = (
        db.query(FailoverPoolMember)
        .filter(FailoverPoolMember.id == member_id, FailoverPoolMember.pool_id == pool_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Участник пула не найден")
    db.delete(member)
    db.commit()
    db.refresh(pool)
    return _pool_response(pool)


@router.post(
    "/{pool_id}/clients",
    response_model=FailoverClientLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def link_client(
    pool_id: int, payload: FailoverClientLinkCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    pool = _get_pool_or_404(db, pool_id)
    existing = (
        db.query(FailoverClientLink)
        .filter(FailoverClientLink.pool_id == pool_id, FailoverClientLink.client_name == payload.client_name)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Этот клиент уже привязан к пулу")

    config = (
        db.query(VpnConfig)
        .filter(VpnConfig.client_name == payload.client_name, VpnConfig.vpn_type == VpnType.amneziawg2)
        .first()
    )
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"У клиента '{payload.client_name}' нет конфигурации AmneziaWG 2.0 ни на одном узле",
        )

    link = FailoverClientLink(
        pool_id=pool_id,
        client_name=payload.client_name,
        primary_node_id=config.node_id,
        access_token=new_device_token(),
    )
    db.add(link)
    db.commit()
    db.refresh(link)

    try:
        sync_client_peer_to_pool(db, pool, payload.client_name)
        db.refresh(link)
    except FailoverPoolError as exc:
        link.last_sync_error = str(exc)
        db.commit()

    return FailoverClientLinkResponse(
        id=link.id,
        client_name=link.client_name,
        primary_node_id=link.primary_node_id,
        access_token=link.access_token,
        last_synced_at=link.last_synced_at.isoformat() if link.last_synced_at else None,
        last_sync_error=link.last_sync_error,
    )


@router.delete("/{pool_id}/clients/{client_name}", response_model=MessageResponse)
def unlink_client(
    pool_id: int, client_name: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    link = (
        db.query(FailoverClientLink)
        .filter(FailoverClientLink.pool_id == pool_id, FailoverClientLink.client_name == client_name)
        .first()
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не привязан к этому пулу")
    db.delete(link)
    db.commit()
    return MessageResponse(message=f"Клиент '{client_name}' отвязан от пула (пиры на узлах не удалены)")


@router.post("/{pool_id}/clients/{client_name}/sync", response_model=FailoverSyncResult)
def resync_client(
    pool_id: int, client_name: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    pool = _get_pool_or_404(db, pool_id)
    try:
        result = sync_client_peer_to_pool(db, pool, client_name)
    except FailoverPoolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return FailoverSyncResult(**result)


@router.get("/{pool_id}/clients/{client_name}/status", response_model=list[FailoverStatusEntry])
def client_status(
    pool_id: int, client_name: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    reports = (
        db.query(FailoverStatusReport)
        .filter(FailoverStatusReport.pool_id == pool_id, FailoverStatusReport.client_name == client_name)
        .order_by(FailoverStatusReport.reported_at.desc())
        .all()
    )
    out = []
    for r in reports:
        node = db.query(Node).filter(Node.id == r.active_node_id).first() if r.active_node_id else None
        out.append(
            FailoverStatusEntry(
                device_label=r.device_label,
                active_node_name=node.name if node else None,
                healthy=r.healthy,
                detail=r.detail,
                reported_at=r.reported_at.isoformat(),
            )
        )
    return out


# --- Device-facing (token auth, no admin session) ---


def _link_by_token(db: Session, token: str) -> FailoverClientLink:
    link = db.query(FailoverClientLink).filter(FailoverClientLink.access_token == token).first()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Неверный токен устройства")
    return link


@public_router.get("/{token}/config", response_model=FailoverDeviceConfigResponse)
def device_get_config(token: str, db: Session = Depends(get_db)):
    link = _link_by_token(db, token)
    pool = db.query(FailoverPool).filter(FailoverPool.id == link.pool_id).first()
    if pool is None or not pool.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пул недоступен")

    servers: list[FailoverServerEntry] = []
    for member in sorted(pool.members, key=lambda m: m.priority):
        try:
            conf = build_member_client_conf(db, pool, link.client_name, member)
        except FailoverPoolError:
            continue
        name = member.label or (member.node.name if member.node else f"node-{member.node_id}")
        servers.append(FailoverServerEntry(name=name, priority=member.priority, conf=conf))

    return FailoverDeviceConfigResponse(
        client_name=link.client_name,
        mode=pool.mode.value if hasattr(pool.mode, "value") else str(pool.mode),
        health_check_target=pool.health_check_target,
        health_check_interval_s=pool.health_check_interval_s,
        health_check_timeout_s=pool.health_check_timeout_s,
        down_threshold=pool.down_threshold,
        servers=servers,
    )


@public_router.post("/{token}/status", response_model=MessageResponse)
def device_post_status(token: str, payload: FailoverStatusCheckIn, db: Session = Depends(get_db)):
    link = _link_by_token(db, token)
    active_node_id = None
    if payload.active_node_name:
        member = (
            db.query(FailoverPoolMember)
            .join(Node, Node.id == FailoverPoolMember.node_id)
            .filter(FailoverPoolMember.pool_id == link.pool_id, Node.name == payload.active_node_name)
            .first()
        )
        active_node_id = member.node_id if member else None

    report = (
        db.query(FailoverStatusReport)
        .filter(
            FailoverStatusReport.pool_id == link.pool_id,
            FailoverStatusReport.client_name == link.client_name,
            FailoverStatusReport.device_label == payload.device_label,
        )
        .first()
    )
    if report is None:
        report = FailoverStatusReport(
            pool_id=link.pool_id, client_name=link.client_name, device_label=payload.device_label
        )
        db.add(report)
    report.active_node_id = active_node_id
    report.healthy = payload.healthy
    report.detail = payload.detail
    report.reported_at = datetime.utcnow()
    db.commit()
    return MessageResponse(message="ok")
