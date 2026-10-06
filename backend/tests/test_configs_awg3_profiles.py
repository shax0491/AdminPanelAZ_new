"""AWG 3.1 client cards list both profiles (antizapret and full VPN) and download them by record path."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import UserRole, VpnType
from app.routers import configs as configs_router


def _config(name="alice", vpn_type=VpnType.amneziawg3):
    return SimpleNamespace(id=7, client_name=name, vpn_type=vpn_type, node_id=1)


def _adapter(records):
    adapter = MagicMock()
    adapter.awg3_list_clients.return_value = [{"name": r} for r in records]
    adapter.get_profile_files.return_value = []
    adapter.awg3_client_config.side_effect = lambda record: f"[Interface]\n# {record}\n"
    return adapter


def test_card_files_are_both_awg3_profiles_without_mode_choice():
    adapter = _adapter(["alice_az", "alice_vpn"])
    files = configs_router._node_profile_files(adapter, _config())
    assert [(f["path"], f["variant"]) for f in files] == [
        ("awg3:alice_az", "antizapret"),
        ("awg3:alice_vpn", "vpn"),
    ]
    adapter.get_profile_files.assert_not_called()


def test_card_files_skip_missing_record():
    adapter = _adapter(["alice_vpn"])
    files = configs_router._node_profile_files(adapter, _config())
    assert [f["path"] for f in files] == ["awg3:alice_vpn"]


def _download(config, path, *, role_admin=True, adapter=None, awg3_mode="split", policy=None):
    db = MagicMock()
    user = SimpleNamespace(role=configs_router.UserRole.admin if role_admin else configs_router.UserRole.user)
    adapter = adapter or _adapter(["alice_az", "alice_vpn"])
    with (
        patch.object(configs_router, "_get_config_for_active_node", return_value=config),
        patch.object(configs_router, "_can_access_config", return_value=True),
        patch.object(configs_router, "get_active_adapter", return_value=adapter),
        patch.object(configs_router, "_viewer_visibility_policy", return_value=policy or {}),
    ):
        return configs_router.download_profile(config.id, path, awg3_mode, db, user), adapter


def test_download_by_card_path_serves_chosen_record_with_mode_filename():
    resp, adapter = _download(_config(), "awg3:alice_vpn")
    adapter.awg3_client_config.assert_called_once_with("alice_vpn")
    assert "AWG3-VPN-alice.conf" in resp.headers["content-disposition"]


def test_download_by_bare_path_uses_mode():
    resp, adapter = _download(_config(), "", awg3_mode="split")
    adapter.awg3_client_config.assert_called_once_with("alice_az")
    assert "AWG3-AZ-alice.conf" in resp.headers["content-disposition"]


def test_download_rejects_record_of_another_client():
    adapter = _adapter(["bob_az"])
    with pytest.raises(HTTPException) as exc:
        _download(_config(), "awg3:bob_az", adapter=adapter)
    assert exc.value.status_code == 404
    adapter.awg3_client_config.assert_not_called()


def test_download_respects_visibility_policy_for_non_admin(monkeypatch):
    monkeypatch.setattr(configs_router, "profile_file_allowed", lambda policy, **kw: kw["variant"] != "vpn")
    with pytest.raises(HTTPException) as exc:
        _download(_config(), "awg3:alice_vpn", role_admin=False)
    assert exc.value.status_code == 403
