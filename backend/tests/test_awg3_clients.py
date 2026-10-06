import os
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
    return svc.Awg3Store(conf_dir=tmp_path, client_dir=tmp_path / "client")


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
    assert f"Endpoint = de2.example:{res['port']}" in cfg
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


def test_full_mode_uses_own_subnet_dns_and_default_route(store):
    fake = FakeAwg()
    res = svc.create_client("laptop", mode="full", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert res["mode"] == "full" and res["ip"] == "10.9.1.2"
    cfg = svc.get_client_config("laptop", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert "Address = 10.9.1.2/32" in cfg
    assert "DNS = 10.9.1.1" in cfg
    assert "AllowedIPs = 0.0.0.0/0" in cfg
    assert "10.9.0.0/24" not in cfg


def test_modes_have_independent_ip_pools(store):
    fake = FakeAwg()
    a = svc.create_client("s1", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    f = svc.create_client("f1", mode="full", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert (a["ip"], f["ip"]) == ("10.9.0.2", "10.9.1.2")


def test_unknown_mode_rejected(store):
    with pytest.raises(svc.Awg3ClientError):
        svc.create_client("x", mode="bogus", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=FakeAwg())


def test_legacy_record_without_mode_is_split(store):
    fake = FakeAwg()
    svc.create_client("old", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    data = store.load_clients()
    data["old"].pop("mode")
    store.save_clients(data)
    assert [c["mode"] for c in svc.list_clients(store)] == ["split"]
    assert "AllowedIPs = 10.9.0.0/24" in svc.get_client_config("old", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)


def test_endpoint_falls_back_to_server_host_file(tmp_path, monkeypatch):
    host_file = tmp_path / "server_host"
    host_file.write_text("de9.example.org\n", encoding="utf-8")
    monkeypatch.delenv("AWG3_ENDPOINT_HOST", raising=False)
    monkeypatch.setattr(svc, "SERVER_HOST_FILE", host_file)
    assert svc.endpoint_from_env() == "de9.example.org"


def test_split_list_reads_live_antizapret_ips(tmp_path, monkeypatch):
    ips = tmp_path / "ips"
    ips.write_text(", 198.18.0.0/15, 1.1.1.0/24, 8.8.8.0/24", encoding="utf-8")
    monkeypatch.delenv("AWG3_SPLIT_ALLOWED_FILE", raising=False)
    monkeypatch.setattr(svc, "ANTIZAPRET_IPS_FILE", ips)
    assert svc.split_allowed_from_file() == ["198.18.0.0/15", "1.1.1.0/24", "8.8.8.0/24"]

def test_state_archive_roundtrip(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "awg1.conf").write_text(SERVER_CONF, encoding="utf-8")
    (src / "clients.json").write_text("{}", encoding="utf-8")
    (src / "server.key").write_text("SERVERPRIV=\n", encoding="utf-8")
    (src / "split-allowed.txt").write_text("1.1.1.1/32\n", encoding="utf-8")
    archive = svc.export_state_archive(src)

    dst = tmp_path / "dst"
    monkeypatch.setattr(svc, "UNIT", "awg3@awg1")
    svc.import_state_archive(archive, dst)
    assert (dst / "awg1.conf").read_text(encoding="utf-8") == SERVER_CONF
    assert (dst / "split-allowed.txt").read_text(encoding="utf-8") == "1.1.1.1/32\n"
    if os.name == "posix":
        assert (dst / "server.key").stat().st_mode & 0o777 == 0o600


def test_state_archive_rejects_foreign_member(tmp_path):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b"x"
        info = tarfile.TarInfo("../evil")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(svc.Awg3ClientError):
        svc.import_state_archive(buf.getvalue(), tmp_path / "dst")


def test_state_archive_requires_awg1_conf(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clients.json").write_text("{}", encoding="utf-8")
    archive = svc.export_state_archive(src)
    with pytest.raises(svc.Awg3ClientError):
        svc.import_state_archive(archive, tmp_path / "dst")


def test_mtu_read_from_node_file_and_clamped(tmp_path):
    assert svc.read_mtu(tmp_path) == svc.MTU_DEFAULT
    (tmp_path / "mtu").write_text("1392\n", encoding="utf-8")
    assert svc.read_mtu(tmp_path) == 1392
    (tmp_path / "mtu").write_text("1500", encoding="utf-8")
    assert svc.read_mtu(tmp_path) == 1420
    (tmp_path / "mtu").write_text("900", encoding="utf-8")
    assert svc.read_mtu(tmp_path) == 1280
    (tmp_path / "mtu").write_text("junk", encoding="utf-8")
    assert svc.read_mtu(tmp_path) == svc.MTU_DEFAULT


def test_new_client_gets_random_port_in_range_and_profile_file(store):
    res = svc.create_client("rnd", endpoint_host="vpn.example", split_allowed_ips=SPLIT, store=store, run=FakeAwg())
    assert svc.PORT_RANGE[0] <= res["port"] <= svc.PORT_RANGE[1]
    assert f"Endpoint = vpn.example:{res['port']}" in res["config"]
    profile = store.client_dir / "antizapret" / "antizapret-rnd-awg3.conf"
    assert res["profile"] == str(profile)
    assert profile.read_text(encoding="utf-8") == res["config"]
    if os.name == "posix":
        assert profile.stat().st_mode & 0o777 == 0o600
    assert store.load_clients()["rnd"]["port"] == res["port"]


def test_clients_get_distinct_ports_and_full_mode_uses_vpn_folder(store):
    fake = FakeAwg()
    ports = set()
    for i in range(20):
        ports.add(svc.create_client(f"c{i}", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)["port"])
    assert len(ports) == 20
    full = svc.create_client("full1", mode="full", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert (store.client_dir / "vpn" / "vpn-full1-awg3.conf").exists()
    assert full["port"] not in ports


def test_config_refresh_keeps_stored_port(store):
    fake = FakeAwg()
    res = svc.create_client("keep", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    cfg = svc.get_client_config("keep", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert f"Endpoint = h:{res['port']}" in cfg


def test_delete_removes_profile_file(store):
    svc.create_client("gone", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=FakeAwg())
    profile = store.client_dir / "antizapret" / "antizapret-gone-awg3.conf"
    assert profile.exists()
    svc.delete_client("gone", store=store, run=FakeAwg())
    assert not profile.exists()


def test_legacy_record_without_port_uses_server_port(store):
    fake = FakeAwg()
    svc.create_client("old", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    data = store.load_clients()
    data["old"].pop("port")
    store.save_clients(data)
    assert f"Endpoint = h:{svc.PORT}" in svc.get_client_config("old", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    assert svc.list_clients(store)[0]["port"] == svc.PORT


def test_suspend_removes_peer_and_unsuspend_restores_same_keys(store):
    fake = FakeAwg()
    res = svc.create_client("blk", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)
    before = store.load_clients()["blk"]
    assert svc.suspend_client("blk", store=store, run=fake) is True
    assert "PublicKey = " + before["public_key"] not in store.server_conf.read_text(encoding="utf-8")
    assert any(args == ["awg", "set", svc.IFACE, "peer", before["public_key"], "remove"] for args, _ in fake.calls)
    assert store.load_clients()["blk"]["suspended"] is True
    assert svc.suspend_client("blk", store=store, run=fake) is False
    assert svc.list_clients(store)[0]["suspended"] is True

    assert svc.unsuspend_client("blk", store=store, run=fake) is True
    conf = store.server_conf.read_text(encoding="utf-8")
    assert f"PublicKey = {before['public_key']}" in conf
    assert f"PresharedKey = {before['psk']}" in conf
    restored = store.load_clients()["blk"]
    assert restored["public_key"] == before["public_key"] and restored["ip"] == before["ip"]
    assert restored["suspended"] is False
    assert svc.unsuspend_client("blk", store=store, run=fake) is False
    assert res["ip"] == before["ip"]


def test_suspend_unknown_client_fails_clearly(store):
    with pytest.raises(svc.Awg3ClientError):
        svc.suspend_client("ghost", store=store, run=FakeAwg())
    with pytest.raises(svc.Awg3ClientError):
        svc.unsuspend_client("ghost", store=store, run=FakeAwg())


def test_client_config_copies_awg31_transport_keys_from_server(store):
    text = store.server_conf.read_text(encoding="utf-8")
    store.server_conf.write_text(
        text + "ContentPaddingAddition = 2\nRandomTrailers = true\nDisableCookies = true\n",
        encoding="utf-8",
    )
    cfg = svc.create_client("v31", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=FakeAwg())["config"]
    assert "HeaderProtectionKey = HPK=" in cfg
    assert "ContentPaddingAddition = 2" in cfg
    assert "RandomTrailers = true" in cfg
    assert "DisableCookies = true" in cfg
    assert "MTU = 1280" in cfg


def test_transport_keys_are_shared_not_generated_per_client(store):
    text = store.server_conf.read_text(encoding="utf-8")
    store.server_conf.write_text(text + "ContentPaddingAddition = 2\n", encoding="utf-8")
    fake = FakeAwg()
    a = svc.create_client("a1", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)["config"]
    b = svc.create_client("b1", endpoint_host="h", split_allowed_ips=SPLIT, store=store, run=fake)["config"]
    line = "ContentPaddingAddition = 2"
    assert line in a and line in b
    assert "HeaderProtectionKey = HPK=" in a and "HeaderProtectionKey = HPK=" in b
