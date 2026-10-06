"""Online status in traffic monitoring is per (client, protocol family), never per client name alone."""

from app.routers.traffic import _filter_session_keys
from app.services.traffic.active_clients import protocol_family, session_key


def test_family_collapses_openvpn_transports_and_keeps_awg_families():
    assert protocol_family("openvpn-udp") == "openvpn"
    assert protocol_family("openvpn-tcp") == "openvpn"
    assert protocol_family("openvpn") == "openvpn"
    assert protocol_family("amneziawg2") == "amneziawg2"
    assert protocol_family("amneziawg3") == "amneziawg3"
    assert protocol_family("wireguard") == "wireguard"


def test_session_key_is_case_insensitive_on_name():
    assert session_key("Ivan", "amneziawg2") == session_key(" ivan ", "amneziawg2") == "ivan|amneziawg2"


def test_awg2_online_does_not_mark_same_client_openvpn_or_wireguard_online():
    online = {session_key("ivan", "amneziawg2")}
    assert session_key("ivan", protocol_family("openvpn-udp")) not in online
    assert session_key("ivan", protocol_family("wireguard")) not in online
    assert session_key("ivan", protocol_family("amneziawg2")) in online


def test_session_filter_keeps_only_allowed_clients():
    keys = {"ivan|amneziawg2", "anna|openvpn"}
    assert _filter_session_keys(keys, None) == keys
    assert _filter_session_keys(keys, {"Ivan"}) == {"ivan|amneziawg2"}
