import pytest
from fastapi import HTTPException

from app.routers.client_ipv6 import render_profiles, require_server_dual_stack


def test_render_adds_replaces_and_removes_only_own_line():
    base = "# комментарий\nbob dual\nalice ipv4\n"
    assert render_profiles(base, "alice", "dual") == "# комментарий\nbob dual\nalice dual\n"
    assert render_profiles(base, "bob", "default") == "# комментарий\nalice ipv4\n"
    assert render_profiles("", "x", "ipv4") == "x ipv4\n"
    assert render_profiles("x ipv4\n", "x", "default") == ""


class Adapter:
    def __init__(self, status=None, exc=None):
        self.status, self.exc = status, exc

    def get_ipv6_status(self):
        if self.exc:
            raise self.exc
        return self.status


def test_dual_profile_refused_when_server_dual_stack_off():
    for off in ({"enabled": False}, {}, None):
        with pytest.raises(HTTPException) as e:
            require_server_dual_stack(Adapter(off))
        assert e.value.status_code == 409


def test_dual_profile_allowed_when_server_dual_stack_on():
    require_server_dual_stack(Adapter({"enabled": True, "effective_mode": "c"}))


def test_unknown_server_state_does_not_write_profile():
    with pytest.raises(HTTPException) as e:
        require_server_dual_stack(Adapter(exc=RuntimeError("агент недоступен")))
    assert e.value.status_code == 502
    with pytest.raises(HTTPException) as e2:  # не VPN-узел: тоже fail-closed, а не молчаливый пропуск
        require_server_dual_stack(Adapter(exc=NotImplementedError()))
    assert e2.value.status_code == 409


def test_enabled_but_not_applied_by_node_is_refused():
    for eff in ("a", "", None):
        with pytest.raises(HTTPException) as e:
            require_server_dual_stack(Adapter({"enabled": True, "effective_mode": eff}))
        assert e.value.status_code == 409


class _Cfg:
    """Узел с ipv6-profile.txt; фиксирует, какие операции над клиентскими конфигами вызваны."""

    def __init__(self, enabled=True):
        self.files, self.ops, self.enabled = {}, [], enabled

    def get_ipv6_status(self):
        return {"enabled": self.enabled, "effective_mode": "c" if self.enabled else "a"}

    def read_config_file(self, name):
        if name not in self.files:
            raise FileNotFoundError(name)
        return self.files[name]

    def write_config_file(self, name, text):
        self.files[name] = text
        self.ops.append("write_profile")

    def recreate_profiles(self):
        self.ops.append("recreate")

    def awg3_refresh_ipv6(self, client):
        self.ops.append(f"awg3:{client}")
        return ["ok"]


def _put(monkeypatch, adapter, client, profile):
    from types import SimpleNamespace

    from app.routers import client_ipv6

    monkeypatch.setattr(client_ipv6, "get_active_adapter", lambda db: adapter)
    monkeypatch.setattr(client_ipv6, "require_ha_primary_for_config_ops", lambda db: None)
    monkeypatch.setattr(client_ipv6, "log_action", lambda *a, **k: None)
    return client_ipv6.set_client_profile(client, client_ipv6.ProfileBody(profile=profile), None, SimpleNamespace(id=1, username="a"))


def test_changing_client_stack_updates_dual_map_and_regenerates_only_that_flow(monkeypatch):
    node = _Cfg(True)
    node.files["ipv6-profile.txt"] = "old_ipv4 ipv4\n"
    _put(monkeypatch, node, "alice", "dual")
    assert node.files["ipv6-profile.txt"] == "old_ipv4 ipv4\nalice dual\n"  # чужой IPv4-клиент не тронут
    assert node.ops == ["write_profile", "recreate", "awg3:alice"]
    _put(monkeypatch, node, "alice", "ipv4")
    assert node.files["ipv6-profile.txt"] == "old_ipv4 ipv4\nalice ipv4\n"


def test_dual_stack_off_refuses_dual_via_api_but_keeps_ipv4_clients_working(monkeypatch):
    node = _Cfg(False)
    with pytest.raises(HTTPException) as err:
        _put(monkeypatch, node, "bob", "dual")
    assert err.value.status_code == 409 and node.ops == []  # ни профиль, ни конфиги не тронуты
    _put(monkeypatch, node, "bob", "ipv4")  # IPv4-профиль при OFF допустим
    assert node.files["ipv6-profile.txt"] == "bob ipv4\n"


def test_profile_file_editor_cannot_bypass_409():
    from app.routers.client_ipv6 import guard_profile_file_write

    node = _Cfg(False)
    node.files["ipv6-profile.txt"] = "old dual\nkeep ipv4\n"
    guard_profile_file_write(node, "old dual\nkeep ipv4\nnew ipv4\n")  # IPv4-строки и уже существующие dual допустимы
    with pytest.raises(HTTPException) as e:
        guard_profile_file_write(node, "old dual\nkeep dual\n")  # ipv4 -> dual при OFF
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        guard_profile_file_write(node, "old dual\nfresh dual\n")  # новый dual-клиент при OFF
    guard_profile_file_write(_Cfg(True), "old dual\nfresh dual\n")  # при ON можно
    assert node.ops == []  # проверка ничего не пишет
