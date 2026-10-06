"""AmneziaWG 3 failover: endpoint rewrite to the front, restore, and state sync from the primary."""

from types import SimpleNamespace

import pytest

from app.models import VpnType
from app.services import failover_front as ff
from app.services.node_sync import vpn_state_sync as vss


class FakeAdapter:
    def __init__(self, name: str):
        self.name = name
        self.hosts: list[str] = []
        self.archive_in: bytes | None = None
        self.restore_result = {"success": True, "errors": []}

    def awg3_set_server_host(self, host: str) -> str:
        self.hosts.append(host)
        return host

    def export_awg3_backup(self) -> bytes:
        return f"archive-from-{self.name}".encode()

    def restore_awg3_backup(self, data: bytes) -> dict:
        self.archive_in = data
        return self.restore_result


def _pool(vpn_type=VpnType.amneziawg3, front_host="front.example"):
    front = SimpleNamespace(host=front_host) if front_host else None
    return SimpleNamespace(id=7, vpn_type=vpn_type, front_node_id=1 if front else None, front_node=front)


def _member(name: str, host: str, pool):
    return SimpleNamespace(node=SimpleNamespace(name=name, host=host), pool=pool)


@pytest.fixture()
def adapters(monkeypatch):
    registry: dict[str, FakeAdapter] = {}

    def fake_get(node):
        return registry.setdefault(node.name, FakeAdapter(node.name))

    monkeypatch.setattr(ff, "get_adapter_for_node", fake_get)
    return registry


def test_rewrite_points_member_clients_at_front_host(adapters):
    pool = _pool()
    member = _member("replica", "10.0.0.2", pool)
    assert ff.rewrite_member_client_endpoints(pool, member) == 1
    assert adapters["replica"].hosts == ["front.example"]


def test_rewrite_without_front_is_a_noop(adapters):
    pool = _pool(front_host=None)
    member = _member("replica", "10.0.0.2", pool)
    assert ff.rewrite_member_client_endpoints(pool, member) == 0
    assert adapters.get("replica") is None or adapters["replica"].hosts == []


def test_restore_puts_member_back_on_its_own_host(adapters):
    pool = _pool()
    member = _member("replica", "10.0.0.2", pool)
    assert ff.restore_member_client_endpoint(member) == 1
    assert adapters["replica"].hosts == ["10.0.0.2"]


def test_sync_copies_primary_layer_and_restarts_replica(adapters):
    primary, replica = adapters.setdefault("p", FakeAdapter("p")), FakeAdapter("r")
    result = vss.sync_amneziawg3_state_from_primary(primary, replica)
    assert replica.archive_in == b"archive-from-p"
    assert result["success"] is True


def test_sync_fails_loudly_when_replica_restart_fails():
    primary = FakeAdapter("p")
    replica = FakeAdapter("r")
    replica.restore_result = {"success": False, "errors": [{"error": "awg3@awg1 failed"}]}
    with pytest.raises(RuntimeError):
        vss.sync_amneziawg3_state_from_primary(primary, replica)
