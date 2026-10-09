"""MTProxy (MTProxyL) on all nodes for the panel tab: state, users, and actions on users/limits."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Node, User
from app.services.action_log import log_action
from app.services.feature_guards import get_feature_service
from app.services.mtproxy_monitor import (
    PANEL_NODE_ID,
    mtproxy_all_nodes,
    panel_mtproxy_status,
    panel_node_name,
    reset_cache,
    run_mtproxy_action,
)

router = APIRouter(prefix="/mtproxy", tags=["mtproxy"])


class MtproxyActionIn(BaseModel):
    action: Literal["setlimits", "enable", "disable", "link", "reset_traffic", "restart"]
    label: str | None = Field(default=None, max_length=64)
    max_conns: int | None = Field(default=None, ge=0, le=10_000)
    max_ips: int | None = Field(default=None, ge=0, le=1_000)
    quota_gb: float | None = Field(default=None, ge=0, le=100_000)
    expires: str | None = Field(default=None, max_length=32)


def _require_enabled() -> None:
    if not get_feature_service().is_enabled("mtproxy"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Модуль MTProxy отключён")


@router.get("/status")
def mtproxy_status(
    refresh: bool = Query(False, description="Опросить узлы заново, минуя кэш"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    _require_enabled()
    if refresh:
        reset_cache()
    return {"nodes": mtproxy_all_nodes(db)}


@router.post("/nodes/{node_id}/action")
def mtproxy_node_action(
    node_id: int,
    payload: MtproxyActionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Лимиты/вкл/выкл/ссылка пользователя, обнуление трафика или перезапуск MTProxyL на узле."""
    _require_enabled()
    from app.services.node_manager import get_adapter_for_node, is_vpn_node

    try:
        if node_id == PANEL_NODE_ID:
            # MTProxyL на сервере самой панели: выполняем здесь же, панель работает от root
            if panel_mtproxy_status(db) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MTProxyL на панели не найден")
            node_name = panel_node_name()
            result = run_mtproxy_action(payload.model_dump(exclude_none=True))
        else:
            node = db.get(Node, node_id)
            if node is None or not is_vpn_node(node):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Узел не найден")
            node_name = node.name
            result = get_adapter_for_node(node).mtproxy_action(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MTProxyL: {result.get('output') or 'команда завершилась с ошибкой'}"[:500],
        )
    if payload.action != "link":
        reset_cache()
        log_action(
            db,
            action="mtproxy_action",
            user_id=current_user.id,
            username=current_user.username,
            details=f"node={node_name};action={payload.action};label={payload.label or ''}",
        )
    return result
