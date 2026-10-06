"""AmneziaWG 3 endpoints, separate from /awg2.

Everything goes through the active node's adapter: the AWG 3 interface lives
on the node (DE2, NL1, ...), not on the panel host.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.services import awg3_clients
from app.services.awg3_noc import awg3_monitoring_view
from app.services.node_manager import get_active_adapter, get_active_node, get_adapter_for_node, list_vpn_nodes

router = APIRouter(prefix="/awg3", tags=["awg3"])


class Awg3ClientCreate(BaseModel):
    name: str
    mode: str = "split"


@router.get("/health")
def awg3_health(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return get_active_adapter(db).awg3_health()


@router.get("/monitoring")
def awg3_monitoring(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = get_active_node(db)
    return {**awg3_monitoring_view(get_active_adapter(db).awg3_monitoring()), "node_id": node.id, "node_name": node.name, "node_host": node.host}


@router.get("/monitoring/all")
def awg3_monitoring_all(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """All VPN nodes in one call (same contract as AWG 2): one node failing never breaks the rest."""
    out: list[dict] = []
    for node in list_vpn_nodes(db):
        entry: dict = {"node_id": node.id, "node_name": node.name, "node_host": node.host, "clients": [], "error": None}
        try:
            entry["clients"] = awg3_monitoring_view(get_adapter_for_node(node).awg3_monitoring())["clients"]
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
        out.append(entry)
    return {"nodes": out}


@router.get("/clients")
def awg3_list_clients(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"items": get_active_adapter(db).awg3_list_clients()}


@router.post("/clients", status_code=201)
def awg3_create_client(payload: Awg3ClientCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        res = get_active_adapter(db).awg3_create_client(payload.name, payload.mode)
    except awg3_clients.Awg3ClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "name": res.get("name", payload.name),
        "mode": res.get("mode", payload.mode),
        "ip": res.get("ip"),
        "port": res.get("port"),
        "public_key": res.get("public_key"),
        "profile": res.get("profile"),
    }


@router.get("/clients/{name}/config")
def awg3_client_config(name: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        text = get_active_adapter(db).awg3_client_config(name)
    except awg3_clients.Awg3ClientError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"name": name, "filename": f"awg3-{name}.conf", "config": text}


@router.delete("/clients/{name}", status_code=204)
def awg3_delete_client(name: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        get_active_adapter(db).awg3_delete_client(name)
    except awg3_clients.Awg3ClientError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return None
