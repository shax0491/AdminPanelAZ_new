"""AWG 3 route lists are listed and served like AWG 1.5 / 2.0 ones."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.constants.public_routes import PUBLIC_ROUTE_ROUTERS
from app.database import get_db
from app.routers import public_download as public_download_router
from app.services.cidr.constants import RESULT_FILES


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(public_download_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app) as c:
        yield c


def test_awg3_route_files_registered_for_listing_and_download():
    assert RESULT_FILES["keenetic_awg3"] == "keenetic-amneziawg3-routes.txt"
    assert RESULT_FILES["mikrotik_awg3"] == "mikrotik-amneziawg3-routes.txt"
    assert PUBLIC_ROUTE_ROUTERS["keenetic-awg3"] == "keenetic_awg3"
    assert PUBLIC_ROUTE_ROUTERS["mikrotik-awg3"] == "mikrotik_awg3"


@pytest.mark.parametrize("router,key", [("keenetic-awg3", "keenetic_awg3"), ("mikrotik-awg3", "mikrotik_awg3")])
def test_awg3_public_download_serves_node_file(client, router, key):
    adapter = MagicMock()
    adapter.get_route_result_content.return_value = {"filename": RESULT_FILES[key], "content": "route ADD 1.1.1.0 MASK 255.255.255.0 10.9.0.1\n"}
    with (
        patch("app.routers.public_download.require_openvpn_and_security"),
        patch("app.routers.public_download.is_public_download_enabled", return_value=True),
        patch("app.routers.public_download.ip_restriction_service") as ip_svc,
        patch("app.routers.public_download.public_download_rate_limit_service"),
        patch("app.routers.public_download.get_active_adapter", return_value=adapter),
        patch("app.routers.public_download.settings") as settings,
        patch("app.routers.public_download.log_action"),
    ):
        ip_svc.get_client_ip.return_value = "203.0.113.10"
        settings.audit_log_enabled = False
        resp = client.get(f"/api/public/route-download/{router}")
    assert resp.status_code == 200
    adapter.get_route_result_content.assert_called_once_with(key)
    assert "10.9.0.1" in resp.text
