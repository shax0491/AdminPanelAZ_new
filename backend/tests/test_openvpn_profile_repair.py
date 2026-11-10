"""Tests for OpenVPN profile helpers (no automatic cert re-issue)."""

from unittest.mock import MagicMock

from app.services.openvpn_pki import ProfileCertIssue, ProfileValidationResult
from app.services.openvpn_profile_repair import (
    recreate_openvpn_profiles,
    finalize_openvpn_profiles_after_add,
    validate_openvpn_profiles,
)


def test_recreate_openvpn_profiles_calls_client_sh_7_only():
    adapter = MagicMock()

    result = recreate_openvpn_profiles(adapter)

    adapter.recreate_profiles.assert_called_once()
    adapter.add_openvpn_client.assert_not_called()
    assert result.success
    assert result.recreated


def test_finalize_openvpn_profiles_after_add_validates_without_recreate_or_reissue(monkeypatch):
    # 2.26.6: create/renew no longer run client.sh 7 at all (client.sh 1 already wrote the new .ovpn files).
    adapter = MagicMock()
    monkeypatch.setattr(
        "app.services.openvpn_profile_repair.validate_client_profiles",
        lambda *args, **kwargs: ProfileValidationResult(
            ready=False,
            issues=(
                ProfileCertIssue(
                    client_name="AN_Office",
                    path="/root/antizapret/client/openvpn/antizapret-udp/x.ovpn",
                    filename="x.ovpn",
                    serial_hex="F401806F35A8048BA0941A9F085EF9C2",
                    status="revoked",
                ),
            ),
        ),
    )

    result = finalize_openvpn_profiles_after_add(adapter, client_names=["AN_Office"])

    adapter.recreate_profiles.assert_not_called()
    adapter.add_openvpn_client.assert_not_called()
    assert result.success
    assert not result.recreated
    assert result.validation is not None
    assert not result.validation.ready


def test_validate_openvpn_profiles_is_read_only(monkeypatch):
    adapter = MagicMock()
    monkeypatch.setattr(
        "app.services.openvpn_profile_repair.validate_client_profiles",
        lambda *args, **kwargs: ProfileValidationResult(ready=True, issues=()),
    )

    result = validate_openvpn_profiles(adapter, client_names=["client-a"])

    adapter.recreate_profiles.assert_not_called()
    adapter.add_openvpn_client.assert_not_called()
    assert result.ready
