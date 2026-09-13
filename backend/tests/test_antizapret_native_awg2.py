"""Native AmneziaWG 2.0 client generation: obfuscation sync, MTU=1280, client.sh option mapping.

Covers the panel-side logic added to integrate with the *native* AmneziaWG 2.0 that setup.sh
compiles (amneziawg-go/amneziawg-tools, files *-am2.conf) instead of the third-party az-awg2
overlay. client.sh itself is bash and cannot run on this platform, so `_run_client_script` is
monkeypatched to fake what it would have written to disk, and the panel-side post-processing
(obfuscation sync + MTU enforcement) is exercised against those fake files directly.
"""

from __future__ import annotations

from pathlib import Path

import app.services.antizapret as antizapret_module
from app.services.antizapret import AntiZapretService, VpnType, _parse_client_names_section

SERVER_CONF = """[Interface]
PrivateKey = server-priv
Address = 10.29.9.1/24
ListenPort = 53443
Jc = 7
Jmin = 60
Jmax = 250
S1 = 12
S2 = 34
S3 = 41
S4 = 8
H1 = 111-222
H2 = 333-444
H3 = 555-666
H4 = 777-888
PostUp = ip link set dev %i txqueuelen 10000

[Peer]
# Client = existing
PublicKey = xyz
AllowedIPs = 10.29.9.2/32
"""

CLIENT_CONF_NO_MTU = """[Interface]
PrivateKey = client-priv
Address = 10.29.9.5/32
DNS = 10.29.9.1
Jc = 5
Jmin = 50
Jmax = 300
S1 = 88
S2 = 121
S3 = 41
S4 = 8
H1 = 305419896-305419996
H2 = 892374510-892374610
H3 = 1439827201-1439827301
H4 = 1985043762-1985043862

[Peer]
PublicKey = server-pub
PresharedKey = psk
Endpoint = vpn.example.com:53443
AllowedIPs = 10.29.9.0/24
PersistentKeepalive = 15
"""


