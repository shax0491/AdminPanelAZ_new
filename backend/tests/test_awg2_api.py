"""Native AmneziaWG 2 router handlers (mocked adapter)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import UserRole, VpnType
from app.routers import awg2 as awg2_router
from app.services.feature_guards import check_path_access
from app.services.feature_toggles import FeatureToggleService


def _svc(env_file: Path, **flags: bool) -> FeatureToggleService:
    lines = [f"{key}={'true' if value else 'false'}" for key, value in flags.items()]
    env_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return FeatureToggleService(env_file)


class _QueryStub:
    def __init__(self, result):
        self._result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        return [] if self._result is None else [self._result]

    def update(self, *_args, **_kwargs):
        return 0

    def delete(self, *_args, **_kwargs):
        return 0


class _FakeDb:
    def __init__(self, *, owner=None, existing=None):
        self.owner = owner
        self.existing = existing
        self.add = MagicMock()
        self.commit = MagicMock()
        self.refresh = MagicMock()
        self.delete = MagicMock()
        self.flush = MagicMock()

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "User":
            return _QueryStub(self.owner)
        if name == "VpnConfig":
            return _QueryStub(self.existing)
        return _QueryStub(None)

    def close(self):
        return None


def test_awg2_health_requires_toggle(tmp_path: Path):
    service = _svc(tmp_path / ".env", FEATURE_AWG2_ENABLED=False)
    blocked = check_path_access("/api/awg2/health", service=service)
    assert blocked is not None and blocked[0] == "awg2"


def test_awg2_health_ok():
    node = SimpleNamespace(id=1, name="local", host="127.0.0.1")
    adapter = MagicMock()
    adapter.get_awg2_health.return_value = {
        "installed": False,
        "awg_binary": False,
        "server_dir": False,
        "missing_components": ["awg_binary"],
    }
    db = MagicMock()
    with (
        patch.object(awg2_router, "get_active_node", return_value=node),
        patch.object(awg2_router, "get_active_adapter", return_value=adapter),
    ):
        result = awg2_router.awg2_health(db=db, _=SimpleNamespace())
    assert result["installed"] is False
    assert result["missing_components"] == ["awg_binary"]
    assert result["node_id"] == 1
    assert result["node_name"] == "local"


def test_awg2_health_installed_mock():
    node = SimpleNamespace(id=2, name="vpn-a", host="10.0.0.1")
    adapter = MagicMock()
    adapter.get_awg2_health.return_value = {
        "installed": True,
        "awg_binary": True,
        "server_dir": True,
        "missing_components": [],
    }
    with (
        patch.object(awg2_router, "get_active_node", return_value=node),
        patch.object(awg2_router, "get_active_adapter", return_value=adapter),
    ):
        result = awg2_router.awg2_health(db=MagicMock(), _=SimpleNamespace())
    assert result["installed"] is True
    assert result["missing_components"] == []


# --- create/delete идут через config_provisioning (рефакторинг апстрима 2.26.6) ---

_CP = "app.services.config_provisioning"


@pytest.fixture()
def prov_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _prov_node_owner(db):
    from app.models import Node, NodeStatus, User

    node = Node(
        name="node-1", host="127.0.0.1", port=9100, api_key_hash="h", api_key_encrypted="e",
        status=NodeStatus.online, is_local=True, node_kind="vpn", node_metadata="{}",
    )
    owner = User(username="admin", password_hash="x", role=UserRole.admin)
    db.add_all([node, owner])
    db.commit()
    return node, owner


def _provision(db, node, owner, adapter, group=None, enqueue=None):
    from app.services.config_provisioning import provision_config

    with (
        patch(f"{_CP}.get_adapter_for_node", return_value=adapter),
        patch(f"{_CP}.require_vpn_type"),
        patch(f"{_CP}.enforce_can_create_vpn_type"),
        patch(f"{_CP}.get_feature_service", return_value=SimpleNamespace()),
        patch(f"{_CP}.refresh_config_cert_expiry"),
        patch(f"{_CP}.find_sync_group_for_primary", return_value=group),
        patch(f"{_CP}.enqueue_maybe_replicate_create", enqueue or MagicMock(return_value=None)),
        patch(f"{_CP}.admin_notify_service"),
    ):
        return provision_config(
            db, node=node, client_name="awg2user", vpn_type=VpnType.amneziawg2,
            owner=owner, actor=owner, notify=True,
        )


def test_create_amneziawg2_calls_replicate_and_awg2(prov_db):
    node, owner = _prov_node_owner(prov_db)
    adapter = MagicMock()
    adapter.get_awg2_health.return_value = {"installed": True}
    adapter.awg2_add_client.return_value = "ok"
    enqueue = MagicMock(return_value={"task_id": "t1"})

    config, _warn, task_id = _provision(prov_db, node, owner, adapter, group=SimpleNamespace(id=7), enqueue=enqueue)

    assert config.vpn_type == VpnType.amneziawg2 and task_id == "t1"
    adapter.awg2_add_client.assert_called_once_with("awg2user")
    adapter.add_wireguard_client.assert_not_called()
    enqueue.assert_called_once()


def test_create_amneziawg2_not_installed_409(prov_db):
    # Health gate now reflects the native `awg` binary (client.sh), not the retired
    # third-party az-awg2 overlay — "installed": False means amneziawg-tools is missing.
    node, owner = _prov_node_owner(prov_db)
    adapter = MagicMock()
    adapter.get_awg2_health.return_value = {"installed": False, "awg_binary": False}

    with pytest.raises(HTTPException) as exc:
        _provision(prov_db, node, owner, adapter)

    assert exc.value.status_code == 409
    assert "awg" in exc.value.detail["message"].lower()


def test_create_amneziawg2_health_probe_failure_409(prov_db):
    node, owner = _prov_node_owner(prov_db)
    adapter = MagicMock()
    adapter.get_awg2_health.side_effect = HTTPException(status_code=404, detail="missing")

    with pytest.raises(HTTPException) as exc:
        _provision(prov_db, node, owner, adapter)

    assert exc.value.status_code == 409
    assert "awg" in exc.value.detail["message"].lower()


def _deprovision(db, node, owner, config, adapter, group=None):
    from app.services.config_provisioning import deprovision_config

    enqueue = MagicMock(return_value=None)
    with (
        patch(f"{_CP}.get_adapter_for_node", return_value=adapter),
        patch(f"{_CP}.find_sync_group_for_primary", return_value=group),
        patch(f"{_CP}.enqueue_maybe_replicate_delete", enqueue),
        patch(f"{_CP}.admin_notify_service"),
    ):
        deprovision_config(db, config=config, actor=owner, notify=True)
    return enqueue


def _add_config(db, node, owner, vpn_type):
    from app.models import VpnConfig

    config = VpnConfig(node_id=node.id, client_name="awg2user", vpn_type=vpn_type, owner_id=owner.id)
    db.add(config)
    db.commit()
    return config


def test_delete_amneziawg2_calls_replicate_and_awg2(prov_db):
    node, owner = _prov_node_owner(prov_db)
    config = _add_config(prov_db, node, owner, VpnType.amneziawg2)
    adapter = MagicMock()

    enqueue = _deprovision(prov_db, node, owner, config, adapter, group=SimpleNamespace(id=7))

    adapter.awg2_delete_client.assert_called_once_with("awg2user")
    adapter.delete_wireguard_client.assert_not_called()
    enqueue.assert_called_once()


def test_delete_one_protocol_leaves_other_protocol_rows_for_same_client_untouched(prov_db):
    """client.sh options 7/8/9 delete exactly one protocol for a client name (see
    AntiZapretService.delete_openvpn_client/delete_wireguard_client/delete_amneziawg2_client) —
    deleting the AWG2 row for a client that also has an OpenVPN row must succeed and must not
    touch that sibling OpenVPN config/adapter call at all."""
    from app.models import VpnConfig

    node, owner = _prov_node_owner(prov_db)
    _add_config(prov_db, node, owner, VpnType.openvpn)
    config = _add_config(prov_db, node, owner, VpnType.amneziawg2)
    adapter = MagicMock()

    _deprovision(prov_db, node, owner, config, adapter)

    adapter.awg2_delete_client.assert_called_once_with("awg2user")
    adapter.delete_openvpn_client.assert_not_called()
    adapter.delete_wireguard_client.assert_not_called()
    left = prov_db.query(VpnConfig).all()
    assert [c.vpn_type for c in left] == [VpnType.openvpn]


def test_get_monitoring_ok():
    node = SimpleNamespace(id=1, name="local", host="127.0.0.1")
    adapter = MagicMock()
    adapter.get_awg2_monitoring.return_value = {
        "ifaces": [{"name": "antizapret-awg", "peer_count": 1}],
        "clients": [{"name": "ivan", "online": True}],
        "stats_available": True,
    }
    with (
        patch.object(awg2_router, "get_active_node", return_value=node),
        patch.object(awg2_router, "get_active_adapter", return_value=adapter),
    ):
        result = awg2_router.get_monitoring(db=MagicMock(), _=SimpleNamespace())
    assert result["stats_available"] is True
    assert result["clients"][0]["name"] == "ivan"
    assert result["node_id"] == 1


def test_get_client_stats_ok():
    node = SimpleNamespace(id=1, name="local", host="127.0.0.1")
    adapter = MagicMock()
    adapter.get_awg2_client_stats.return_value = {
        "name": "ivan",
        "online": True,
        "endpoint": "1.2.3.4:12345",
        "handshake_age_s": 12,
        "rx_life": 10,
        "tx_life": 20,
        "daily": [{"day": "2026-08-10", "rx": 1, "tx": 2}],
        "geo": None,
    }
    with (
        patch.object(awg2_router, "get_active_node", return_value=node),
        patch.object(awg2_router, "get_active_adapter", return_value=adapter),
    ):
        result = awg2_router.get_client_stats("ivan", db=MagicMock(), _=SimpleNamespace())
    assert result["name"] == "ivan"
    assert result["daily"][0]["day"] == "2026-08-10"
    assert result["node_id"] == 1
