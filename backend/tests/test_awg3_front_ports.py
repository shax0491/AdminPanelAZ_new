"""Failover front for AmneziaWG 3: the whole client UDP range 51900-51999 follows the active member."""

import pytest

from proxy_agent.iptables_dest import (
    AWG3_PORT_FIRST,
    AWG3_PORT_LAST,
    awg3_status_from_rules,
    detect_awg3_destination,
    detect_failover_destination,
    plan_awg3_switch,
    plan_awg3_teardown,
    plan_failover_switch,
)

SPAN = f"{AWG3_PORT_FIRST}:{AWG3_PORT_LAST}"


def _rule_dnat(ip: str, label: str) -> str:
    return (
        f'-A PREROUTING -p udp -m udp --dport {SPAN} -m comment --comment "az-failover-awg3:{label}" '
        f"-j DNAT --to-destination {ip}:{AWG3_PORT_FIRST}-{AWG3_PORT_LAST}"
    )


def _rule_masq(ip: str, label: str) -> str:
    return (
        f'-A POSTROUTING -d {ip}/32 -p udp -m udp --dport {SPAN} -m comment --comment "az-failover-awg3:{label}" '
        "-j MASQUERADE"
    )


def test_first_switch_inserts_dnat_and_masquerade_for_whole_range():
    plan = plan_awg3_switch("", "pool1", "203.0.113.7")
    assert plan[0][:6] == ["iptables", "-w", "-t", "nat", "-A", "PREROUTING"]
    assert "--dport" in plan[0] and plan[0][plan[0].index("--dport") + 1] == SPAN
    assert plan[0][-1] == f"203.0.113.7:{AWG3_PORT_FIRST}-{AWG3_PORT_LAST}"
    assert plan[1][4] == "-A" and plan[1][5] == "POSTROUTING" and plan[1][-1] == "MASQUERADE"


def test_switch_to_other_member_removes_old_pair_first():
    rules = _rule_dnat("198.51.100.1", "pool1") + "\n" + _rule_masq("198.51.100.1", "pool1")
    plan = plan_awg3_switch(rules, "pool1", "203.0.113.7")
    assert [step[4] for step in plan] == ["-D", "-D", "-A", "-A"]
    assert plan[0][-1] == f"198.51.100.1:{AWG3_PORT_FIRST}-{AWG3_PORT_LAST}"
    assert plan[2][-1] == f"203.0.113.7:{AWG3_PORT_FIRST}-{AWG3_PORT_LAST}"


def test_switch_to_current_member_is_a_noop():
    rules = _rule_dnat("203.0.113.7", "pool1")
    assert plan_awg3_switch(rules, "pool1", "203.0.113.7") == []


def test_teardown_removes_current_pair_only():
    rules = _rule_dnat("203.0.113.7", "pool1") + "\n" + _rule_masq("203.0.113.7", "pool1")
    plan = plan_awg3_teardown(rules, "pool1")
    assert [step[4] for step in plan] == ["-D", "-D"]
    assert plan_awg3_teardown("", "pool1") == []


def test_status_reports_destination_and_range():
    rules = _rule_dnat("203.0.113.7", "pool1")
    status = awg3_status_from_rules(rules, "pool1")
    assert status == {
        "label": "pool1",
        "port_range": [AWG3_PORT_FIRST, AWG3_PORT_LAST],
        "destination_ip": "203.0.113.7",
        "installed": True,
    }


def test_awg2_failover_rules_with_same_label_are_not_touched():
    awg2 = (
        '-A PREROUTING -p udp -m udp --dport 53443 -m comment --comment "az-failover:pool1" '
        "-j DNAT --to-destination 198.51.100.9:53443"
    )
    assert detect_awg3_destination(awg2, "pool1") is None
    assert detect_failover_destination(_rule_dnat("203.0.113.7", "pool1"), "pool1") is None
    plan = plan_failover_switch(awg2, "pool1", 53443, "203.0.113.7")
    assert all(f"{SPAN}" not in " ".join(step) for step in plan)


def test_invalid_label_rejected():
    with pytest.raises(ValueError):
        plan_awg3_switch("", "Bad Label!", "203.0.113.7")


def test_awg3_monitoring_view_matches_awg2_page_shape():
    from app.services.awg3_noc import awg3_monitoring_view

    raw = {
        "ifaces": {"split": {"name": "awg1", "up": True, "peers": []}},
        "clients": [
            {"name": "alice", "mode": "split", "online": True, "handshake_age_s": 5, "rx": 10, "tx": 20, "pubkey": "a"},
            {"name": "bob", "mode": "full", "online": False, "handshake_age_s": None, "rx": 0, "tx": 0, "pubkey": "b"},
        ],
    }
    view = awg3_monitoring_view(raw)
    names = {i["name"]: i for i in view["ifaces"]}
    assert set(names) == {"antizapret", "vpn"}
    assert names["antizapret"]["peer_count"] == 1 and names["vpn"]["peer_count"] == 1
    assert names["antizapret"]["port"] == "51821"
    by_name = {c["name"]: c for c in view["clients"]}
    assert by_name["alice"]["iface"] == "antizapret" and by_name["alice"]["online"] is True
    assert by_name["bob"]["iface"] == "vpn" and by_name["bob"]["online"] is False
    assert view["stats_available"] is False
