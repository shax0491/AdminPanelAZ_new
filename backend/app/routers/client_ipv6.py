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


def require_server_dual_stack(adapter) -> None:
    """Dual-Stack клиента возможен, только если узел САМ подтвердил: ENABLE_IPV6=y и эффективный режим не 'a'.
    Источник - get_ipv6_status() узла (setup + state up.sh), не БД панели. Выключен -> 409; состояние неизвестно
    (узел недоступен, не VPN-узел, режим не подтверждён) -> fail-closed, профиль не пишем."""
    try:
        st = adapter.get_ipv6_status() or {}
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dual-Stack недоступен: узел не VPN-узел или не отдаёт статус IPv6") from exc
    except Exception as exc:  # noqa: BLE001 - не знаем состояние сервера: профиль не пишем
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Не удалось узнать состояние Dual-Stack на сервере: {exc}") from exc
    if not st.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dual-Stack выключен на сервере: включите его в карточке VPS, затем выдайте Dual-Stack профиль",
        )
    if st.get("effective_mode") in (None, "", "a"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dual-Stack включён в настройках, но узел его не применил (effective_mode=a): дождитесь применения или проверьте узел",
        )


def _dual_set(text: str) -> set[str]:
    return {p[0] for p in (ln.split() for ln in text.splitlines() if not ln.lstrip().startswith("#")) if len(p) >= 2 and p[1] == "dual"}


def guard_profile_file_write(adapter, new_text: str) -> None:
    """Прямая правка ipv6-profile.txt (редактор файлов) не должна обходить 409: новые dual-строки требуют серверный Dual-Stack."""
    try:
        old = adapter.read_config_file(PROFILE_FILE)
    except FileNotFoundError:
        old = ""
    if _dual_set(new_text) - _dual_set(old):
        require_server_dual_stack(adapter)


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
    if body.profile == "dual":  # "ipv4" и "default" допустимы всегда: при выключенном Dual-Stack клиент работает по IPv4
        require_server_dual_stack(adapter)
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
