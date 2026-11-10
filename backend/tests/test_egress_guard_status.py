import json
from types import SimpleNamespace

import pytest
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Node, NodeStatus

from app.services.egress_guard_status import (
    collect_egress_status, evaluate_guard, layer_v6_status,
    parse_az_guard_conf, parse_handshake_ages, proton_profile_ipv6_info,
)

NOW = 1_700_000_200.0

FORWARD_OK = "-P FORWARD ACCEPT\n-A FORWARD -j AZ_EGRESS_GUARD\n-A FORWARD -j ACCEPT\n"
MANGLE_OK = "-P PREROUTING ACCEPT\n-A PREROUTING -j AZ_EGRESS_MARK\n"
NAT_OK = "-P POSTROUTING ACCEPT\n-A POSTROUTING -j AZ_EGRESS_NAT\n"
IP_RULE_OK = "0:\tfrom all lookup local\n900:\tfrom all fwmark 0x1f40 lookup 100\n32766:\tfrom all lookup main\n"
TABLE100_OK = ("default dev proton metric 100\n"
               "default dev warp-antizapret metric 200\n"
               "blackhole default metric 2000\n")
INPUT_OK = "-P INPUT ACCEPT\n-A INPUT -p tcp -m tcp --dport 9100 -j DROP /* az-panel-only */\n"

CONF_OK = '''
# az-guard
HAS_IPV6=1
EGRESS_IFACES="proton warp-antizapret"
INGRESS_IPS="203.0.113.10 198.51.100.4"
LAYERS="awg:wg0:10.8.0.0/24,10.9.0.0/24:fd42:1::/64 ovpn:tun0:10.10.0.0/24: broken:wg9"
BASE_RULES="-4|oif proton lookup 51820 priority 1000
-4|oif warp-antizapret lookup 5000 priority 1001
-6|oif proton lookup 51820 priority 1000"
'''

PROFILE_V6 = """
[Interface]
PrivateKey = SECRET_PRIVATE_KEY_DO_NOT_LEAK
Address = 10.2.0.2/32, fd42:dead:beef::2/128
[Peer]
PublicKey = PUBKEY
PresharedKey = SECRET_PSK_DO_NOT_LEAK
AllowedIPs = 0.0.0.0/0, ::/0
"""

PROFILE_V4 = """
[Interface]
PrivateKey = SECRET_PRIVATE_KEY_DO_NOT_LEAK
Address = 10.3.0.2/32
[Peer]
AllowedIPs = 0.0.0.0/0
"""


class FakeRunner:
    def __init__(self, files=None, cmds=None):
        self.files = files or {}
        self.cmds = cmds or {}
        self.calls = []

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        key = tuple(argv)
        if key in self.cmds:
            return self.cmds[key]
        if argv[0] == "cat" and len(argv) == 2:
            if argv[1] in self.files:
                return 0, self.files[argv[1]]
            return 1, "cat: No such file or directory"
        return 127, f"{argv[0]}: not found"


def make_runner(conf=CONF_OK, table100=TABLE100_OK, forward=FORWARD_OK):
    return FakeRunner(
        files={
            "/etc/az-guard.conf": conf,
            "/etc/wireguard/proton.conf": PROFILE_V6,
            "/etc/wireguard/warp-antizapret.conf": PROFILE_V4,
        },
        cmds={
            ("iptables", "-w", "-S", "FORWARD"): (0, forward),
            ("iptables", "-w", "-t", "mangle", "-S", "PREROUTING"): (0, MANGLE_OK),
            ("iptables", "-w", "-t", "nat", "-S", "POSTROUTING"): (0, NAT_OK),
            ("ip6tables", "-w", "-S", "FORWARD"): (0, FORWARD_OK),
            ("ip6tables", "-w", "-t", "mangle", "-S", "PREROUTING"): (0, MANGLE_OK),
            ("ip6tables", "-w", "-t", "nat", "-S", "POSTROUTING"): (0, NAT_OK),
            ("ip", "rule"): (0, IP_RULE_OK),
            ("ip", "route", "show", "table", "100"): (0, table100),
            ("iptables", "-w", "-S", "INPUT"): (0, INPUT_OK),
            ("ip", "link", "show", "azr1"): (0, "5: azr1: <POINTOPOINT,NOARP,UP> mtu 1420 qdisc noqueue state UNKNOWN"),
            ("wg", "show", "proton", "latest-handshakes"): (0, "PEERPROTON1\t1700000100\n"),
            ("wg", "show", "warp-antizapret", "latest-handshakes"): (0, "PEERWARP1\t1700000050\n"),
            ("wg", "show", "azr1", "latest-handshakes"): (0, "PEERREV1\t1700000150\n"),
        },
    )