def _make_service(tmp_path: Path) -> AntiZapretService:
    (tmp_path / "client.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return AntiZapretService(base_path=tmp_path)


def test_rewrite_injects_mtu_and_syncs_obfuscation_from_live_server_conf(tmp_path):
    service = _make_service(tmp_path)
    client_file = tmp_path / "client.conf"
    client_file.write_text(CLIENT_CONF_NO_MTU, encoding="utf-8")

    obfuscation = service._read_native_awg2_server_obfuscation_from_text(SERVER_CONF)
    service._rewrite_native_awg2_client_file(client_file, obfuscation)

    result = client_file.read_text(encoding="utf-8")
    lines = result.splitlines()

    assert "MTU = 1280" in lines
    # MTU must land inside [Interface], right after Address, before [Peer].
    assert lines.index("MTU = 1280") < lines.index("[Peer]")
    assert lines.index("MTU = 1280") == lines.index("Address = 10.29.9.5/32") + 1

    # Obfuscation params must now mirror the *live server* conf, not the stale client template.
    assert "Jc = 7" in result
    assert "Jmin = 60" in result
    assert "Jmax = 250" in result
    assert "S1 = 12" in result
    assert "S2 = 34" in result
    assert "H1 = 111-222" in result
    assert "H4 = 777-888" in result

    # Untouched fields survive as-is.
    assert "PresharedKey = psk" in result
    assert "Endpoint = vpn.example.com:53443" in result


def test_rewrite_is_idempotent_when_mtu_already_present(tmp_path):
    service = _make_service(tmp_path)
    client_file = tmp_path / "client.conf"
    client_file.write_text(CLIENT_CONF_NO_MTU.replace("DNS = 10.29.9.1", "DNS = 10.29.9.1\nMTU = 1420"), encoding="utf-8")

    obfuscation = service._read_native_awg2_server_obfuscation_from_text(SERVER_CONF)
    service._rewrite_native_awg2_client_file(client_file, obfuscation)

    result = client_file.read_text(encoding="utf-8")
    assert result.count("MTU = ") == 1
    assert "MTU = 1280" in result
    assert "MTU = 1420" not in result


def test_server_obfuscation_parsing_stops_before_peer_section(tmp_path):
    service = _make_service(tmp_path)
    params = service._read_native_awg2_server_obfuscation_from_text(SERVER_CONF)
    assert params == {
        "Jc": "7",
        "Jmin": "60",
        "Jmax": "250",
        "S1": "12",
        "S2": "34",
        "S3": "41",
        "S4": "8",
        "H1": "111-222",
        "H2": "333-444",
        "H3": "555-666",
        "H4": "777-888",
    }
    # PrivateKey/PublicKey/AllowedIPs must never leak into the "obfuscation params" set.
    assert "PrivateKey" not in params
    assert "AllowedIPs" not in params


def test_add_amneziawg2_client_applies_overrides_to_generated_files(tmp_path, monkeypatch):
    service = _make_service(tmp_path)

    az_dir = tmp_path / "etc-amneziawg"
    az_dir.mkdir()
    (az_dir / "antizapret2.conf").write_text(SERVER_CONF, encoding="utf-8")
    (az_dir / "vpn2.conf").write_text(SERVER_CONF, encoding="utf-8")
    monkeypatch.setattr(antizapret_module, "NATIVE_AWG2_SERVER_DIR", az_dir)
    monkeypatch.setattr(
        antizapret_module,
        "NATIVE_AWG2_TUNNELS",
        {
            "antizapret": (az_dir / "antizapret2.conf", "antizapret"),
            "vpn": (az_dir / "vpn2.conf", "vpn"),
        },
    )

    client_dir = service.client_dir / "amneziawg2" / "antizapret"
    client_dir.mkdir(parents=True)
    fake_profile = client_dir / "antizapret2-testclient-am2.conf"

    calls: list[list[str]] = []

    def fake_run_client_script(self, *args, timeout=120):
        calls.append(list(args))
        # Simulate what client.sh's `render()` would have written for this client.
        fake_profile.write_text(CLIENT_CONF_NO_MTU, encoding="utf-8")
        return "AmneziaWG 2.0 profile files (re)created for client 'testclient'"

    monkeypatch.setattr(AntiZapretService, "_run_client_script", fake_run_client_script)

    output = service.add_amneziawg2_client("testclient")

    assert calls == [["1", "testclient", "3650"]]  # unified add option, not the stale "4"
    assert "testclient" in output

    generated = fake_profile.read_text(encoding="utf-8")
    assert "MTU = 1280" in generated
    assert "Jc = 7" in generated  # synced from the live server conf fixture above


def test_client_sh_option_numbers_match_unified_menu(tmp_path, monkeypatch):
    """Regression test for the bug this patch fixes: the panel used to call options 4/5/6/7,
    which in the *current* unified client.sh mean recreate/backup/restore — not
    add/delete/list-wireguard. Calling "restore" from a "list clients" button would attempt to
    wipe and restore from any stray /root/backup*.tar.gz and reboot the host."""
    service = _make_service(tmp_path)
    calls: list[list[str]] = []

    def fake_run_client_script(self, *args, timeout=120):
        calls.append(list(args))
        return ""

    monkeypatch.setattr(AntiZapretService, "_run_client_script", fake_run_client_script)

    service.add_wireguard_client("alice")
    service.delete_wireguard_client("alice")
    service.list_wireguard_clients()
    service.recreate_profiles()

    assert calls[0] == ["1", "alice", "3650"]
    assert calls[1] == ["8", "alice"]  # WireGuard/AmneziaWG 1.5-only delete, not unified "2"
    assert calls[2] == ["3"]
    assert calls[3] == ["4"]
    for call in calls:
        assert call[0] not in {"5", "6"}, "must never hit backup/restore for a client add/delete/list/recreate action"


def test_parse_client_names_section_splits_unified_list_output():
    output = "\n".join(
        [
            "List clients",
            "",
            "OpenVPN client names:",
            "alice",
            "bob",
            "",
            "WireGuard/AmneziaWG 1.5 client names:",
            "alice",
            "",
            "AmneziaWG 2.0 client names:",
            "alice",
            "carol",
        ]
    )

    assert _parse_client_names_section(output, "OpenVPN client names:") == ["alice", "bob"]
    assert _parse_client_names_section(output, "WireGuard/AmneziaWG 1.5 client names:") == ["alice"]
    assert _parse_client_names_section(output, "AmneziaWG 2.0 client names:") == ["alice", "carol"]


def test_get_profile_files_reads_native_am2_conf(tmp_path):
    service = _make_service(tmp_path)
    az_dir = service.client_dir / "amneziawg2" / "antizapret"
    az_dir.mkdir(parents=True)
    (az_dir / "antizapret2-testclient-am2.conf").write_text(CLIENT_CONF_NO_MTU, encoding="utf-8")

    files = service.get_profile_files("testclient", VpnType.amneziawg2)

    assert len(files) == 1
    assert files[0]["protocol"] == "amneziawg2"
    assert files[0]["filename"] == "antizapret2-testclient-am2.conf"


def test_get_amneziawg2_health_reflects_native_awg_binary(tmp_path, monkeypatch):
    service = _make_service(tmp_path)

    monkeypatch.setattr(antizapret_module.shutil, "which", lambda _name: None)
    assert service.get_amneziawg2_health()["installed"] is False

    monkeypatch.setattr(antizapret_module.shutil, "which", lambda _name: "/usr/bin/awg")
    assert service.get_amneziawg2_health()["installed"] is True
