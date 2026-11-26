from app.main import app


def test_status_routes_registered():
    # маршрут Mini App пропадал при слиянии (status_router не подключён) -> 404 во вкладках
    paths = app.openapi()["paths"]
    for p in ("awg2", "awg3", "mtproxy", "cidr", "warper"):
        assert f"/api/tg-mini/{p}/status" in paths, p
