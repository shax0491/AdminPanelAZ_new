"""Telegram bot /awg3 — AmneziaWG 3.1 status (admin, if awg3 enabled): online, interfaces, top traffic."""

from __future__ import annotations

from app.services import telegram_bot_i18n as i18n
from app.services.feature_guards import get_feature_service
from app.services.telegram_bot_handlers.base import BotContext, is_admin, unlinked_message
from app.services.telegram_bot_handlers.ui import nav_footer_keyboard, send_or_edit
from app.services.tg_mini_status import build_awg3_status_payload
from app.services.traffic_limit import human_bytes


def _format_awg3_text(payload: dict) -> str:
    lines = [
        "<b>AmneziaWG 3.1</b>",
        f"Узел: <code>{payload.get('node_name') or '—'}</code> ({payload.get('node_host') or '—'})",
    ]
    if payload.get("health_error"):
        lines.append(f"Ошибка проверки: {payload['health_error']}")
    if not payload.get("installed"):
        missing = payload.get("missing_components") or []
        lines.append("Установлен: нет" + (f" (нет {', '.join(missing)})" if missing else ""))
        return "\n".join(lines)
    lines.append("Установлен: да")
    lines.append(f"Онлайн: {int(payload.get('online_count') or 0)} из {int(payload.get('peer_count') or 0)}")
    lines.append(f"Интерфейсы: {payload.get('ifaces_summary') or '—'}")
    top = payload.get("top_traffic") or []
    if top:
        lines.append("Топ по трафику:")
        for row in top:
            lines.append(
                f"• <code>{row.get('name')}</code> — ↓{human_bytes(int(row.get('rx') or 0)) or '0 B'} "
                f"↑{human_bytes(int(row.get('tx') or 0)) or '0 B'}"
            )
    else:
        lines.append("Трафика по клиентам пока нет.")
    return "\n".join(lines)


async def handle_awg3_status(ctx: BotContext, *, message_id: int | None = None) -> None:
    from app.services.telegram_api import send_message

    if ctx.user is None:
        await send_message(ctx.bot_token, ctx.chat_id, unlinked_message())
        return
    if not is_admin(ctx.user):
        await send_message(ctx.bot_token, ctx.chat_id, i18n.ADMIN_ONLY)
        return
    if not get_feature_service().is_enabled("awg3"):
        await send_message(ctx.bot_token, ctx.chat_id, "AmneziaWG 3.1 выключен в настройках панели.")
        return
    try:
        text = _format_awg3_text(build_awg3_status_payload(ctx.db))
    except Exception as exc:  # noqa: BLE001
        await send_message(ctx.bot_token, ctx.chat_id, f"AmneziaWG 3.1: ошибка — {exc}")
        return
    await send_or_edit(ctx, text, markup=nav_footer_keyboard(refresh=None), message_id=message_id)
