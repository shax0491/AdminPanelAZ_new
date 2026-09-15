"""Native AmneziaWG 2.0 status/monitoring API (client management is under /configs)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.services.awg2 import Awg2ClientNotFoundError, Awg2NotInstalledError
from app.services.node_manager import get_active_adapter, get_active_node, get_adapter_for_node, list_vpn_nodes
from app.services.node_sync.groups import find_sync_group_for_primary, get_replica_nodes
from app.services.node_sync.vpn_state_sync import sync_amneziawg2_state_from_primary

router = APIRouter(prefix="/awg2", tags=["awg2"])


def _node_meta(node) -> dict:
    return {"node_id": node.id, "node_name": node.name, "node_host": node.host}


def _ha_sync_awg2_from_active(db: Session) -> dict[str, Any]:
    """Best-effort HA push of native AWG2 state to replicas. Never raises.

    Kept here (rather than only reachable via a removed obfuscation-apply endpoint) because
    app.services.backup_overlays imports this directly when a restored panel backup embeds
    an az-awg2 overlay archive — unrelated to this router's own (now status/monitoring-only)
    endpoints, but this is still the one place that wires it up.
    """
    errors: list[dict[str, str | None]] = []
    try:
        node = get_active_node(db)
        group = find_sync_group_for_primary(db, node.id)
        if not group:
            return {"attempted": False, "errors": []}
        primary_adapter = get_active_adapter(db)
        replicas = get_replica_nodes(db, group)
        if not replicas:
            return {"attempted": True, "errors": []}
        for replica in replicas:
            try:
                replica_adapter = get_adapter_for_node(replica)
                sync_amneziawg2_state_from_primary(
                    primary_adapter,
                    replica_adapter,
                    db=db,
                    replica_node=replica,
                )
            except Exception as exc:  # noqa: BLE001 — collect warnings, do not fail apply
                errors.append({"node_name": getattr(replica, "name", None), "error": str(exc)})
        return {"attempted": True, "errors": errors}
    except Exception as exc:  # noqa: BLE001
        return {"attempted": True, "errors": [{"node_name": None, "error": str(exc)}]}


def _map_awg2_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, Awg2NotInstalledError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, Awg2ClientNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/health")
def awg2_health(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = get_active_node(db)
    data = get_active_adapter(db).get_awg2_health()
    return {**data, **_node_meta(node)}


@router.get("/monitoring")
def get_monitoring(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = get_active_node(db)
    try:
        data = get_active_adapter(db).get_awg2_monitoring()
    except Exception as exc:  # noqa: BLE001
        raise _map_awg2_exc(exc) from exc
    return {**data, **_node_meta(node)}


@router.get("/monitoring/all")
def get_monitoring_all(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Combined AWG2 client monitoring across every VPN node in one call, so the
    panel can show one flat table instead of forcing an admin to switch the
    active node and reload per server. Best-effort per node: a node where AWG2
    isn't installed or is unreachable is reported with an error, never fails
    the whole request."""
    out: list[dict[str, Any]] = []
    for node in list_vpn_nodes(db):
        entry: dict[str, Any] = {**_node_meta(node), "clients": [], "error": None}
        try:
            data = get_adapter_for_node(node).get_awg2_monitoring()
            entry["clients"] = data.get("clients", [])
        except Awg2NotInstalledError:
            continue
        except Exception as exc:  # noqa: BLE001 — one unreachable node must not break the rest
            entry["error"] = str(exc)
        out.append(entry)
    return {"nodes": out}


@router.get("/clients/{name}/stats")
def get_client_stats(name: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    node = get_active_node(db)
    try:
        data = get_active_adapter(db).get_awg2_client_stats(name)
    except Exception as exc:  # noqa: BLE001
        raise _map_awg2_exc(exc) from exc
    return {**data, **_node_meta(node)}
