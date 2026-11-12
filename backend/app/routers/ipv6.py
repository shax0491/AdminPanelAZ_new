"""IPv6 узла AntiZapret: статус и применение (режим, выход и профиль задаются в «Конфиг AntiZapret»)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.services.action_log import log_action
from app.services.node_manager import get_active_adapter
from app.services.node_sync.groups import require_ha_primary_for_config_ops

router = APIRouter(prefix="/ipv6", tags=["ipv6"])


def _status(adapter) -> dict[str, Any]:
    try:
        return adapter.get_ipv6_status()
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc


@router.get("")
def get_ipv6_status(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _status(get_active_adapter(db))


@router.post("/apply")
def apply_ipv6(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Перезапуск antizapret: up.sh заново читает setup, проверяет выход IPv6 и выставляет эффективный режим."""
    require_ha_primary_for_config_ops(db)
    adapter = get_active_adapter(db)
    adapter.restart_service("antizapret")
    log_action(db, action="ipv6_apply", user_id=current_user.id, username=current_user.username, details="restart antizapret")
    return _status(adapter)
