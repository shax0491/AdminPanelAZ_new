"""Узел «только MTProxy» (node_kind=mtproxy): proxy_agent + MTProxyL без AntiZapret."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import mtproxy_monitor, node_manager


def _node(node_id: int, kind: str, *, online: bool = True, name: str | None = None):
    from app.models import NodeStatus

    return SimpleNamespace(
        id=node_id,
        name=name or f"{kind}-{node_id}",
        node_kind=kind,
        is_local=False,
        status=NodeStatus.online if online else NodeStatus.offline,
    )


def test_kind_helpers():
    assert node_manager.is_mtproxy_node(_node(1, "mtproxy"))
    assert node_manager.uses_proxy_agent(_node(1, "mtproxy"))
    assert node_manager.uses_proxy_agent(_node(2, "proxy"))
    assert not node_manager.uses_proxy_agent(_node(3, "vpn"))
    assert not node_manager.is_vpn_node(_node(1, "mtproxy"))


def test_mtproxy_node_is_not_a_vpn_adapter():
    with pytest.raises(HTTPException) as exc:
        node_manager.get_adapter_for_node(_node(1, "mtproxy", name="FI1"))
    assert "только с MTProxy" in str(exc.value.detail)


def test_health_of_mtproxy_node_goes_through_proxy_agent(monkeypatch):
    class Adapter:
        def health(self):
            return {"ok": True, "agent_version": "1.2.0", "mtproxyl": True}

    seen = []
    monkeypatch.setattr(node_manager, "get_proxy_adapter", lambda node, api_key_override=None: seen.append(node.id) or Adapter())
    result = node_manager.check_node_health(_node(7, "mtproxy"))
    assert result["status"] == "online" and result["mtproxyl"] is True
    assert seen == [7]


def test_mtproxy_status_of_mtproxy_node_uses_proxy_adapter(monkeypatch):
    mtproxy_monitor.reset_cache()

    class ProxyAdapter:
        def get_mtproxy_status(self):
            return {"installed": True, "running": True, "domain": "media.example"}

    def no_vpn_adapter(node):
        raise AssertionError("узлу «только MTProxy» не нужен node_agent")

    monkeypatch.setattr(node_manager, "get_proxy_adapter", lambda node: ProxyAdapter())
    monkeypatch.setattr(node_manager, "get_adapter_for_node", no_vpn_adapter)
    status = mtproxy_monitor.node_mtproxy_status(_node(11, "mtproxy"), max_age=0)
    assert status["domain"] == "media.example"
    mtproxy_monitor.reset_cache()


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def order_by(self, *_a):
        return self

    def all(self):
        return self._rows


def test_tab_lists_mtproxy_nodes_and_hides_vpn_without_mtproxyl(monkeypatch):
    vpn_with = _node(1, "vpn", name="DE1")
    vpn_without = _node(2, "vpn", name="DE2")
    proxy = _node(3, "proxy", name="RU_DE2")
    mt = _node(4, "mtproxy", name="FI1")
    mt_offline = _node(5, "mtproxy", name="FR1", online=False)
    db = SimpleNamespace(query=lambda _model: _FakeQuery([vpn_with, vpn_without, proxy, mt, mt_offline]))
    statuses = {
        1: {"installed": True, "running": True},
        2: {"installed": False},
        4: {"installed": True, "running": True, "domain": "media.example"},
    }
    monkeypatch.setattr(mtproxy_monitor, "node_mtproxy_status", lambda node, **kw: statuses.get(node.id))
    monkeypatch.setattr(mtproxy_monitor, "panel_mtproxy_status", lambda db, **kw: None)

    rows = {r["node_name"]: r for r in mtproxy_monitor.mtproxy_all_nodes(db)}
    assert set(rows) == {"DE1", "FI1", "FR1"}  # DE2 без MTProxyL и прокси-узел не показываем
    assert rows["FI1"]["node_kind"] == "mtproxy" and rows["FI1"]["domain"] == "media.example"
    assert rows["FR1"]["installed"] is None and rows["FR1"]["node_online"] is False


def test_proxy_agent_serves_mtproxy(monkeypatch):
    monkeypatch.setenv("PROXY_AGENT_MODE", "dev")
    from fastapi.testclient import TestClient

    from proxy_agent import main as agent

    monkeypatch.setattr(agent, "PROXY_AGENT_API_KEY", "k" * 32)
    monkeypatch.setattr(agent, "PROXY_AGENT_ALLOWED_IPS", [])
    monkeypatch.setattr(mtproxy_monitor, "collect_mtproxy_status", lambda: {"installed": True, "running": True})
    monkeypatch.setattr(mtproxy_monitor, "run_mtproxy_action", lambda payload: {"ok": True, "action": payload["action"]})
    monkeypatch.setattr(mtproxy_monitor, "_mtproxyl_cmd", lambda: ["mtproxyl"])

    client = TestClient(agent.app)
    headers = {"X-Node-Key": "k" * 32}
    assert client.get("/mtproxy/status", headers=headers).json() == {"installed": True, "running": True}
    assert client.post("/mtproxy/action", json={"action": "restart"}, headers=headers).json()["ok"] is True
    assert client.get("/health", headers=headers).json()["mtproxyl"] is True
    assert client.get("/mtproxy/status", headers={"X-Node-Key": "wrong"}).status_code == 401
