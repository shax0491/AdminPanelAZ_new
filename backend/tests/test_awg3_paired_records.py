"""One AmneziaWG 3.1 client = two registry records (<name>_az, <name>_vpn), handled as one client."""

import pytest

from app.services import awg3_clients as svc
from app.services.awg3_noc import peers_from_awg3_monitoring

SPLIT = ["1.1.1.1/32"]


class FakeAwg:
    def __init__(self):
        self.calls = []
        self.n = 0

    def __call__(self, args, stdin):
        self.calls.append((args, stdin))
        if args[:2] == ["awg", "genkey"]:
            self.n += 1
            return f"PRIV{self.n}="
        if args[:2] == ["awg", "pubkey"]:
            return "PUB="
        if args[:2] == ["awg", "genpsk"]:
            return f"PSK{self.n}="
        return ""


@pytest.fixture()
def store(tmp_path):
    (tmp_path / "awg1.conf").write_text("[Interface]\nPrivateKey = SERVER=\nListenPort = 51821\n", encoding="utf-8")
    return svc.Awg3Store(conf_dir=tmp_path, client_dir=tmp_path / "client")


def _create_pair(store, fake, name):
    svc.create_client(svc.profile_record(name, "split"), mode="split", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    svc.create_client(svc.profile_record(name, "full"), mode="full", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)


def test_record_names_and_base_name_roundtrip():
    assert svc.profile_record("ivan", "split") == "ivan_az"
    assert svc.profile_record("ivan", "full") == "ivan_vpn"
    assert svc.base_name("ivan_az") == "ivan" and svc.base_name("ivan_vpn") == "ivan"
    assert svc.base_name("legacy") == "legacy"
    assert svc.BASE_NAME_MAX == 28  # 32 minus the 4-char "_vpn" suffix


def test_records_for_pair_and_legacy(store):
    fake = FakeAwg()
    _create_pair(store, fake, "ivan")
    assert svc.records_for("ivan", store) == ["ivan_az", "ivan_vpn"]
    assert svc.records_for("old", store) == ["old"]


def test_delete_by_client_name_removes_both_records_and_files(store):
    fake = FakeAwg()
    _create_pair(store, fake, "ivan")
    svc.delete_client("ivan", store=store, run=fake)
    assert store.load_clients() == {}
    assert not (store.client_dir / "antizapret" / "antizapret-ivan_az-awg3.conf").exists()
    assert not (store.client_dir / "vpn" / "vpn-ivan_vpn-awg3.conf").exists()


def test_suspend_and_unsuspend_by_client_name_cover_both_records(store):
    fake = FakeAwg()
    _create_pair(store, fake, "ivan")
    assert svc.suspend_client("ivan", store=store, run=fake) is True
    clients = store.load_clients()
    assert clients["ivan_az"]["suspended"] is True and clients["ivan_vpn"]["suspended"] is True
    assert svc.suspend_client("ivan", store=store, run=fake) is False
    assert svc.unsuspend_client("ivan", store=store, run=fake) is True
    clients = store.load_clients()
    assert clients["ivan_az"]["suspended"] is False and clients["ivan_vpn"]["suspended"] is False


def test_noc_peers_carry_the_client_name_not_the_record(store):
    payload = {"clients": [
        {"name": "ivan_az", "mode": "split", "pubkey": "a", "handshake_age_s": 5, "rx": 1, "tx": 2, "online": True},
        {"name": "ivan_vpn", "mode": "full", "pubkey": "b", "handshake_age_s": 5, "rx": 3, "tx": 4, "online": True},
    ]}
    peers = peers_from_awg3_monitoring(payload)
    assert {p.client_name for p in peers} == {"ivan"}
    assert {p.interface for p in peers} == {"antizapret3", "vpn3"}
