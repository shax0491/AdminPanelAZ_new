"""Telegram bot /awg3: online count, interfaces and top traffic like /awg2, per client not per profile record."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import tg_mini_status
from app.services.telegram_bot_handlers import awg3_status as awg3_handler


def _adapter(health, monitoring):
    adapter = MagicMock()
    adapter.awg3_health.return_value = health
    adapter.awg3_monitoring.return_value = monitoring
    return adapter


def _payload(adapter, node=None):
    node = node or SimpleNamespace(id=3, name="nl1", host="185.193.51.13")
    with (
        patch.object(tg_mini_status, "get_active_node", return_value=node),
        patch.object(tg_mini_status, "get_active_adapter", return_value=adapter),
    ):
        return tg_mini_status.build_awg3_status_payload(MagicMock())


def _client(name, *, online, rx, tx, mode="split"):
    return {"iface": "awg1", "name": name, "pubkey": name, "online": online, "rx": rx, "tx": tx, "mode": mode}


HEALTHY = {"tools_present": True, "userspace_present": True}


def test_payload_counts_online_and_sums_both_profiles_of_a_client():
    monitoring = {
        "ifaces": {"awg1": {"name": "awg1", "up": True, "peers": []}},
        "clients": [
            _client("alice_az", online=True, rx=1000, tx=2000, mode="split"),
            _client("alice_vpn", online=False, rx=500, tx=100, mode="full"),
            _client("bob_az", online=True, rx=9000, tx=1000, mode="split"),
            _client("carol_az", online=False, rx=10, tx=10, mode="split"),
        ],
    }
    payload = _payload(_adapter(HEALTHY, monitoring))

    assert payload["installed"] is True and payload["health_error"] is None
    assert payload["peer_count"] == 4 and payload["online_count"] == 2
    top = payload["top_traffic"]
    assert [row["name"] for row in top] == ["bob", "alice", "carol"]
    assert top[1] == {"name": "alice", "rx": 1500, "tx": 2100}
    assert "antizapret" in payload["ifaces_summary"] and "vpn" in payload["ifaces_summary"]
    assert payload["node_name"] == "nl1"


def test_payload_when_tools_are_missing_skips_monitoring():
    adapter = _adapter({"tools_present": False, "userspace_present": True}, {})
    adapter.awg3_monitoring.side_effect = AssertionError("monitoring must not be read when not installed")
    payload = _payload(adapter)
    assert payload["installed"] is False
    assert payload["missing_components"] == ["awg"]
    assert payload["online_count"] == 0 and payload["top_traffic"] == []


def test_payload_keeps_health_error_and_does_not_crash():
    adapter = MagicMock()
    adapter.awg3_health.side_effect = RuntimeError("node unreachable")
    payload = _payload(adapter)
    assert payload["installed"] is False and payload["health_error"] == "node unreachable"


def test_text_shows_online_ratio_interfaces_and_readable_traffic():
    text = awg3_handler._format_awg3_text(
        {
            "node_name": "nl1",
            "node_host": "185.193.51.13",
            "installed": True,
            "online_count": 2,
            "peer_count": 4,
            "ifaces_summary": "antizapret:3, vpn:1",
            "top_traffic": [{"name": "bob", "rx": 5 * 1024 * 1024, "tx": 2048}],
        }
    )
    assert "Онлайн: 2 из 4" in text
    assert "antizapret:3, vpn:1" in text
    assert "<code>bob</code>" in text
    assert "↓5.0 MB" in text and "↑2.0 KB" in text


def test_text_for_not_installed_node_is_short_and_names_the_gap():
    text = awg3_handler._format_awg3_text(
        {"node_name": "n", "node_host": "h", "installed": False, "missing_components": ["awg"], "health_error": None}
    )
    assert "Установлен: нет" in text and "awg" in text and "Онлайн" not in text


def test_text_without_traffic_says_so():
    text = awg3_handler._format_awg3_text(
        {"node_name": "n", "node_host": "h", "installed": True, "online_count": 0, "peer_count": 0, "top_traffic": []}
    )
    assert "Трафика по клиентам пока нет" in text


def _ctx():
    return SimpleNamespace(
        user=SimpleNamespace(id=1, role="admin"),
        bot_token="token",
        chat_id=42,
        db=MagicMock(),
        telegram_user_id="99",
        mini_app_url="",
    )


def test_handler_reports_disabled_toggle():
    feature = MagicMock()
    feature.is_enabled.return_value = False
    send = AsyncMock()
    with (
        patch.object(awg3_handler, "get_feature_service", return_value=feature),
        patch.object(awg3_handler, "is_admin", return_value=True),
        patch("app.services.telegram_api.send_message", send),
    ):
        asyncio.run(awg3_handler.handle_awg3_status(_ctx()))
    send.assert_awaited_once()
    assert "выключен" in send.await_args.args[2]
    feature.is_enabled.assert_called_with("awg3")


def test_handler_sends_the_new_status_text():
    feature = MagicMock()
    feature.is_enabled.return_value = True
    sent = AsyncMock()
    payload = {
        "node_name": "nl1", "node_host": "h", "installed": True, "online_count": 1,
        "peer_count": 2, "ifaces_summary": "antizapret:2", "top_traffic": [],
    }
    with (
        patch.object(awg3_handler, "get_feature_service", return_value=feature),
        patch.object(awg3_handler, "is_admin", return_value=True),
        patch.object(awg3_handler, "build_awg3_status_payload", return_value=payload),
        patch.object(awg3_handler, "send_or_edit", sent),
    ):
        asyncio.run(awg3_handler.handle_awg3_status(_ctx()))
    sent.assert_awaited_once()
    assert "Онлайн: 1 из 2" in sent.await_args.args[1]


def test_mini_awg3_status_404_when_toggle_off():
    from fastapi import HTTPException

    from app.routers import tg_mini as tg_mini_router

    feature = MagicMock()
    feature.is_enabled.return_value = False
    with patch.object(tg_mini_router, "get_feature_service", return_value=feature):
        try:
            tg_mini_router.mini_awg3_status(db=MagicMock(), _=SimpleNamespace())
            raise AssertionError("expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 404 and "AmneziaWG 3" in str(exc.detail)
    feature.is_enabled.assert_called_with("awg3")


def test_mini_awg3_status_returns_the_payload_when_enabled():
    from app.routers import tg_mini as tg_mini_router
    from app.routers.tg_mini import status as status_router

    feature = MagicMock()
    feature.is_enabled.return_value = True
    payload = {"installed": True, "online_count": 2}
    with (
        patch.object(tg_mini_router, "get_feature_service", return_value=feature),
        patch.object(status_router, "build_awg3_status_payload", return_value=payload),
    ):
        assert tg_mini_router.mini_awg3_status(db=MagicMock(), _=SimpleNamespace()) == payload


def _dashboard_env(monkeypatch, *, awg3_enabled, peers):
    from datetime import datetime, timedelta

    from app.routers.tg_mini import dashboard
    from app.schemas import WireGuardPeer

    adapter = MagicMock()
    adapter.parse_openvpn_status.return_value = []
    adapter.parse_wireguard_status.return_value = []
    adapter.get_server_ip.return_value = "1.2.3.4"
    fetch = MagicMock(return_value=peers)
    monkeypatch.setattr(dashboard, "get_active_adapter", lambda _db: adapter)
    monkeypatch.setattr(dashboard, "get_active_node", lambda _db: SimpleNamespace(id=1))
    monkeypatch.setattr(dashboard, "fetch_awg2_peers_for_adapter", lambda _a: [])
    monkeypatch.setattr(dashboard, "fetch_awg3_peers_for_adapter", fetch)
    monkeypatch.setattr(dashboard, "is_awg3_enabled", lambda _db: awg3_enabled)
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 3
    now = datetime.utcnow()
    peers[:] = [
        WireGuardPeer(
            interface="antizapret", public_key=f"pk{i}", client_name=name, transfer_rx=10 * i, transfer_tx=20 * i,
            latest_handshake=(now - timedelta(seconds=5 if online else 900)).isoformat(),
        )
        for i, (name, online) in enumerate(peers, start=1)
    ]
    return dashboard, db, fetch


def test_mini_dashboard_counts_awg3_online_and_lists_peers(monkeypatch):
    dashboard, db, _ = _dashboard_env(monkeypatch, awg3_enabled=True, peers=[("alice", True), ("bob", False)])
    result = dashboard.mini_dashboard(current_user=SimpleNamespace(), db=db)
    assert result["connected_amneziawg3"] == 1 and result["total_amneziawg3_peers"] == 2
    assert [p["client_name"] for p in result["amneziawg3_peers"]] == ["alice"]
    assert result["amneziawg3_peers"][0]["transfer_rx"] == 10


def test_mini_dashboard_does_not_poll_awg3_when_toggle_off(monkeypatch):
    dashboard, db, fetch = _dashboard_env(monkeypatch, awg3_enabled=False, peers=[("alice", True)])
    result = dashboard.mini_dashboard(current_user=SimpleNamespace(), db=db)
    fetch.assert_not_called()
    assert result["connected_amneziawg3"] == 0 and result["amneziawg3_peers"] == []
