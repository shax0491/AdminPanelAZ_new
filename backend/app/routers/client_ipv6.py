"""Профиль IPv6 клиента (default | ipv4 | dual) для всех слоёв: OpenVPN, AWG 1.5, AWG 2.0, AWG 3.0.

Профиль хранится в config/ipv6-profile.txt узла ("имя ipv4|dual"); "default" удаляет строку, и берётся IPV6_PROFILE_DEFAULT.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.services import awg3_clients
from app.services.action_log import log_action
from app.services.node_manager import get_active_adapter
from app.services.node_sync.groups import require_ha_primary_for_config_ops

router = APIRouter(prefix="/client-ipv6", tags=["ipv6"])

PROFILE_FILE = "ipv6-profile.txt"
CLIENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class ProfileBody(BaseModel):
    profile: Literal["default", "ipv4", "dual"]
    # Перегенерировать клиентские конфиги WG/AWG (client.sh 4, затрагивает всех клиентов) и обновить AWG3-пир.
    regenerate: bool = True


def _read(adapter) -> dict[str, str]:
    try:
        text = adapter.read_config_file(PROFILE_FILE)
    except FileNotFoundError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.lstrip().startswith("#") and parts[1] in ("ipv4", "dual"):
            out[parts[0]] = parts[1]
    return out


def render_profiles(text: str, client: str, profile: str) -> str:
    """Заменить/добавить/удалить строку клиента, остальное (комментарии, чужие строки) не трогать."""
    kept = [ln for ln in text.splitlines() if not (ln.split() and ln.split()[0] == client and not ln.lstrip().startswith("#"))]
    if profile != "default":
        kept.append(f"{client} {profile}")
    return "\n".join(kept) + "\n" if kept else ""


@router.get("")
def list_client_profiles(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"profiles": _read(get_active_adapter(db))}


@router.put("/{client}")
def set_client_profile(
    client: str,
    body: ProfileBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if not CLIENT_RE.match(client):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимое имя клиента")
    require_ha_primary_for_config_ops(db)
    adapter = get_active_adapter(db)
    try:
        current = adapter.read_config_file(PROFILE_FILE)
    except FileNotFoundError:
        current = ""
    adapter.write_config_file(PROFILE_FILE, render_profiles(current, client, body.profile))
    notes: list[str] = []
    if body.regenerate:
        try:
            adapter.recreate_profiles()
            notes.append("профили WG/AWG перегенерированы")
        except Exception as exc:  # noqa: BLE001 - профиль уже записан, перегенерацию можно повторить
            notes.append(f"перегенерация профилей не удалась: {exc}")
        try:
            done = adapter.awg3_refresh_ipv6(client)
            if done:
                notes.append("AWG 3.0: " + ", ".join(done))
        except awg3_clients.Awg3ClientError:
            pass  # у клиента нет AWG3-записей
        except (NotImplementedError, AttributeError):
            pass
    log_action(
        db,
        action="client_ipv6_profile",
        user_id=current_user.id,
        username=current_user.username,
        details=f"{client}={body.profile}",
    )
    return {"client": client, "profile": body.profile, "notes": notes}
