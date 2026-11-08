from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

KEY = "d" * 40


class FakeRuntime:
    def __init__(self):
        self.transport = None

    def audit(self):
        return {
            "has_ipv4": True,
            "has_ipv6": True,
            "ipv6_address": "2001:db8::1",
            "ipv6_subnet_prefix": "64",
            "ipv6_rotation_supported": True,
        }

    def probe(self, family, target):
        return {"reachable": True, "family": family, "target": target, "latency_ms": 12.5}

    def apply_transport(self, transport_mode):
        self.transport = transport_mode
        return {"transport_mode": transport_mode, "changed": ["xray-az"]}

    def rotate_ipv6(self):
        return {"ipv6_address": "2001:db8::2", "old_ipv6_address": "2001:db8::1"}


@pytest.fixture
def node_main(monkeypatch):
    monkeypatch.setenv("NODE_AGENT_MODE", "dev")
    monkeypatch.setenv("NODE_AGENT_API_KEY", KEY)
    monkeypatch.setenv("NODE_AGENT_ALLOWED_IPS", "")
    import node_agent.main as module

    runtime = FakeRuntime()
    monkeypatch.setattr(module, "dual_stack_runtime", runtime, raising=False)
    return module, runtime


@pytest.fixture
def proxy_main(monkeypatch):
    monkeypatch.setenv("PROXY_AGENT_MODE", "dev")
    monkeypatch.setenv("PROXY_AGENT_API_KEY", KEY)
    monkeypatch.setenv("PROXY_AGENT_ALLOWED_IPS", "")
    import proxy_agent.main as module

    runtime = FakeRuntime()
    monkeypatch.setattr(module, "dual_stack_runtime", runtime, raising=False)
    return module, runtime


def test_node_agent_excludes_unauthenticated_dual_stack_audit(node_main):
    module, _runtime = node_main
    response = TestClient(module.app).get("/dual-stack/capabilities")
    assert response.status_code in (401, 422)


def test_node_agent_audit_probe_transport_and_rotation(node_main):
    module, runtime = node_main
    client = TestClient(module.app)
    headers = {"X-Node-Key": KEY}

    audit = client.get("/dual-stack/capabilities", headers=headers)
    probe = client.get(
        "/dual-stack/probe",
        params={"family": 6, "target": "2001:db8::2"},
        headers=headers,
    )
    transport = client.post("/dual-stack/transport", json={"mode": "force_ipv6"}, headers=headers)
    rotation = client.post("/dual-stack/rotate-ipv6", headers=headers)

    assert audit.status_code == 200
    assert audit.json()["ipv6_rotation_supported"] is True
    assert probe.json()["latency_ms"] == 12.5
    assert transport.status_code == 200
    assert runtime.transport == "force_ipv6"
    assert rotation.status_code == 200
    assert rotation.json()["ipv6_address"] == "2001:db8::2"


def test_proxy_agent_has_authenticated_audit_and_probe(proxy_main):
    module, _runtime = proxy_main
    client = TestClient(module.app)
    headers = {"X-Node-Key": KEY}

    audit = client.get("/dual-stack/capabilities", headers=headers)
    probe = client.get(
        "/dual-stack/probe",
        params={"family": 4, "target": "203.0.113.10"},
        headers=headers,
    )

    assert audit.status_code == 200
    assert audit.json()["has_ipv4"] is True
    assert probe.status_code == 200
    assert probe.json()["target"] == "203.0.113.10"


def test_node_agent_configures_reverse_exit_without_leaking_key(node_main, monkeypatch):
    module, _runtime = node_main

    class ReverseRuntime:
        def configure_exit(self, **kwargs):
            assert kwargs["private_key"] == "private"
            return {"interface": "azr5", "active": True}

        def status(self, link_id):
            return {"interface": f"azr{link_id}", "connected": True}

    monkeypatch.setattr(module, "reverse_tunnel_runtime", ReverseRuntime(), raising=False)
    client = TestClient(module.app)
    response = client.post(
        "/reverse-tunnel/exit",
        headers={"X-Node-Key": KEY},
        json={
            "link_id": 5,
            "private_key": "private",
            "address4": "10.77.5.2/32",
            "address6": "fd77::5:2/128",
            "peer_public_key": "public",
            "relay_host": "2001:db8::10",
            "listen_port": 51877,
            "allowed_ips": "10.77.5.1/32, fd77::5:1/128",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"interface": "azr5", "active": True}
    assert "private" not in response.text
    assert client.get("/reverse-tunnel/5/status", headers={"X-Node-Key": KEY}).json()["connected"] is True


def test_proxy_agent_configures_relay_and_watchdog(proxy_main, monkeypatch):
    module, _runtime = proxy_main

    class ReverseRuntime:
        def configure_relay(self, **kwargs):
            return {"interface": "azr5", "active": True, "listen_port": kwargs["listen_port"]}

    class Watchdog:
        def configure(self, link):
            return {"link_id": link["link_id"], "enabled": True, "reverse_active": False}

        def public_status(self, link_id):
            return {"link_id": link_id, "enabled": True, "reverse_active": True}

    monkeypatch.setattr(module, "reverse_tunnel_runtime", ReverseRuntime(), raising=False)
    monkeypatch.setattr(module, "reverse_watchdog", Watchdog(), raising=False)
    client = TestClient(module.app)
    headers = {"X-Node-Key": KEY}
    relay = client.post(
        "/reverse-tunnel/relay",
        headers=headers,
        json={
            "link_id": 5,
            "private_key": "private",
            "address4": "10.77.5.1/32",
            "address6": "fd77::5:1/128",
            "listen_port": 51877,
            "peer_public_key": "public",
            "peer_allowed_ips": "10.77.5.2/32, fd77::5:2/128",
        },
    )
    watchdog = client.put(
        "/reverse-tunnel/watchdog",
        headers=headers,
        json={
            "link_id": 5,
            "enabled": True,
            "direct_ipv4": "203.0.113.10",
            "direct_ipv6": "2001:db8::10",
            "tunnel_ipv4": "10.77.5.2",
        },
    )
    status = client.get("/reverse-tunnel/watchdog/5", headers=headers)

    assert relay.status_code == 200
    assert "private" not in relay.text
    assert watchdog.status_code == 200
    assert watchdog.json()["reverse_active"] is False
    assert status.json()["reverse_active"] is True
