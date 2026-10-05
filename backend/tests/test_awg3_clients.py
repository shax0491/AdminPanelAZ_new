from pathlib import Path

import pytest

from app.services import awg3_clients as svc

SERVER_CONF = """[Interface]
PrivateKey = SERVERPRIV=
Address = 10.9.0.1/24
ListenPort = 51821
MTU = 1280
Jc = 4
Jmin = 8
Jmax = 80
S1 = 20
S2 = 24
S3 = 12
S4 = 12
H1 = 1000-2000
H2 = 3000-4000
H3 = 5000-6000
H4 = 7000-8000
HeaderProtectionKey = HPK=
"""

SPLIT = ["1.1.1.1/32", "198.18.0.0/15"]


class FakeAwg:
    """Stands in for the awg binary: deterministic keys, records calls."""

    def __init__(self):
        self.calls: list[tuple[list[str], str | None]] = []
        self.n = 0

    def __call__(self, args, stdin):
        self.calls.append((args, stdin))
        if args[:2] == ["awg", "genkey"]:
            self.n += 1
            return f"PRIV{self.n}="
        if args[:2] == ["awg", "pubkey"]:
            return f"PUB[{stdin}]"
        if args[:2] == ["awg", "genpsk"]:
            self.n += 1
            return f"PSK{self.n}="
        return ""


@pytest.fixture
def store(tmp_path: Path) -> svc.Awg3Store:
    (tmp_path / "awg1.conf").write_text(SERVER_CONF, encoding="utf-8")
    return svc.Awg3Store(conf_dir=tmp_path)


def test_create_client_writes_server_peer_and_registry(store):
    fake = FakeAwg()
    res = svc.create_client("phone", endpoint_host="de2.example", split_allowed_ips=SPLIT, store=store, run=fake)
    assert res["ip"] == "10.9.0.2"
    assert "[Peer]\n# phone\n" in store.server_conf.read_text(encoding="utf-8")
    assert "phone" in store.load_clients()
    set_call = [c for c in fake.calls if c[0][:3] == ["awg", "set", "awg1"]]
    assert set_call, "peer must be added to the live interface with awg set"


def test_client_config_has_split_allowed_ips_and_obfuscation(store):
    fake = FakeAwg()
    res = svc.create_client("router", endpoint_host="de2.example", split_allowed_ips=SPLIT, store=store, run=fake)
    cfg = res["config"]
    assert "Address = 10.9.0.2/32" in cfg
    assert "DNS = 10.9.0.1" in cfg
    assert "AllowedIPs = 10.9.0.0/24, 1.1.1.1/32, 198.18.0.0/15" in cfg
    assert "Endpoint = de2.example:51821" in cfg
    for line in ["Jc = 4", "S4 = 12", "H1 = 1000-2000", "HeaderProtectionKey = HPK="]:
        assert line in cfg
    assert "0.0.0.0/0" not in cfg


def test_duplicate_name_rejected(store):
    fake = FakeAwg()
    svc.create_client("dup", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    with pytest.raises(svc.Awg3ClientError):
        svc.create_client("dup", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)


def test_invalid_name_rejected(store):
    with pytest.raises(svc.Awg3ClientError):
        svc.create_client("bad name!", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=FakeAwg())


def test_ips_increment(store):
    fake = FakeAwg()
    a = svc.create_client("a", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    b = svc.create_client("b", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert (a["ip"], b["ip"]) == ("10.9.0.2", "10.9.0.3")


def test_delete_removes_live_peer_config_block_and_registry(store):
    fake = FakeAwg()
    svc.create_client("keep", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    svc.create_client("gone", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    gone_pub = store.load_clients()["gone"]["public_key"]
    svc.delete_client("gone", store=store, run=fake)
    text = store.server_conf.read_text(encoding="utf-8")
    assert "# gone" not in text and gone_pub not in text
    assert "# keep" in text
    assert "gone" not in store.load_clients() and "keep" in store.load_clients()
    assert any(c[0][:4] == ["awg", "set", "awg1", "peer"] and c[0][-1] == "remove" for c in fake.calls)


def test_list_and_get_config(store):
    fake = FakeAwg()
    svc.create_client("x", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert [c["name"] for c in svc.list_clients(store)] == ["x"]
    assert "Address = 10.9.0.2/32" in svc.get_client_config("x", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    with pytest.raises(svc.Awg3ClientError):
        svc.get_client_config("nope", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)


def test_client_allowed_ips_include_server_subnet(store):
    fake = FakeAwg()
    res = svc.create_client("subnet", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert "AllowedIPs = 10.9.0.0/24, 1.1.1.1/32, 198.18.0.0/15" in res["config"]
