"""AmneziaWG 3.0 endpoints, separate from /awg2.

Everything goes through the active node's adapter: the AWG 3.0 interface lives
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
from app.services.node_manager import get_active_adapter

router = APIRouter(prefix="/awg3", tags=["awg3"])


class Awg3ClientCreate(BaseModel):
    name: str


@router.get("/health")
def awg3_health(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return get_active_adapter(db).awg3_health()


@router.get("/monitoring")
def awg3_monitoring(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return get_active_adapter(db).awg3_monitoring()


@router.get("/clients")
def awg3_list_clients(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"items": get_active_adapter(db).awg3_list_clients()}


@router.post("/clients", status_code=201)
def awg3_create_client(payload: Awg3ClientCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        res = get_active_adapter(db).awg3_create_client(payload.name)
    except awg3_clients.Awg3ClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": res.get("name", payload.name), "ip": res.get("ip"), "public_key": res.get("public_key")}


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
