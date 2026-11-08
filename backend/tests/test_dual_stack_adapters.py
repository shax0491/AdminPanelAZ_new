from __future__ import annotations

from unittest.mock import MagicMock

from app.services.node_adapter import RemoteNodeAdapter
from app.services.proxy_node_adapter import ProxyNodeAdapter


def test_remote_adapter_brackets_ipv6_agent_url():
    adapter = RemoteNodeAdapter("2001:db8::1", 9100, "k" * 32)
    assert adapter.base_url == "http://[2001:db8::1]:9100"


def test_proxy_adapter_brackets_ipv6_agent_url():
    adapter = ProxyNodeAdapter("2001:db8::1", 9101, "k" * 32)
    assert adapter.base_url == "http://[2001:db8::1]:9101"


def test_remote_adapter_dual_stack_operations_use_agent_api(monkeypatch):
    adapter = RemoteNodeAdapter("2001:db8::1", 9100, "k" * 32)
    request = MagicMock(side_effect=[{"has_ipv6": True}, {"reachable": True}, {"transport_mode": "auto"}, {"ipv6_address": "2001:db8::2"}])
    monkeypatch.setattr(adapter, "_request", request)

    assert adapter.dual_stack_capabilities()["has_ipv6"] is True
    assert adapter.probe_dual_stack(6, "2001:db8::2")["reachable"] is True
    assert adapter.set_dual_stack_transport("auto")["transport_mode"] == "auto"
    assert adapter.rotate_ipv6()["ipv6_address"] == "2001:db8::2"

    assert request.call_args_list[0].args == ("GET", "/dual-stack/capabilities")
    assert request.call_args_list[1].args == ("GET", "/dual-stack/probe")
    assert request.call_args_list[1].kwargs["params"] == {"family": 6, "target": "2001:db8::2"}
    assert request.call_args_list[2].args == ("POST", "/dual-stack/transport")
    assert request.call_args_list[2].kwargs["json"] == {"mode": "auto"}
    assert request.call_args_list[3].args == ("POST", "/dual-stack/rotate-ipv6")


def test_proxy_adapter_dual_stack_operations_use_agent_api(monkeypatch):
    adapter = ProxyNodeAdapter("2001:db8::1", 9101, "k" * 32)
    request = MagicMock(side_effect=[{"has_ipv6": True}, {"reachable": False}])
    monkeypatch.setattr(adapter, "_request", request)

    assert adapter.dual_stack_capabilities()["has_ipv6"] is True
    assert adapter.probe_dual_stack(4, "203.0.113.1")["reachable"] is False
    assert request.call_args_list[1].kwargs["params"] == {"family": 4, "target": "203.0.113.1"}


def test_remote_adapter_reverse_exit_and_status(monkeypatch):
    adapter = RemoteNodeAdapter("203.0.113.10", 9100, "k" * 32)
    request = MagicMock(side_effect=[{"active": True}, {"connected": True}])
    monkeypatch.setattr(adapter, "_request", request)
    payload = {"link_id": 5, "private_key": "secret"}

    assert adapter.configure_reverse_exit(payload)["active"] is True
    assert adapter.reverse_tunnel_status(5)["connected"] is True
    assert request.call_args_list[0].args == ("POST", "/reverse-tunnel/exit")
    assert request.call_args_list[0].kwargs["json"] is payload
    assert request.call_args_list[1].args == ("GET", "/reverse-tunnel/5/status")


def test_proxy_adapter_reverse_relay_and_watchdog(monkeypatch):
    adapter = ProxyNodeAdapter("198.51.100.10", 9101, "k" * 32)
    request = MagicMock(side_effect=[{"active": True}, {"enabled": True}, {"reverse_active": False}])
    monkeypatch.setattr(adapter, "_request", request)

    assert adapter.configure_reverse_relay({"link_id": 5})["active"] is True
    assert adapter.configure_reverse_watchdog({"link_id": 5})["enabled"] is True
    assert adapter.reverse_watchdog_status(5)["reverse_active"] is False
    assert request.call_args_list[0].args == ("POST", "/reverse-tunnel/relay")
    assert request.call_args_list[1].args == ("PUT", "/reverse-tunnel/watchdog")
    assert request.call_args_list[2].args == ("GET", "/reverse-tunnel/watchdog/5")
