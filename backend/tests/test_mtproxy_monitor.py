"""Мониторинг MTProxy: разбор вывода MTProxyL и метрики правил оповещений по узлам."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import mtproxy_monitor


def test_parse_status_json_skips_log_lines():
    out = "  [i] something\n{\"status\":\"running\",\"port\":443,\"domain\":\"animal.example.ru\",\"version\":\"1.6.33\"}\n"
    data = mtproxy_monitor.parse_status_json(out)
    assert data["status"] == "running"
    assert data["domain"] == "animal.example.ru"
    assert mtproxy_monitor.parse_status_json("no json here") is None


def test_parse_availability_history_keeps_last_checks():
    lines = "\n".join(
        f'{{"checked_at":"2026-10-08T0{i}:00:00Z","target":"t","percentage":{p},"total_probes":20,"success_probes":{p // 5},"error":""}}'
        for i, p in enumerate([90, 100, 40])
    )
    checks = mtproxy_monitor.parse_availability_history(lines + "\nbroken line\n", limit=2)
    assert [c["percentage"] for c in checks] == [100.0, 40.0]
    assert checks[-1]["success"] == 8
    assert mtproxy_monitor.parse_availability_history("") == []


class _Query:
    def __init__(self, nodes):
        self._nodes = nodes

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self._nodes


@pytest.fixture
def fake_nodes(monkeypatch):
    statuses = {
        1: {"installed": True, "running": True, "status": "running", "availability": {"percentage": 100.0}},
        2: {"installed": True, "running": False, "status": "stopped", "availability": {"percentage": 35.0}},
        3: {"installed": False},
        4: None,  # агент недоступен или старый
        5: {"installed": True, "running": False, "status": "unknown", "availability": None},
    }
    nodes = [SimpleNamespace(id=i, name=f"n{i}") for i in statuses]
    monkeypatch.setattr(mtproxy_monitor, "_monitored_nodes", lambda db, node_id: [n for n in nodes if node_id in (None, n.id)])
    monkeypatch.setattr(mtproxy_monitor, "node_mtproxy_status", lambda node, **kw: statuses[node.id])
    return statuses


def test_down_counts_only_stopped_proxies(fake_nodes):
    assert mtproxy_monitor.mtproxy_down_value(None, None) == 1.0
    assert mtproxy_monitor.mtproxy_down_value(None, 1) == 0.0
    assert mtproxy_monitor.mtproxy_down_value(None, 2) == 1.0
    # без MTProxyL / узел не ответил - метрика пустая, правило не срабатывает
    assert mtproxy_monitor.mtproxy_down_value(None, 3) is None
    assert mtproxy_monitor.mtproxy_down_value(None, 4) is None


def test_availability_is_worst_node(fake_nodes):
    assert mtproxy_monitor.mtproxy_availability_value(None, None) == 35.0
    assert mtproxy_monitor.mtproxy_availability_value(None, 1) == 100.0
    assert mtproxy_monitor.mtproxy_availability_value(None, 5) is None


def test_overview_lists_only_installed(fake_nodes):
    rows = mtproxy_monitor.mtproxy_overview(None)
    assert [r["node_id"] for r in rows] == [1, 2, 5]


def test_node_status_cached(monkeypatch):
    mtproxy_monitor.reset_cache()
    calls = []

    class Adapter:
        def get_mtproxy_status(self):
            calls.append(1)
            return {"installed": True, "running": True}

    import app.services.node_manager as node_manager

    monkeypatch.setattr(node_manager, "get_adapter_for_node", lambda node: Adapter())
    node = SimpleNamespace(id=42)
    assert mtproxy_monitor.node_mtproxy_status(node)["running"] is True
    assert mtproxy_monitor.node_mtproxy_status(node)["running"] is True
    assert len(calls) == 1
    mtproxy_monitor.reset_cache()


def test_alert_metrics_registered():
    from app.models import AlertRuleMetric
    from app.services.alert_rules import ALERT_METRIC_LABELS

    assert AlertRuleMetric.mtproxy_down.value in ALERT_METRIC_LABELS
    assert AlertRuleMetric.mtproxy_availability_pct.value in ALERT_METRIC_LABELS


def test_bot_text_lists_nodes():
    from app.services.telegram_bot_handlers.mtproxy_status import format_mtproxy_text

    text = format_mtproxy_text([
        {"node_name": "NL2", "running": True, "status": "running", "domain": "animal.example.ru", "port": 443,
         "connections": 3, "availability": {"percentage": 100.0, "success": 20, "total": 20, "checked_at": "2026-10-08T08:30:00Z"}},
        {"node_name": "DE1", "running": False, "status": "stopped", "availability": None},
    ])
    assert "NL2" in text and "✅ работает" in text and "<b>100%</b> (20/20 зондов)" in text
    assert "❌ stopped" in text and "нет данных" in text
    assert "не найден" in format_mtproxy_text([])
