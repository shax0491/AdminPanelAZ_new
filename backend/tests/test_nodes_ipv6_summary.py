from types import SimpleNamespace

from app.routers import nodes as nodes_router


class FakeDb:
    def __init__(self, items):
        self.items = items

    def query(self, _model):
        return SimpleNamespace(all=lambda: self.items)


def test_summary_keeps_short_fields_and_reports_unreachable_nodes(monkeypatch):
    good = SimpleNamespace(id=1, node_kind="vpn")
    bad = SimpleNamespace(id=2, node_kind="vpn")
    proxy = SimpleNamespace(id=3, node_kind="proxy")

    def adapter(node):
        if node.id == 2:
            raise RuntimeError("agent down")
        return SimpleNamespace(get_ipv6_status=lambda: {"enabled": True, "effective_mode": "c", "egress": "warp", "reason": "", "x": 1})

    monkeypatch.setattr(nodes_router, "get_adapter_for_node", adapter)
    out = nodes_router.nodes_ipv6_summary(db=FakeDb([good, bad, proxy]), _=None)
    assert set(out) == {"1", "2"}
    assert out["1"]["egress"] == "warp" and "x" not in out["1"]
    assert out["2"]["error"] == "agent down"


def test_summary_syncs_db_egress_to_node_state_without_touching_nodes(monkeypatch):
    """БД подтягивается к фактическому active_egress; к узлу идёт только чтение статуса."""
    node = SimpleNamespace(id=1, node_kind="vpn", egress_mode="native")
    off = SimpleNamespace(id=2, node_kind="vpn", egress_mode="native")
    commits, calls = [], []

    class Db(FakeDb):
        def add(self, item):
            calls.append(("add", item.id))

        def commit(self):
            commits.append(1)

    statuses = {1: {"active_egress": "proton", "effective_mode": "c"}, 2: {"active_egress": "proton", "effective_mode": "a"}}
    monkeypatch.setattr(nodes_router, "get_adapter_for_node", lambda n: SimpleNamespace(get_ipv6_status=lambda: statuses[n.id]))

    nodes_router.nodes_ipv6_summary(db=Db([node, off]), _=None)

    assert node.egress_mode == "proton" and off.egress_mode == "native"
    assert calls == [("add", 1)] and commits == [1]
