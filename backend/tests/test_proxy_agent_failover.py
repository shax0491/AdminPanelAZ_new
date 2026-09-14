"""Unit tests for proxy_agent failover-front rules (independent of proxy.sh DESTINATION).

No live net / iptables — pure text-in/argv-out like test_proxy_agent_destination.py.
"""

from __future__ import annotations

import pytest

from proxy_agent.iptables_dest import (
    detect_failover_destination,
    failover_status_from_rules,
    plan_failover_switch,
    plan_failover_teardown,
    validate_failover_label,
    validate_failover_port,
)

FIXTURE_EMPTY = """\
*nat
:PREROUTING ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
COMMIT
"""

# proxy.sh's own DESTINATION rules coexisting alongside a failover-front label —
# must never be confused with each other (no shared comment tag).
FIXTURE_PROXY_SH_ONLY = """\
*nat
:PREROUTING ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
-A PREROUTING -p tcp -m tcp --dport 443 -j DNAT --to-destination 203.0.113.10:50443
-A POSTROUTING -d 203.0.113.10/32 -j SNAT --to-source 198.51.100.1
COMMIT
"""

FIXTURE_ONE_LABEL = """\
*nat
:PREROUTING ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
-A PREROUTING -p tcp -m tcp --dport 443 -j DNAT --to-destination 203.0.113.10:50443
-A POSTROUTING -d 203.0.113.10/32 -j SNAT --to-source 198.51.100.1
-A PREROUTING -p udp --dport 39001 -m comment --comment "az-failover:pool3" -j DNAT --to-destination 10.10.10.11:39001
-A POSTROUTING -p udp -d 10.10.10.11 --dport 39001 -m comment --comment "az-failover:pool3" -j MASQUERADE
COMMIT
"""

FIXTURE_TWO_LABELS = FIXTURE_ONE_LABEL.replace(
    "COMMIT",
    '-A PREROUTING -p udp --dport 39002 -m comment --comment "az-failover:pool9" '
    "-j DNAT --to-destination 10.10.10.21:39002\n"
    '-A POSTROUTING -p udp -d 10.10.10.21 --dport 39002 -m comment --comment "az-failover:pool9" '
    "-j MASQUERADE\nCOMMIT",
)


def test_validate_failover_label():
    assert validate_failover_label(" Pool-3_x ") == "pool-3_x"
    with pytest.raises(ValueError):
        validate_failover_label("bad label!")
    with pytest.raises(ValueError):
        validate_failover_label("")


def test_validate_failover_port():
    assert validate_failover_port(39001) == 39001
    with pytest.raises(ValueError):
        validate_failover_port(0)
    with pytest.raises(ValueError):
        validate_failover_port(70000)


def test_detect_failover_destination_absent():
    assert detect_failover_destination(FIXTURE_EMPTY, "pool3") is None
    assert detect_failover_destination(FIXTURE_PROXY_SH_ONLY, "pool3") is None


def test_detect_failover_destination_present():
    assert detect_failover_destination(FIXTURE_ONE_LABEL, "pool3") == "10.10.10.11"
    # Different label on the same host must not match another label's rule.
    assert detect_failover_destination(FIXTURE_ONE_LABEL, "pool9") is None


def test_detect_failover_destination_multiple_labels_isolated():
    assert detect_failover_destination(FIXTURE_TWO_LABELS, "pool3") == "10.10.10.11"
    assert detect_failover_destination(FIXTURE_TWO_LABELS, "pool9") == "10.10.10.21"


def test_failover_status_from_rules():
    status = failover_status_from_rules(FIXTURE_ONE_LABEL, "pool3", 39001)
    assert status == {
        "label": "pool3",
        "port": 39001,
        "destination_ip": "10.10.10.11",
        "installed": True,
    }
    status_missing = failover_status_from_rules(FIXTURE_EMPTY, "pool3", 39001)
    assert status_missing["installed"] is False
    assert status_missing["destination_ip"] is None


def test_plan_failover_switch_fresh_install():
    plan = plan_failover_switch(FIXTURE_EMPTY, "pool3", 39001, "10.10.10.11")
    # No existing rule → only two -A (insert) commands, no -D.
    assert len(plan) == 2
    assert all(argv[4] == "-A" for argv in plan)
    dnat, masq = plan
    assert "DNAT" in dnat
    assert "10.10.10.11:39001" in dnat
    assert "MASQUERADE" in masq
    assert "10.10.10.11" in masq


def test_plan_failover_switch_replaces_existing():
    plan = plan_failover_switch(FIXTURE_ONE_LABEL, "pool3", 39001, "10.10.10.22")
    # Existing rule → -D old pair then -A new pair (4 commands total).
    assert len(plan) == 4
    actions = [argv[4] for argv in plan]
    assert actions == ["-D", "-D", "-A", "-A"]
    assert "10.10.10.11:39001" in plan[0]
    assert "10.10.10.22:39001" in plan[2]


def test_plan_failover_switch_noop_when_same_ip():
    plan = plan_failover_switch(FIXTURE_ONE_LABEL, "pool3", 39001, "10.10.10.11")
    assert plan == []


def test_plan_failover_switch_does_not_touch_other_label():
    plan = plan_failover_switch(FIXTURE_TWO_LABELS, "pool3", 39001, "10.10.10.99")
    for argv in plan:
        assert "pool9" not in " ".join(argv)


def test_plan_failover_teardown_removes_only_that_label():
    plan = plan_failover_teardown(FIXTURE_TWO_LABELS, "pool3", 39001)
    assert len(plan) == 2
    assert all(argv[4] == "-D" for argv in plan)
    joined = " ".join(" ".join(a) for a in plan)
    assert "pool3" in joined
    assert "pool9" not in joined
    # proxy.sh's own rules must never appear in a failover plan.
    assert "50443" not in joined


def test_plan_failover_teardown_noop_when_absent():
    assert plan_failover_teardown(FIXTURE_EMPTY, "pool3", 39001) == []
