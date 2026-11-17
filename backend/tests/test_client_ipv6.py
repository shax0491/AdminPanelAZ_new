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

    def __init__(self, enabled=True, awg3=(), refresh_exc=None):
        self.files, self.ops, self.enabled = {}, [], enabled
        self.awg3, self.refresh_exc = list(awg3), refresh_exc

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

    def awg3_list_clients(self):
        return [{"name": f"{b}{sfx}"} for b in self.awg3 for sfx in ("_az", "_vpn")]

    def awg3_refresh_ipv6(self, client):
        self.ops.append(f"awg3:{client}")
        if self.refresh_exc:
            raise self.refresh_exc
        return ["ok"] if client in self.awg3 else []


def _put(monkeypatch, adapter, client, profile, regenerate=True):
    from types import SimpleNamespace

    from app.routers import client_ipv6

    monkeypatch.setattr(client_ipv6, "get_active_adapter", lambda db: adapter)
    monkeypatch.setattr(client_ipv6, "require_ha_primary_for_config_ops", lambda db: None)
    monkeypatch.setattr(client_ipv6, "log_action", lambda *a, **k: None)
    return client_ipv6.set_client_profile(client, client_ipv6.ProfileBody(profile=profile, regenerate=regenerate), None, SimpleNamespace(id=1, username="a"))


def test_changing_client_stack_updates_dual_map_and_regenerates_only_that_flow(monkeypatch):
    node = _Cfg(True)
    node.files["ipv6-profile.txt"] = "old_ipv4 ipv4\n"
    _put(monkeypatch, node, "alice", "dual")
    assert node.files["ipv6-profile.txt"] == "old_ipv4 ipv4\nalice dual\n"  # чужой IPv4-клиент не тронут
    assert node.ops == ["write_profile", "recreate"]  # alice без AWG3: legacy-перегенерация, пересоздавать AWG3 нечего
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


def test_awg3_client_switch_refreshes_for_both_regenerate_values_without_mass_regen(monkeypatch):
    for regen in (True, False):
        node = _Cfg(True, awg3=["onex", "cudy"])
        node.files["ipv6-profile.txt"] = "onex dual\n"
        for prof in ("ipv4", "dual"):
            node.ops.clear()
            res = _put(monkeypatch, node, "onex", prof, regenerate=regen)
            assert node.ops == ["write_profile", "awg3:onex"]  # ни recreate (client.sh 4), ни чужих клиентов
            assert any("AWG 3.0" in n for n in res["notes"])
        assert node.files["ipv6-profile.txt"] == "onex dual\n"


def test_legacy_regen_restores_awg3_of_every_client(monkeypatch):
    node = _Cfg(True, awg3=["cudy", "wrk"])
    _put(monkeypatch, node, "plain", "ipv4", regenerate=True)
    assert node.ops == ["write_profile", "recreate", "awg3:cudy", "awg3:wrk"]  # client.sh 4 стёр AWG3-файлы: пересозданы
    node.ops.clear()
    _put(monkeypatch, node, "plain", "dual", regenerate=False)
    assert node.ops == ["write_profile"]  # без regenerate легаси-клиент ничего не перегенерирует


def test_awg3_refresh_failure_is_not_masked_by_200(monkeypatch):
    node = _Cfg(True, awg3=["onex"], refresh_exc=RuntimeError("awg set упал"))
    for regen in (True, False):
        with pytest.raises(HTTPException) as e:
            _put(monkeypatch, node, "onex", "ipv4", regenerate=regen)
        assert e.value.status_code == 502 and "awg set упал" in e.value.detail


def test_node_without_awg3_support_still_switches_profile(monkeypatch):
    node = _Cfg(True)
    node.awg3_list_clients = lambda: (_ for _ in ()).throw(NotImplementedError())
    _put(monkeypatch, node, "x", "ipv4", regenerate=False)
    assert node.files["ipv6-profile.txt"] == "x ipv4\n"
