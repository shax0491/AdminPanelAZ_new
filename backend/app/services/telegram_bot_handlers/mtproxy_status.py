"""Telegram bot /mtproxy — MTProxy (MTProxyL) на всех узлах: работает ли, домен, доступность из России."""

from __future__ import annotations

from html import escape

from app.services import telegram_bot_i18n as i18n
from app.services.feature_guards import get_feature_service
from app.services.mtproxy_monitor import mtproxy_overview
from app.services.telegram_bot_handlers.base import BotContext, is_admin, unlinked_message
from app.services.telegram_bot_handlers.ui import nav_footer_keyboard, send_or_edit


def _availability_line(row: dict) -> str:
    availability = row.get("availability") or {}
    pct = availability.get("percentage")
    if pct is None:
        return "доступность из России: нет данных"
    icon = "🟢" if pct >= 80 else ("🟡" if pct >= 50 else "🔴")
    probes = ""
    if availability.get("total"):
        probes = f" ({availability.get('success')}/{availability.get('total')} зондов)"
    when = str(availability.get("checked_at") or "").replace("T", " ").replace("Z", " UTC")
    return f"{icon} доступность из России: <b>{pct:g}%</b>{probes}" + (f", {escape(when)}" if when else "")


def _users_lines(users: list[dict]) -> list[str]:
    """Кто сейчас подключён и у кого квота на исходе."""
    lines: list[str] = []
    online = sorted((u for u in users if u.get("connections")), key=lambda u: -int(u["connections"]))
    if online:
        names = ", ".join(f"{escape(str(u['label']))} ({u['connections']})" for u in online)
        lines.append(f"👥 онлайн {len(online)} из {len(users)}: {names}")
    elif users:
        lines.append(f"👥 онлайн 0 из {len(users)}")
    for user in users:
        pct = user.get("quota_pct")
        if pct is not None and pct >= 90:
            icon = "⛔" if pct >= 100 else "⚠️"
            lines.append(f"{icon} {escape(str(user['label']))}: {pct:g}% квоты")
    return lines


def format_mtproxy_text(rows: list[dict]) -> str:
    lines = ["<b>MTProxy</b>"]
    if not rows:
        lines.append("Ни на одном узле MTProxyL не найден (или агенты узлов не обновлены).")
        return "\n".join(lines)
    for row in rows:
        state = "✅ работает" if row.get("running") else f"❌ {escape(str(row.get('status') or 'остановлен'))}"
        lines.append("")
        lines.append(f"<b>{escape(str(row.get('node_name') or row.get('node_id')))}</b> — {state}")
        details = []
        if row.get("domain"):
            details.append(f"домен <code>{escape(str(row['domain']))}</code>")
        if row.get("port"):
            public = row.get("public_port")
            if public and public != row["port"]:
                details.append(f"порт {public} (слушает {row['port']})")
            else:
                details.append(f"порт {row['port']}")
        if row.get("connections") is not None:
            details.append(f"подключений {row['connections']}")
        if details:
            lines.append(", ".join(details))
        lines.append(_availability_line(row))
        lines.extend(_users_lines(row.get("users") or []))
        if row.get("error"):
            lines.append(f"ошибка: {escape(str(row['error']))}")
    return "\n".join(lines)


async def handle_mtproxy_status(ctx: BotContext, *, message_id: int | None = None) -> None:
    from app.services.telegram_api import send_message

    if ctx.user is None:
        await send_message(ctx.bot_token, ctx.chat_id, unlinked_message())
        return
    if not is_admin(ctx.user):
        await send_message(ctx.bot_token, ctx.chat_id, i18n.ADMIN_ONLY)
        return
    if not get_feature_service().is_enabled("mtproxy"):
        await send_message(ctx.bot_token, ctx.chat_id, "MTProxy выключен в настройках панели.")
        return
    try:
        text = format_mtproxy_text(mtproxy_overview(ctx.db))
    except Exception as exc:  # noqa: BLE001
        await send_message(ctx.bot_token, ctx.chat_id, f"MTProxy: ошибка — {escape(str(exc))}")
        return
    await send_or_edit(ctx, text, markup=nav_footer_keyboard(refresh=None), message_id=message_id)
