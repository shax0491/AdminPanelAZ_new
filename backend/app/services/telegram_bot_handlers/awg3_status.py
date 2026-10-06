"""Telegram bot /awg3 — AmneziaWG 3.1 status (admin, if awg3 enabled)."""

from __future__ import annotations

from app.services.feature_guards import get_feature_service
from app.services.node_manager import get_active_adapter
from app.services.telegram_bot_handlers.base import BotContext, is_admin, unlinked_message
from app.services.telegram_bot_handlers.ui import nav_footer_keyboard, send_or_edit
from app.services import telegram_bot_i18n as i18n


def _format_awg3_text(health: dict, monitoring: dict) -> str:
    lines = ["<b>AmneziaWG 3.1</b>"]
    lines.append(
        f"инструменты: {'да' if health.get('tools_present') else 'нет'}, "
        f"userspace: {'да' if health.get('userspace_present') else 'нет'}"
    )
    for iface in health.get("ifaces") or []:
        state = "поднят" if iface.get("up") else "выключен"
        lines.append(f"• <code>{iface.get('name')}</code> UDP {iface.get('port')} {iface.get('subnet')} — {state}")
    peers = sum(len(v.get("peers") or []) for v in (monitoring.get("ifaces") or {}).values())
    lines.append(f"клиентов: {peers}")
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
        adapter = get_active_adapter(ctx.db)
        text = _format_awg3_text(adapter.awg3_health(), adapter.awg3_monitoring())
    except Exception as exc:  # noqa: BLE001
        await send_message(ctx.bot_token, ctx.chat_id, f"AmneziaWG 3.1: ошибка — {exc}")
        return
    await send_or_edit(ctx, text, markup=nav_footer_keyboard(refresh=None), message_id=message_id)
