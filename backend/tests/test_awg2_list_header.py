"""client.sh 3 AmneziaWG 2 header: new (2.0) and old format must both be parsed."""

from __future__ import annotations

from app.services.antizapret import _parse_client_names_section

OUTPUT = """OpenVPN client names:
alice

WireGuard/AmneziaWG 1.5 client names:
bob

AmneziaWG 2.0 client names:
carol
dave
"""


def test_awg2_header_new_and_old_format():
    headers = ("AmneziaWG 2.0 client names:", "AmneziaWG 2 client names:")
    assert _parse_client_names_section(OUTPUT, headers) == ["carol", "dave"]
    assert _parse_client_names_section(OUTPUT.replace("AmneziaWG 2.0", "AmneziaWG 2"), headers) == ["carol", "dave"]
