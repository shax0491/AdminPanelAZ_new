"""Native AmneziaWG 2.0 runtime: block/unblock peer sync and `awg show dump` monitoring.

Covers app/services/native_awg2_runtime.py, added to replace the retired az-awg2 overlay's
block/unblock and monitoring for the *-am2.conf clients that client.sh generates natively.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.services.native_awg2_runtime as native_awg2_runtime
from app.services.native_awg2_runtime import (
    _load_native_peer_info,
    _sync_interface_from_stripped_config,
    block_client_runtime,
    get_client_stats,
    get_monitoring,
    sync_all_native_awg2_interfaces,
    unblock_client_runtime,
)

SERVER_CONF = """[Interface]
PrivateKey = server-priv
Address = 10.29.9.1/24
ListenPort = 53443
Jc = 5
Jmin = 50
Jmax = 300

# Client = alice
# PrivateKey = alice-priv
[Peer]
PublicKey = alice-pub
PresharedKey = alice-psk
AllowedIPs = 10.29.9.2/32

# Client = bob
# PrivateKey = bob-priv
[Peer]
PublicKey = bob-pub
PresharedKey = bob-psk
AllowedIPs = 10.29.9.3/32
"""


def _write_server_conf(tmp_path: Path, name: str = "antizapret2.conf") -> Path:
    path = tmp_path / name
    path.write_text(SERVER_CONF, encoding="utf-8")
    return path


def test_load_native_peer_info_maps_pubkey_to_name_and_allowed_ips(tmp_path, monkeypatch):
    antizapret_conf = _write_server_conf(tmp_path, "antizapret2.conf")
    vpn_conf = tmp_path / "vpn2.conf"
    vpn_conf.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        native_awg2_runtime,
        "NATIVE_AWG2_CONFIG_FILES",
        {"antizapret": antizapret_conf, "vpn": vpn_conf},
    )

    info = _load_native_peer_info()

    assert info["alice-pub"] == {"name": "alice", "allowed_ips": "10.29.9.2/32"}
    assert info["bob-pub"] == {"name": "bob", "allowed_ips": "10.29.9.3/32"}


def _dump_row(pubkey: str, *, handshake: int, rx: int, tx: int, endpoint: str = "1.2.3.4:51820") -> str:
    # awg/wg show <iface> dump columns: pubkey psk endpoint allowed-ips handshake rx tx keepalive
    return "\t".join([pubkey, "(none)", endpoint, "10.29.9.2/32", str(handshake), str(rx), str(tx), "15"])


def test_get_monitoring_marks_recent_handshake_online(tmp_path, monkeypatch):
    antizapret_conf = _write_server_conf(tmp_path, "antizapret2.conf")
    vpn_conf = tmp_path / "vpn2.conf"
    vpn_conf.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        native_awg2_runtime,
        "NATIVE_AWG2_CONFIG_FILES",
        {"antizapret": antizapret_conf, "vpn": vpn_conf},
    )

    now = 1_700_000_000
    monkeypatch.setattr(native_awg2_runtime, "time", SimpleNamespace(time=lambda: now))

    header = "server-priv\tserver-pub\t53443\toff"
    online_row = _dump_row("alice-pub", handshake=now - 30, rx=100, tx=200)
    stale_row = _dump_row("bob-pub", handshake=now - 999, rx=5, tx=5)

    def fake_dump(iface, **_kwargs):
        # Must receive the real interface name (antizapret2/vpn2), not the "antizapret"/"vpn"
        # label used everywhere else — that was the bug (monitoring always reported 0 peers).
        assert iface in ("antizapret2", "vpn2")
        if iface == "antizapret2":
            return "\n".join([header, online_row, stale_row])
        return ""

    monkeypatch.setattr(native_awg2_runtime, "_awg_show_dump", fake_dump)
    monkeypatch.setattr(native_awg2_runtime, "_lookup_local_geo_for_endpoint", lambda _ep: None)

    data = get_monitoring()

    by_name = {c["name"]: c for c in data["clients"]}
    assert by_name["alice"]["online"] is True
    assert by_name["alice"]["rx"] == 100
    assert by_name["alice"]["tx"] == 200
    assert by_name["alice"]["allowed_ips"] == "10.29.9.2/32"
    assert by_name["bob"]["online"] is False
    assert data["ifaces"] == [
        {"name": "antizapret", "peer_count": 2, "up": True},
        {"name": "vpn", "peer_count": 0, "up": False},
    ]
    assert data["stats_available"] is False


def test_get_client_stats_aggregates_across_interfaces_and_reports_missing(tmp_path, monkeypatch):
    antizapret_conf = _write_server_conf(tmp_path, "antizapret2.conf")
    vpn_conf = tmp_path / "vpn2.conf"
    vpn_conf.write_text(SERVER_CONF.replace("alice-pub", "alice-pub-vpn"), encoding="utf-8")
    monkeypatch.setattr(
        native_awg2_runtime,
        "NATIVE_AWG2_CONFIG_FILES",
        {"antizapret": antizapret_conf, "vpn": vpn_conf},
    )
    now = 1_700_000_000
    monkeypatch.setattr(native_awg2_runtime, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(native_awg2_runtime, "_lookup_local_geo_for_endpoint", lambda _ep: {"country": "XX"})

    header = "x\tx\tx\tx"

    def fake_dump(iface, **_kwargs):
        assert iface in ("antizapret2", "vpn2")
        if iface == "antizapret2":
            return "\n".join([header, _dump_row("alice-pub", handshake=now - 10, rx=10, tx=20)])
        if iface == "vpn2":
            return "\n".join([header, _dump_row("alice-pub-vpn", handshake=now - 10, rx=1, tx=2)])
        return ""

    monkeypatch.setattr(native_awg2_runtime, "_awg_show_dump", fake_dump)

    stats = get_client_stats("alice")
    assert stats["online"] is True
    assert stats["rx_life"] == 11
    assert stats["tx_life"] == 22
    assert stats["geo"] == {"country": "XX"}

    assert get_client_stats("nobody") is None


def test_block_client_runtime_removes_every_matching_peer(monkeypatch):
    # _collect_client_peers (via _peer_specs_for_client) resolves labels to the real
    # antizapret2/vpn2 interface names before block_client_runtime ever sees them.
    monkeypatch.setattr(
        native_awg2_runtime,
        "_collect_client_peers",
        lambda name, **_kw: [("antizapret2", "alice-pub"), ("vpn2", "alice-pub-vpn")],
    )
    calls: list[list[str]] = []

    def fake_run(args, timeout=native_awg2_runtime.COMMAND_TIMEOUT_SECONDS):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_awg2_runtime, "_run", fake_run)

    result = block_client_runtime("alice")

    assert result["success"] is True
    assert result["removed_count"] == 2
    assert calls == [
        ["awg", "set", "antizapret2", "peer", "alice-pub", "remove"],
        ["awg", "set", "vpn2", "peer", "alice-pub-vpn", "remove"],
    ]


def test_peer_specs_for_client_resolve_real_interface_names(tmp_path, monkeypatch):
    """Regression: block/unblock and monitoring all ultimately depend on
    _peer_specs_for_client/_collect_client_peers producing the REAL antizapret2/vpn2 interface
    name, not the "antizapret"/"vpn" label used as the config-file dict key — passing the label
    straight to `awg` silently targets a nonexistent interface."""
    antizapret_conf = _write_server_conf(tmp_path, "antizapret2.conf")
    vpn_conf = tmp_path / "vpn2.conf"
    vpn_conf.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        native_awg2_runtime,
        "NATIVE_AWG2_CONFIG_FILES",
        {"antizapret": antizapret_conf, "vpn": vpn_conf},
    )

    peers = native_awg2_runtime._collect_client_peers("alice")

    assert peers == [("antizapret2", "alice-pub")]


def test_block_client_runtime_no_peers_found():
    result = block_client_runtime("ghost-client-name-not-in-any-conf")
    assert result["success"] is False
    assert result["error_count"] == 1


def test_unblock_client_runtime_restores_persisted_peer_spec(monkeypatch):
    monkeypatch.setattr(
        native_awg2_runtime,
        "_peer_specs_for_client",
        lambda name, **_kw: [
            {
                "interface_name": "antizapret2",
                "peer_public_key": "alice-pub",
                "allowed_ips": "10.29.9.2/32",
                "preshared_key": "",
            }
        ],
    )
    calls: list[list[str]] = []

    def fake_run(args, timeout=native_awg2_runtime.COMMAND_TIMEOUT_SECONDS):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_awg2_runtime, "_run", fake_run)

    result = unblock_client_runtime("alice")

    assert result["success"] is True
    assert result["restored"] == 1
    assert calls == [["awg", "set", "antizapret2", "peer", "alice-pub", "allowed-ips", "10.29.9.2/32"]]


def test_sync_interface_from_stripped_config_strips_the_full_path_not_bare_name(monkeypatch):
    # Regression: `awg-quick strip <bare interface name>` resolves against
    # awg-quick's own compiled-in default directory (/etc/amnezia/amneziawg on
    # this build) — NOT /etc/amneziawg where the native config actually lives.
    # That silently failed with "does not exist" every single time this ran;
    # only surfaced once a caller started checking the return value instead of
    # just logging it. Must pass the full config path instead.
    calls: list[list[str]] = []

    def fake_run(args, timeout=native_awg2_runtime.COMMAND_TIMEOUT_SECONDS):
        calls.append(args)
        if args[:2] == ["awg-quick", "strip"]:
            return SimpleNamespace(returncode=0, stdout="[Interface]\nPrivateKey=x\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_awg2_runtime, "_run", fake_run)

    ok, err = _sync_interface_from_stripped_config("antizapret2")

    assert ok is True, err
    assert calls[0] == ["awg-quick", "strip", str(Path("/etc/amneziawg/antizapret2.conf"))]
    assert calls[1][:2] == ["awg", "syncconf"]
    assert calls[1][2] == "antizapret2"


def test_sync_all_native_awg2_interfaces_uses_full_paths_for_both_ifaces(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, timeout=native_awg2_runtime.COMMAND_TIMEOUT_SECONDS):
        calls.append(args)
        if args[:2] == ["awg-quick", "strip"]:
            return SimpleNamespace(returncode=0, stdout="[Interface]\nPrivateKey=x\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_awg2_runtime, "_run", fake_run)

    result = sync_all_native_awg2_interfaces()

    assert result["success"] is True
    strip_targets = [c[2] for c in calls if c[:2] == ["awg-quick", "strip"]]
    expected = [str(Path("/etc/amneziawg/antizapret2.conf")), str(Path("/etc/amneziawg/vpn2.conf"))]
    assert sorted(strip_targets) == sorted(expected)