# --------------------------- парсеры конфига ---------------------------

def test_parse_conf_full():
    conf = parse_az_guard_conf(CONF_OK)
    assert conf["has_ipv6"] is True
    assert conf["egress_ifaces"] == ["proton", "warp-antizapret"]
    assert conf["ingress_ips"] == ["203.0.113.10", "198.51.100.4"]
    assert len(conf["layers"]) == 2
    awg = conf["layers"][0]
    assert awg["name"] == "awg" and awg["iface"] == "wg0"
    assert awg["v4"] == ["10.8.0.0/24", "10.9.0.0/24"]
    assert awg["v6"] == ["fd42:1::/64"]
    assert conf["layers"][1]["v6"] == []  # пустой v6 у слоя — ок
    assert conf["base_rules"][0] == {"family": "inet", "args": "oif proton lookup 51820 priority 1000"}
    assert conf["base_rules"][2]["family"] == "inet6"
    assert "bad LAYERS entry: broken:wg9" in conf["errors"]


def test_parse_conf_no_ipv6_and_crlf():
    conf = parse_az_guard_conf("HAS_IPV6=0\r\nEGRESS_IFACES=proton\r\n")
    assert conf["has_ipv6"] is False
    assert conf["egress_ifaces"] == ["proton"]


def test_parse_conf_bad_layer_no_name():
    conf = parse_az_guard_conf('LAYERS=":wg0:10.0.0.0/24"\n')
    assert conf["layers"] == []
    assert conf["errors"]


# --------------------------- proton-профиль / v6 ---------------------------

def test_proton_profile_ipv6_info():
    info = proton_profile_ipv6_info(PROFILE_V6)
    assert info["has_v6"] is True
    assert "fd42:dead:beef::2/128" in info["addresses_v6"]
    assert "::/0" in info["allowed_v6"]
    info4 = proton_profile_ipv6_info(PROFILE_V4)
    assert info4["has_v6"] is False


def test_proton_profile_does_not_return_keys():
    info = proton_profile_ipv6_info(PROFILE_V6)
    assert "SECRET" not in json.dumps(info)


def test_layer_v6_status():
    assert layer_v6_status(has_ipv6=False, layer_v6=["::/0"], profile_has_v6=True) == "disabled"
    assert layer_v6_status(has_ipv6=True, layer_v6=[], profile_has_v6=True) == "no_v6_subnets"
    assert layer_v6_status(has_ipv6=True, layer_v6=["::/0"], profile_has_v6=False) == "profile_without_v6"
    assert layer_v6_status(has_ipv6=True, layer_v6=["::/0"], profile_has_v6=None) == "profile_missing"
    assert layer_v6_status(has_ipv6=True, layer_v6=["::/0"], profile_has_v6=True) == "active"


# --------------------------- evaluate_guard ---------------------------

def test_evaluate_guard_standing():
    r = evaluate_guard(
        forward_s=FORWARD_OK, mangle_pre_s=MANGLE_OK, nat_post_s=NAT_OK,
        ip_rule=IP_RULE_OK, table100=TABLE100_OK, has_ipv6=False,
    )
    assert r["guard_active"] is True
    assert r["jump_first"]["filter"] is True
    assert r["fwmark_rule"] is True
    assert r["blackhole"] is True
    assert r["egress_routes"] == {"proton": "live", "warp-antizapret": "live"}
    assert r["jump_first"]["filter6"] is None


