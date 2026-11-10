"""Bot/Mini App callers must pass ``BackgroundTasks`` to ``update_telegram_settings`` (2.26.6 signature).

The merged code kept the old positional call ``(payload, request, db, admin)``, which shifted ``db``/``admin``
by one slot and made both the Mini App PATCH and the bot settings handler fail with AttributeError.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import BackgroundTasks

from app.services.telegram_bot_handlers import settings as bot_settings


def test_bot_patch_passes_background_tasks_then_db_then_admin(monkeypatch):
    captured: dict = {}
    ran: list[str] = []

    def fake_update(payload, request, background, db, admin):
        captured.update(payload=payload, background=background, db=db, admin=admin)
        background.add_task(ran.append, "synced")
        return "result"

    monkeypatch.setattr("app.routers.settings_telegram.update_telegram_settings", fake_update)
    monkeypatch.setattr(bot_settings, "_make_bot_request", lambda _ctx: MagicMock())
    monkeypatch.setattr(bot_settings, "_log_bot_action", lambda *_a, **_k: None)
    ctx = SimpleNamespace(db=object(), user=object())

    result = bot_settings._apply_telegram_patch(ctx, payload="payload", log_details="x")

    assert result == "result"
    assert isinstance(captured["background"], BackgroundTasks)
    assert captured["db"] is ctx.db and captured["admin"] is ctx.user
    assert ran == ["synced"], "queued best-effort tasks must run: there is no HTTP response to trigger them"
