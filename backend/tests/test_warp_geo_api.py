"""Warp Geo router: feature-gating + node resolution (mocked adapter)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models import NodeStatus
from app.routers import warp_geo as warp_geo_router
from app.services.feature_guards import check_path_access
from app.services.feature_toggles import FeatureToggleService


def _svc(env_file: Path, **flags: bool) -> FeatureToggleService:
    lines = [f"{key}={'true' if value else 'false'}" for key, value in flags.items()]
    env_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return FeatureToggleService(env_file)


def test_warp_geo_disabled_by_default_blocks_access(tmp_path: Path):
    # warp_geo defaults to False - unlike awg2/warper, a fresh install must not
    # expose it until the admin opts in from Настройки → Модули.
    service = _svc(tmp_path / ".env")
    blocked = check_path_access("/api/warp-geo/nodes", service=service)
    assert blocked is not None and blocked[0] == "warp_geo"


def test_warp_geo_enabled_allows_access(tmp_path: Path):
    service = _svc(tmp_path / ".env", FEATURE_WARP_GEO_ENABLED=True)
    blocked = check_path_access("/api/warp-geo/nodes", service=service)
    assert blocked is None


class _QueryStub:
    def __init__(self, result):
        self._result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._result


def test_status_endpoint_404_for_missing_node():
    db = MagicMock()
    db.query.return_value = _QueryStub(None)
    with pytest.raises(HTTPException) as exc:
        warp_geo_router.warp_geo_status(node_id=999, db=db, _=None)
    assert exc.value.status_code == 404


def test_status_endpoint_400_for_proxy_node():
    node = MagicMock(node_kind="proxy")
    db = MagicMock()
    db.query.return_value = _QueryStub(node)
    with pytest.raises(HTTPException) as exc:
        warp_geo_router.warp_geo_status(node_id=1, db=db, _=None)
    assert exc.value.status_code == 400


def test_check_endpoint_rejects_unknown_scope():
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        warp_geo_router.warp_geo_check(node_id=1, scope="bogus", db=db, _=None)
    assert exc.value.status_code == 400


def test_status_endpoint_happy_path(monkeypatch):
    node = MagicMock(node_kind="vpn")
    db = MagicMock()
    db.query.return_value = _QueryStub(node)
    adapter = MagicMock()
    adapter.get_warp_geo_status.return_value = {"warp_provider": "proton"}
    monkeypatch.setattr(warp_geo_router, "get_adapter_for_node", lambda _n: adapter)

    result = warp_geo_router.warp_geo_status(node_id=1, db=db, _=None)

    assert result == {"warp_provider": "proton"}


def test_list_nodes_excludes_proxy_nodes():
    vpn_node = SimpleNamespace(id=1, name="lv1", node_kind="vpn", status=NodeStatus.online)
    db = MagicMock()

    class _ListQuery:
        def filter(self, *_a, **_kw):
            return self

        def order_by(self, *_a, **_kw):
            return self

        def all(self):
            return [vpn_node]

    db.query.return_value = _ListQuery()

    result = warp_geo_router.list_warp_geo_nodes(db=db, _=None)

    assert result == {"nodes": [{"id": 1, "name": "lv1", "status": "online"}]}
