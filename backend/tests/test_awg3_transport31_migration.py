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


def test_ensure_skips_node_without_awg3_layer(tmp_path):
    empty = svc.Awg3Store(conf_dir=tmp_path, client_dir=tmp_path / "client")
    assert svc.ensure_transport31(empty) == {"skipped": True, "changed": False, "restarted": False, "error": None}


def test_ensure_migrates_and_restarts_unit_once(store, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    first = svc.ensure_transport31(store)
    assert first == {"skipped": False, "changed": True, "restarted": True, "error": None}
    assert calls == [["systemctl", "restart", svc.UNIT]]
    second = svc.ensure_transport31(store)
    assert second == {"skipped": False, "changed": False, "restarted": False, "error": None}
    assert len(calls) == 1


def test_ensure_reports_restart_failure(store, monkeypatch):
    monkeypatch.setattr(svc.subprocess, "run", lambda args, **kw: SimpleNamespace(returncode=1, stderr="unit failed"))
    result = svc.ensure_transport31(store)
    assert result["changed"] is True and result["restarted"] is False and "unit failed" in result["error"]
