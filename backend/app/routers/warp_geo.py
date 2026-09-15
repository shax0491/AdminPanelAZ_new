"""Warp Geo — статус провайдера WARP и гео-проверка исходящего трафика по узлам."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Node, User
from app.services.node_manager import NODE_KIND_PROXY, get_adapter_for_node

router = APIRouter(prefix="/warp-geo", tags=["warp-geo"])


class ProtonConfigBody(BaseModel):
    raw_config: str = Field(min_length=1, max_length=4096)


class ProviderBody(BaseModel):
    provider: str


def _get_vpn_node_or_404(node_id: int, db: Session) -> Node:
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Узел не найден")
    if (node.node_kind or "vpn") == NODE_KIND_PROXY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Узел-прокси не имеет AntiZapret/WARP")
    return node


@router.get("/nodes")
def list_warp_geo_nodes(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    nodes = db.query(Node).filter(Node.node_kind != NODE_KIND_PROXY).order_by(Node.id).all()
    return {"nodes": [{"id": n.id, "name": n.name, "status": n.status.value} for n in nodes]}


@router.get("/{node_id}/status")
def warp_geo_status(node_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = _get_vpn_node_or_404(node_id, db)
    adapter = get_adapter_for_node(node)
    return adapter.get_warp_geo_status()


@router.get("/{node_id}/check")
def warp_geo_check(
    node_id: int,
    scope: str = "antizapret",
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if scope not in ("antizapret", "vpn", "raw"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope должен быть antizapret, vpn или raw")
    node = _get_vpn_node_or_404(node_id, db)
    adapter = get_adapter_for_node(node)
    return adapter.check_warp_geo(scope)


@router.post("/{node_id}/proton-config")
def warp_geo_save_proton_config(
    node_id: int,
    scope: str,
    body: ProtonConfigBody,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if scope not in ("antizapret", "vpn"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope должен быть antizapret или vpn")
    node = _get_vpn_node_or_404(node_id, db)
    adapter = get_adapter_for_node(node)
    return adapter.save_warp_proton_config(scope, body.raw_config)


@router.post("/{node_id}/provider")
def warp_geo_set_provider(
    node_id: int,
    body: ProviderBody,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if body.provider not in ("proton", "cloudflare"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider должен быть proton или cloudflare")
    node = _get_vpn_node_or_404(node_id, db)
    adapter = get_adapter_for_node(node)
    return adapter.set_warp_provider(body.provider)


@router.post("/{node_id}/apply")
def warp_geo_apply(node_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = _get_vpn_node_or_404(node_id, db)
    adapter = get_adapter_for_node(node)
    return adapter.apply_warp_changes()
