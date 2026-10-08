"""MTProxy (MTProxyL) on all nodes for the panel tab: state, domain, connections, availability from Russia."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.services.feature_guards import get_feature_service
from app.services.mtproxy_monitor import mtproxy_all_nodes, reset_cache

router = APIRouter(prefix="/mtproxy", tags=["mtproxy"])


@router.get("/status")
def mtproxy_status(
    refresh: bool = Query(False, description="Опросить узлы заново, минуя кэш"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if not get_feature_service().is_enabled("mtproxy"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Модуль MTProxy отключён")
    if refresh:
        reset_cache()
    return {"nodes": mtproxy_all_nodes(db)}
