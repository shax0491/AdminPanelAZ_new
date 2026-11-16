from pathlib import Path

import pytest

from app.services import awg3_clients as svc
from tests.test_awg3_clients import SERVER_CONF, SPLIT, FakeAwg


@pytest.fixture
def v6(tmp_path: Path, monkeypatch):
    """Узел с ENABLE_IPV6=y, режим c, список ips6; профили клиентов - в ipv6-profile.txt."""
    setup = tmp_path / "setup"
    setup.write_text("ENABLE_IPV6=y\nIPV6_MODE=c\nIPV6_PROFILE_DEFAULT=dual\n")
    mode = tmp_path / "ipv6-mode"
    mode.write_text("c\n")
    ips6 = tmp_path / "ips6"
    ips6.write_text(", fd00:30::/64, 2606:4700::/32")
    profiles = tmp_path / "ipv6-profile.txt"
    monkeypatch.setattr(svc, "SETUP_FILE", setup)
    monkeypatch.setattr(svc, "IPV6_MODE_FILE", mode)
    monkeypatch.setattr(svc, "IPV6_IPS_FILE", ips6)
    monkeypatch.setattr(svc, "IPV6_PROFILE_FILE", profiles)
    return SimpleCtx(setup, mode, profiles)


class SimpleCtx:
    def __init__(self, setup, mode, profiles):
        self.setup, self.mode, self.profiles = setup, mode, profiles


@pytest.fixture
def store(tmp_path: Path) -> svc.Awg3Store:
    d = tmp_path / "awg"
    d.mkdir()
    (d / "awg1.conf").write_text(SERVER_CONF, encoding="utf-8")
    return svc.Awg3Store(conf_dir=d, client_dir=d / "client")


def _make(store, name, mode):
    return svc.create_client(name, mode=mode, endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=FakeAwg())


def test_split_dual_client_gets_fake6_and_list_subnets(v6, store):
    res = _make(store, "phone_az", "split")
    cfg = res["config"]
    assert "Address = 10.9.0.2/32, fd29:7::2/128" in cfg
    allowed = [ln for ln in cfg.splitlines() if ln.startswith("AllowedIPs")][0]
    assert "fd00:30::/64" in allowed and "2606:4700::/32" in allowed and "::/0" not in allowed
    peer = store.server_conf.read_text(encoding="utf-8")
    assert "AllowedIPs = 10.9.0.2/32, fd29:7::2/128" in peer


def test_full_dual_client_gets_default_v6_route(v6, store):
    cfg = _make(store, "phone_vpn", "full")["config"]
    assert "Address = 10.9.1.2/32, fd28:7::2/128" in cfg
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in cfg


def test_ipv4_profile_or_disabled_ipv6_gives_plain_v4(v6, store):
    v6.profiles.write_text("phone ipv4\n")
    cfg = _make(store, "phone_az", "split")["config"]
    assert "fd29:7" not in cfg and "::/0" not in cfg
    v6.profiles.write_text("")
    v6.setup.write_text("ENABLE_IPV6=n\nIPV6_MODE=c\n")
    assert "fd28:7" not in _make(store, "tv_vpn", "full")["config"]


def test_mode_b_gives_full_default_but_split_no_v6_routes(v6, store):
    v6.mode.write_text("b\n")
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in _make(store, "a_vpn", "full")["config"]
    split = _make(store, "a_az", "split")["config"]
    assert "Address = 10.9.0.2/32, fd29:7::2/128" in split and "fd00:30" not in split


