"""AmneziaWG 3.1 transport keys migrate into existing awg1.conf only when the admin opted in."""

from types import SimpleNamespace

import pytest

from app.services import awg3_clients as svc
from app.services import node_update

OLD_CONF = """[Interface]
PrivateKey = SERVERPRIV=
Address = 10.9.0.1/24
ListenPort = 51821
MTU = 1280
Jc = 4
S1 = 20
S2 = 24
S3 = 12
S4 = 12
HeaderProtectionKey = HPK=

[Peer]
# alice
PublicKey = PUBA=
PresharedKey = PSKA=
AllowedIPs = 10.9.0.2/32
"""


@pytest.fixture()
def store(tmp_path):
    (tmp_path / "awg1.conf").write_text(OLD_CONF, encoding="utf-8")
    return svc.Awg3Store(conf_dir=tmp_path, client_dir=tmp_path / "client")


def _interface_section(text: str) -> str:
    return text.partition("\n[Peer]\n")[0]


def test_migration_adds_keys_inside_interface_and_keeps_peers(store):
    assert svc.migrate_transport31(store) is True
    text = store.server_conf.read_text(encoding="utf-8")
    iface = _interface_section(text)
    assert "ContentPaddingAddition = 2" in iface
    assert "RandomTrailers = on" in iface
    assert "DisableCookies = on" in iface
    peer = text.partition("\n[Peer]\n")[2]
    assert "ContentPaddingAddition" not in peer
    assert "PublicKey = PUBA=" in peer and "AllowedIPs = 10.9.0.2/32" in peer


def test_migration_is_idempotent(store):
    svc.migrate_transport31(store)
    first = store.server_conf.read_text(encoding="utf-8")
    assert svc.migrate_transport31(store) is False
    assert store.server_conf.read_text(encoding="utf-8") == first


def test_migrated_keys_are_copied_into_client_configs(store, monkeypatch):
    svc.migrate_transport31(store)
    def fake(args, stdin):
        if args[:2] == ["awg", "genkey"]:
            return "PRIV="
        if args[:2] == ["awg", "pubkey"]:
            return "PUB="
        return "PSK="

    res = svc.create_client("c31", endpoint_host="h", split_allowed_ips=["1.1.1.1/32"], store=store, run=fake)
    assert "RandomTrailers = on" in res["config"]
    assert "DisableCookies = on" in res["config"]


def test_flag_is_off_by_default(tmp_path):
    assert svc.transport31_enabled(tmp_path) is False
    (tmp_path / "transport31.enable").write_text("", encoding="utf-8")
    assert svc.transport31_enabled(tmp_path) is True


def test_node_update_does_nothing_without_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "AWG3_CONF_DIR", tmp_path)
    called = []
    monkeypatch.setattr(svc, "migrate_transport31", lambda *a, **k: called.append(1) or True)
    assert node_update._apply_transport31_if_enabled() == {"enabled": False, "changed": False}
    assert called == []


def test_node_update_migrates_and_restarts_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "AWG3_CONF_DIR", tmp_path)
    (tmp_path / "transport31.enable").write_text("", encoding="utf-8")
    monkeypatch.setattr(svc, "migrate_transport31", lambda *a, **k: True)
    restarts = []

    def fake_run(args, **kwargs):
        restarts.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(node_update.subprocess, "run", fake_run)
    result = node_update._apply_transport31_if_enabled()
    assert result == {"enabled": True, "changed": True, "restarted": True}
    assert restarts == [["systemctl", "restart", svc.UNIT]]


def test_node_update_reports_migration_error_without_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "AWG3_CONF_DIR", tmp_path)
    (tmp_path / "transport31.enable").write_text("", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("awg1.conf missing")

    monkeypatch.setattr(svc, "migrate_transport31", boom)
    result = node_update._apply_transport31_if_enabled()
    assert result["enabled"] is True and "awg1.conf missing" in result["error"]
