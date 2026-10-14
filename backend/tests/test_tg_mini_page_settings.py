"""Mini App page must get the Settings object, not the tg_mini.settings submodule (name clash in the package)."""

from __future__ import annotations

from starlette.requests import Request

from app.config import Settings
from app.routers import tg_mini
from app.routers.tg_mini import static as tg_static


def _request(path: str = "/api/tg-mini") -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def test_mini_app_page_passes_settings_object(monkeypatch, tmp_path):
    index = tmp_path / "index.html"
    index.write_text('<html><head></head><body><script src="./assets/a.js"></script></body></html>', encoding="utf-8")
    monkeypatch.setattr(tg_mini, "_static_index", lambda: index)
    seen = []

    def fake_script(settings):
        seen.append(settings)
        return "<script>window.__X=1</script>"

    monkeypatch.setattr("app.services.panel_paths.panel_access_path_script", fake_script)

    response = tg_static.mini_app_page(_request())

    assert response.status_code == 200
    assert isinstance(seen[0], Settings)
    assert b"/api/tg-mini/assets/a.js" in response.body