def test_evaluate_guard_jump_displaced():
    forward = "-P FORWARD ACCEPT\n-A FORWARD -j DOCKER-USER\n-A FORWARD -j AZ_EGRESS_GUARD\n"
    r = evaluate_guard(
        forward_s=forward, mangle_pre_s=MANGLE_OK, nat_post_s=NAT_OK,
        ip_rule=IP_RULE_OK, table100=TABLE100_OK, has_ipv6=False,
    )
    assert r["jump_first"]["filter"] is False
    assert r["guard_active"] is False


def test_evaluate_guard_egress_dead():
    table = "default dev proton metric 5000\nblackhole default metric 2000\n"
    r = evaluate_guard(
        forward_s=FORWARD_OK, mangle_pre_s=MANGLE_OK, nat_post_s=NAT_OK,
        ip_rule=IP_RULE_OK, table100=table, has_ipv6=False,
    )
    assert r["egress_routes"]["proton"] == "dead"


def test_evaluate_guard_empty_inputs():
    r = evaluate_guard(
        forward_s="", mangle_pre_s="", nat_post_s="", ip_rule="", table100="",
        has_ipv6=False,
    )
    assert r["guard_active"] is False
    assert r["jump_first"]["filter"] is False


def test_evaluate_guard_no_fwmark_no_blackhole():
    r = evaluate_guard(
        forward_s=FORWARD_OK, mangle_pre_s=MANGLE_OK, nat_post_s=NAT_OK,
        ip_rule="0:\tfrom all lookup local\n", table100="default dev proton metric 100\n",
        has_ipv6=False,
    )
    assert r["fwmark_rule"] is False
    assert r["blackhole"] is False
    assert r["guard_active"] is False


# --------------------------- handshakes ---------------------------

def test_parse_handshake_ages_min_and_zero():
    text = "proton\tPEER1\t1700000100\r\nproton\tPEER2\t0\nwarp\tPEER3\t1700000000\n"
    ages = parse_handshake_ages(text, NOW)
    assert ages["proton"] == 100.0
    assert ages["warp"] == 200.0
    assert parse_handshake_ages("proton\tP\t0\n", NOW)["proton"] is None
    assert parse_handshake_ages("", NOW) == {}
    assert parse_handshake_ages("garbage\n", NOW) == {}


# --------------------------- collect ---------------------------

def test_collect_full_guard_standing():
    st = collect_egress_status(make_runner(), now=NOW)
    assert st["installed"] is True
    assert st["guard_active"] is True
    assert st["jump_first"]["filter6"] is True
    assert st["egress_routes"] == {"proton": "live", "warp-antizapret": "live"}
    assert st["reverse"] == {"iface": "azr1", "exists": True, "handshake_age": 50.0}
    assert st["handshake_ages"]["proton"] == 100.0
    assert st["panel_isolation"] is True
    assert st["egress_ifaces_v6"] == {"proton": True, "warp-antizapret": False}
    by_name = {l["name"]: l["v6_status"] for l in st["layers_v6"]}
    assert by_name["awg"] == "active"
    assert by_name["ovpn"] == "no_v6_subnets"
    assert st["errors"] == ["bad LAYERS entry: broken:wg9"]


def test_collect_no_conf():
    st = collect_egress_status(FakeRunner(), now=NOW)
    assert st == {"installed": False, "errors": []}


def test_collect_no_ipv6_skips_ip6tables():
    conf = "HAS_IPV6=0\nEGRESS_IFACES=proton\n"
    runner = make_runner(conf=conf)
    st = collect_egress_status(runner, now=NOW)
    assert st["installed"] is True
    assert st["has_ipv6"] is False
    assert st["jump_first"]["filter6"] is None
    assert not any(c[0] == "ip6tables" for c in runner.calls)


def test_collect_missing_egress_iface_marked():
    table = "default dev warp-antizapret metric 200\nblackhole default metric 2000\n"
    st = collect_egress_status(make_runner(table100=table), now=NOW)
    assert st["egress_routes"]["proton"] == "missing"
    assert st["egress_routes"]["warp-antizapret"] == "live"


