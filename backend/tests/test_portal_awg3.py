"""Client portal shows AmneziaWG 3.1 profiles the same way as AmneziaWG 2.0."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import VpnType
from app.services import client_portal as portal
from app.services.profile_download_name import build_profile_download_filename
from app.services.vpn_profile_visibility import (
    PROTOCOLS,
    intersect_policy_with_features,
    protocol_key_from_file,
)


def test_awg3_is_a_visibility_protocol_and_follows_its_feature_flag():
    assert "amneziawg3" in PROTOCOLS
    full = {"protocols": sorted(PROTOCOLS), "routes": ["az", "vpn"], "openvpn_groups": []}
    on = intersect_policy_with_features(full, amneziawg3_enabled=True)
    off = intersect_policy_with_features(full, amneziawg3_enabled=False)
    assert "amneziawg3" in on["protocols"]
    assert "amneziawg3" not in off["protocols"]
    assert protocol_key_from_file(protocol="amneziawg3") == "amneziawg3"


def test_awg3_download_names_by_mode():
    assert build_profile_download_filename("ivan", protocol="amneziawg3", variant="antizapret") == "AWG3-AZ-ivan.conf"
    assert build_profile_download_filename("ivan", protocol="amneziawg3", variant="vpn") == "AWG3-VPN-ivan.conf"


def test_awg3_portal_entries_list_both_paired_profiles():
    adapter = MagicMock()
    adapter.awg3_list_clients.return_value = [{"name": "ivan_az", "mode": "split"}, {"name": "ivan_vpn", "mode": "full"}]
    entries = portal._awg3_portal_entries(adapter, "ivan")
    assert [e["path"] for e in entries] == ["awg3:ivan_az", "awg3:ivan_vpn"]
    assert [e["variant"] for e in entries] == ["antizapret", "vpn"]
    assert portal._awg3_portal_entries(adapter, "missing") == []


def test_awg3_protocol_feature_key_and_title():
    assert portal._protocol_feature_key("amneziawg3") == "awg3"
    assert portal._portal_protocol_for_file({"protocol": "amneziawg3"}, SimpleNamespace(vpn_type=VpnType.wireguard)) == "amneziawg3"


def test_policy_write_api_accepts_awg3_and_rejects_unknown():
    import pytest
    from fastapi import HTTPException

    from app.services.vpn_profile_visibility import normalize_policy

    ok = normalize_policy({"routes": ["az"], "protocols": ["amneziawg3"], "openvpn_groups": []}, strict=True)
    assert ok["protocols"] == ["amneziawg3"]
    with pytest.raises(HTTPException):
        normalize_policy({"routes": ["az"], "protocols": ["amneziawg9"], "openvpn_groups": []}, strict=True)
