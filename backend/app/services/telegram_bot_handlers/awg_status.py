"""Telegram bot /awg — AmneziaWG 2 and 3 in one message (admin, enabled versions only)."""

from __future__ import annotations

from html import escape

from app.services import telegram_bot_i18n as i18n
from app.services.feature_guards import get_feature_service
from app.services.telegram_bot_handlers.awg2_status import _format_awg2_text
from app.services.telegram_bot_handlers.awg3_status import _format_awg3_text
from app.services.telegram_bot_handlers.base import BotContext, is_admin, unlinked_message
from app.services.telegram_bot_handlers.ui import nav_footer_keyboard, send_or_edit
from app.services.tg_mini_status import build_awg2_status_payload, build_awg3_status_payload

_SECTIONS = (
    ("awg2", "AmneziaWG 2", build_awg2_status_payload, _format_awg2_text),
    ("awg3", "AmneziaWG 3", build_awg3_status_payload, _format_awg3_text),
)


async def handle_awg_status(ctx: BotContext, *, message_id: int | None = None) -> None:
    from app.services.telegram_api import send_message

    if ctx.user is None:
        await send_message(ctx.bot_token, ctx.chat_id, unlinked_message())
        return
    if not is_admin(ctx.user):
        await send_message(ctx.bot_token, ctx.chat_id, i18n.ADMIN_ONLY)
        return
    features = get_feature_service()
    parts: list[str] = []
    for feature, name, build_payload, fmt in _SECTIONS:
        if not features.is_enabled(feature):
            continue
        try:
            parts.append(fmt(build_payload(ctx.db)))
        except Exception as exc:  # noqa: BLE001
            parts.append(f"<b>{name}</b>\nОшибка: {escape(str(exc))}")
    if not parts:
        await send_message(ctx.bot_token, ctx.chat_id, "AmneziaWG 2 и 3 выключены в настройках панели.")
        return
    await send_or_edit(ctx, "\n\n".join(parts), markup=nav_footer_keyboard(refresh="nav:awg"), message_id=message_id)
