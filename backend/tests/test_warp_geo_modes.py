"""WARP mode switching (ANTIZAPRET_WARP 1-4, VPN_WARP 1-2) written into setup, applied separately by up.sh."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.warp_geo import WarpModeError, read_warp_status, set_warp_modes

SETUP = "WARP_PROVIDER=proton\nANTIZAPRET_WARP=2\nVPN_WARP=2\nWARP_MTU=1280\n"


def _setup(tmp_path, text=SETUP):
    (tmp_path / "setup").write_text(text, encoding="utf-8")
    return tmp_path


def _read(tmp_path):
    return (tmp_path / "setup").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["1", "2", "3", "4"])
def test_antizapret_modes_are_written(tmp_path, mode):
    _setup(tmp_path)
    result = set_warp_modes(tmp_path, antizapret=mode)
    assert result == {"success": True, "antizapret_warp": mode, "vpn_warp": None}
    text = _read(tmp_path)
    assert f"ANTIZAPRET_WARP={mode}\n" in text
    assert "VPN_WARP=2\n" in text and "WARP_MTU=1280\n" in text and "WARP_PROVIDER=proton\n" in text


def test_vpn_mode_only_and_both_at_once(tmp_path):
    _setup(tmp_path)
    set_warp_modes(tmp_path, vpn="1")
    assert "VPN_WARP=1\n" in _read(tmp_path) and "ANTIZAPRET_WARP=2\n" in _read(tmp_path)
    set_warp_modes(tmp_path, antizapret="4", vpn="2")
    assert "ANTIZAPRET_WARP=4\n" in _read(tmp_path) and "VPN_WARP=2\n" in _read(tmp_path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"antizapret": "5"},
        {"antizapret": "0"},
        {"antizapret": "y"},
        {"antizapret": "2\nEVIL=1"},
        {"vpn": "3"},
        {"vpn": "4"},
        {"vpn": "$(reboot)"},
        {},
    ],
)
def test_invalid_modes_are_rejected_and_setup_is_untouched(tmp_path, kwargs):
    _setup(tmp_path)
    with pytest.raises(WarpModeError):
        set_warp_modes(tmp_path, **kwargs)
    assert _read(tmp_path) == SETUP


def test_missing_mode_is_added_to_a_setup_without_it(tmp_path):
    _setup(tmp_path, "WARP_PROVIDER=proton\n")
    set_warp_modes(tmp_path, antizapret="3", vpn="2")
    assert "ANTIZAPRET_WARP=3\n" in _read(tmp_path) and "VPN_WARP=2\n" in _read(tmp_path)


def test_status_reflects_saved_modes(tmp_path):
    _setup(tmp_path)
    set_warp_modes(tmp_path, antizapret="4", vpn="1")
    status = read_warp_status(tmp_path)
    assert status["antizapret_warp"] == "4" and status["vpn_warp"] == "1"


def test_local_adapter_delegates_and_turns_invalid_mode_into_400(tmp_path):
    from app.services.node_adapter import LocalNodeAdapter

    adapter = LocalNodeAdapter(service=MagicMock(base_path=tmp_path))
    with patch("app.services.warp_geo.set_warp_modes", return_value={"success": True}) as fn:
        adapter.set_warp_modes("4", None)
    fn.assert_called_once_with(tmp_path, antizapret="4", vpn=None)

    _setup(tmp_path)
    with pytest.raises(HTTPException) as exc:
        adapter.set_warp_modes("9", None)
    assert exc.value.status_code == 400


def test_remote_adapter_hits_the_modes_route():
    from app.services.node_adapter import RemoteNodeAdapter

    adapter = RemoteNodeAdapter("127.0.0.1", 9100, "secret")
    with patch.object(adapter, "_request", return_value={"success": True}) as request:
        adapter.set_warp_modes("3", "2")
    request.assert_called_once_with(
        "POST", "/warp-geo/modes", json={"antizapret": "3", "vpn": "2"}, timeout=30.0,
    )


def test_node_agent_endpoint_validates_and_writes(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("NODE_AGENT_MODE", "dev")
    monkeypatch.setenv("NODE_AGENT_API_KEY", "n" * 32)
    os.environ.pop("NODE_AGENT_ALLOWED_IPS", None)

    import node_agent.main as agent_main
    from fastapi.testclient import TestClient

    _setup(tmp_path)
    monkeypatch.setattr(agent_main, "ANTIZAPRET_PATH", tmp_path)
    headers = {"X-Node-Key": agent_main.NODE_AGENT_API_KEY}
    client = TestClient(agent_main.app)

    ok = client.post("/warp-geo/modes", json={"antizapret": "4", "vpn": "2"}, headers=headers)
    assert ok.status_code == 200
    assert "ANTIZAPRET_WARP=4" in _read(tmp_path).splitlines()
    assert client.post("/warp-geo/modes", json={"antizapret": "7"}, headers=headers).status_code == 400
    assert client.post("/warp-geo/modes", json={}, headers=headers).status_code == 400
    assert client.post("/warp-geo/modes", json={"antizapret": "1"}).status_code in (401, 403, 422)
    assert "ANTIZAPRET_WARP=4" in _read(tmp_path).splitlines()


def test_missing_setup_is_a_clear_404_not_a_500(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("NODE_AGENT_MODE", "dev")
    monkeypatch.setenv("NODE_AGENT_API_KEY", "n" * 32)
    os.environ.pop("NODE_AGENT_ALLOWED_IPS", None)

    import node_agent.main as agent_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(agent_main, "ANTIZAPRET_PATH", tmp_path)
    headers = {"X-Node-Key": agent_main.NODE_AGENT_API_KEY}
    response = TestClient(agent_main.app).post("/warp-geo/modes", json={"antizapret": "2"}, headers=headers)
    assert response.status_code == 404 and "setup" in response.json()["detail"]

    from app.services.node_adapter import LocalNodeAdapter
    from unittest.mock import MagicMock

    with pytest.raises(HTTPException) as exc:
        LocalNodeAdapter(service=MagicMock(base_path=tmp_path)).set_warp_modes("2", None)
    assert exc.value.status_code == 404