def test_refresh_switches_profile_live_and_in_server_conf(v6, store):
    _make(store, "dev_az", "split")
    v6.profiles.write_text("dev ipv4\n")
    fake = FakeAwg()
    done = svc.refresh_client_ipv6("dev", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert done == ["dev_az"]
    assert "fd29:7" not in store.server_conf.read_text(encoding="utf-8")
    live = [c for c in fake.calls if c[0][:3] == ["awg", "set", "awg1"]][0][0]
    assert live[-1] == "10.9.0.2/32"
    v6.profiles.write_text("dev dual\n")
    svc.refresh_client_ipv6("dev", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert "AllowedIPs = 10.9.0.2/32, fd29:7::2/128" in store.server_conf.read_text(encoding="utf-8")
    assert "Address = 10.9.0.2/32, fd29:7::2/128" in svc.get_client_config("dev_az", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)


# --- удаление клиента: строка профиля и карта dual (Задача 15) ---

@pytest.fixture
def dual_map(tmp_path: Path, monkeypatch):
    script = tmp_path / "az-dual-clients.sh"
    script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(svc, "DUAL_MAP_SCRIPT", script)
    return script


def _synced(fake):
    return sum(1 for c in fake.calls if c[0][0] == "sh")


def test_delete_with_unreadable_profile_file_still_deletes(tmp_path: Path, store, dual_map, monkeypatch):
    """Профиль нельзя прочитать (у не-root /root недоступен): удаление идёт как раньше, без PermissionError."""
    monkeypatch.setattr(svc, "IPV6_PROFILE_FILE", tmp_path)  # каталог: read_text -> IsADirectoryError (OSError)
    fake = FakeAwg()
    svc.create_client("dev_az", mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    svc.delete_client("dev_az", store=store, run=fake)
    assert "dev_az" not in store.load_clients()


def test_sync_skips_script_in_unreadable_dir(tmp_path: Path, monkeypatch):
    """У не-root (раннер CI) /root недоступен: PermissionError на stat не должен ронять create/delete."""
    script = tmp_path / "az-dual-clients.sh"
    script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(svc, "DUAL_MAP_SCRIPT", script)
    real_stat = svc.os.stat

    def deny(path, *a, **kw):
        if str(path) == str(script):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *a, **kw)

    monkeypatch.setattr(svc.os, "stat", deny)
    fake = FakeAwg()
    svc.sync_dual_map(fake)
    assert _synced(fake) == 0


@pytest.mark.parametrize("profile", ["dual", "ipv4"])
def test_delete_removes_profile_line_and_syncs_map(v6, store, dual_map, profile):
    fake = FakeAwg()
    svc.create_client("dev_az", mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    v6.profiles.write_text(f"# note\nother dual\ndev {profile}\nkeep ipv4\n")
    before = _synced(fake)
    svc.delete_client("dev", store=store, run=fake)
    assert v6.profiles.read_text() == "# note\nother dual\nkeep ipv4\n"
    assert _synced(fake) == before + 1
    assert svc.list_clients(store) == []


def test_recreate_same_name_does_not_inherit_old_profile(v6, store, dual_map):
    v6.setup.write_text("ENABLE_IPV6=y\nIPV6_MODE=c\nIPV6_PROFILE_DEFAULT=ipv4\n")
    v6.profiles.write_text("dev dual\n")
    fake = FakeAwg()
    assert "fd29:7" in _make(store, "dev_az", "split")["config"]
    svc.delete_client("dev", store=store, run=fake)
    cfg = _make(store, "dev_az", "split")["config"]
    assert "fd29:7" not in cfg and svc.client_ipv6_profile("dev") == "ipv4"


def test_delete_paired_client_drops_line_once_both_records_gone(v6, store, dual_map):
    fake = FakeAwg()
    for rec, mode in (("dev_az", "split"), ("dev_vpn", "full")):
        svc.create_client(rec, mode=mode, endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    v6.profiles.write_text("dev dual\nother dual\n")
    svc.delete_client("dev_az", store=store, run=fake)
    assert "dev dual" in v6.profiles.read_text()  # вторая запись клиента ещё жива
    svc.delete_client("dev_vpn", store=store, run=fake)
    assert v6.profiles.read_text() == "other dual\n"


def test_delete_keeps_other_clients_untouched(v6, store, dual_map):
    fake = FakeAwg()
    for rec in ("dev_az", "tv_az"):
        svc.create_client(rec, mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    v6.profiles.write_text("dev dual\ntv ipv4\n")
    tv = store.load_clients()["tv_az"]
    tv_conf = svc._profile_path(store, "split", "tv_az").read_text(encoding="utf-8")
    svc.delete_client("dev", store=store, run=fake)
    assert v6.profiles.read_text() == "tv ipv4\n"
    assert store.load_clients() == {"tv_az": tv}
    assert svc._profile_path(store, "split", "tv_az").read_text(encoding="utf-8") == tv_conf
    assert f"PublicKey = {tv['public_key']}" in store.server_conf.read_text(encoding="utf-8")


def test_delete_sync_failure_reports_error_and_profile_is_clean(v6, store, dual_map):
    fake = FakeAwg()
    svc.create_client("dev_az", mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    v6.profiles.write_text("dev dual\n")

    def failing(args, stdin):
        if args[0] == "sh":
            raise svc.Awg3ClientError("map boom")
        return fake(args, stdin)

    with pytest.raises(svc.Awg3ClientError, match="карта dual-клиентов не обновлена: map boom"):
        svc.delete_client("dev", store=store, run=failing)
    assert v6.profiles.read_text() == ""  # клиента нет, строки нет: состояние не противоречиво


def test_delete_failure_restores_profile_line(v6, store, dual_map):
    fake = FakeAwg()
    svc.create_client("dev_az", mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    v6.profiles.write_text("dev dual\n")

    def failing(args, stdin):
        if args[:3] == ["awg", "set", "awg1"]:
            raise svc.Awg3ClientError("awg boom")
        return fake(args, stdin)

    with pytest.raises(svc.Awg3ClientError, match="awg boom"):
        svc.delete_client("dev", store=store, run=failing)
    assert v6.profiles.read_text() == "dev dual\n"
    assert [c["name"] for c in svc.list_clients(store)] == ["dev_az"]


def test_delete_profile_write_failure_changes_nothing(v6, store, dual_map, monkeypatch):
    fake = FakeAwg()
    svc.create_client("dev_az", mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    v6.profiles.write_text("dev dual\n")

    def boom(_text):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_profile_text", boom)
    peers_before = store.server_conf.read_text(encoding="utf-8")
    with pytest.raises(svc.Awg3ClientError, match="клиент 'dev' не удалён"):
        svc.delete_client("dev", store=store, run=fake)
    assert v6.profiles.read_text() == "dev dual\n"
    assert store.server_conf.read_text(encoding="utf-8") == peers_before
    assert [c["name"] for c in svc.list_clients(store)] == ["dev_az"]