def test_collect_openvpn_tun_no_wg_is_quiet():
    runner = make_runner()
    # tun0 — OpenVPN, wg-команды для него не вызываются; wg на proton отвечает ошибкой
    runner.cmds[("wg", "show", "proton", "latest-handshakes")] = (1, "")
    st = collect_egress_status(runner, now=NOW)
    assert st["handshake_ages"].get("proton") is None
    assert not any("wg:" in e for e in st["errors"])


def test_collect_runner_exception_recorded_not_raised():
    def boom(argv):
        raise RuntimeError("agent dead")

    st = collect_egress_status(boom, now=NOW)
    assert st["installed"] is False
    assert st["errors"]


def test_collect_partial_failure_keeps_result():
    runner = make_runner()
    runner.cmds[("iptables", "-w", "-S", "FORWARD")] = (1, "resource busy")
    st = collect_egress_status(runner, now=NOW)
    assert st["installed"] is True
    assert st["jump_first"]["filter"] is False
    assert st["guard_active"] is False
    assert any("iptables" in e for e in st["errors"])


def test_no_secret_leak_in_result():
    st = collect_egress_status(make_runner(), now=NOW)
    blob = json.dumps(st)
    assert "SECRET_PRIVATE_KEY_DO_NOT_LEAK" not in blob
    assert "SECRET_PSK_DO_NOT_LEAK" not in blob
    assert "PrivateKey" not in blob
    assert "PresharedKey" not in blob


def test_collect_reverse_absent():
    runner = make_runner()
    runner.cmds[("ip", "link", "show", "azr1")] = (1, "")
    runner.cmds[("wg", "show", "azr1", "latest-handshakes")] = (1, "")
    st = collect_egress_status(runner, now=NOW)
    assert st["reverse"]["exists"] is False
    assert st["reverse"]["handshake_age"] is None


# --------------------------- роут ---------------------------
# Паттерн репозитория (test_reverse_panel): sqlite-фикстура + прямой вызов функции роута.

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _node(db, kind="vpn"):
    item = Node(name="n", host="10.0.0.2", port=9100, api_key_hash="h", api_key_encrypted="e",
                is_local=False, transport="http", node_kind=kind, status=NodeStatus.online,
                node_metadata="{}", created_at=datetime.utcnow())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class _Adapter:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def egress_status(self):
        if self.exc:
            raise self.exc
        return self.result


def _call(db, monkeypatch, node_id, adapter=None, nodes_enabled=True):
    from app.routers import nodes as r
    monkeypatch.setattr(r, "is_nodes_enabled", lambda _db: nodes_enabled)
    monkeypatch.setattr(r, "get_adapter_for_node", lambda _n: adapter)
    return r.node_egress_status(node_id, None, db)


def test_route_returns_adapter_payload(db, monkeypatch):
    n = _node(db)
    payload = {"installed": True, "guard_active": True}
    assert _call(db, monkeypatch, n.id, _Adapter(payload)) == payload


def test_route_403_module_disabled(db, monkeypatch):
    n = _node(db)
    with pytest.raises(HTTPException) as e:
        _call(db, monkeypatch, n.id, _Adapter({}), nodes_enabled=False)
    assert e.value.status_code == 403


def test_route_409_proxy(db, monkeypatch):
    n = _node(db, kind="proxy")
    with pytest.raises(HTTPException) as e:
        _call(db, monkeypatch, n.id, _Adapter({}))
    assert e.value.status_code == 409


def test_route_409_adapter_without_support(db, monkeypatch):
    n = _node(db)
    with pytest.raises(HTTPException) as e:
        _call(db, monkeypatch, n.id, object())
    assert e.value.status_code == 409


def test_route_404_unknown_node(db, monkeypatch):
    with pytest.raises(HTTPException) as e:
        _call(db, monkeypatch, 999, _Adapter({}))
    assert e.value.status_code == 404


def test_route_is_admin_only_and_registered():
    from app.routers import nodes as r
    route = next(x for x in r.router.routes if x.path == "/nodes/{node_id}/egress-status")
    deps = [d.call for d in route.dependant.dependencies]
    assert r.require_admin in deps
