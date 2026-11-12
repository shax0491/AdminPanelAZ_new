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
