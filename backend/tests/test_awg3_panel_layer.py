"""AWG 3.0 panel layer: router -> active node adapter -> awg3 service.

The node is faked at the adapter boundary; the local adapter path runs the real
awg3_clients logic against a temporary conf dir with a fake awg binary.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.auth import require_admin
from app.routers import awg3 as awg3_router
from app.services import awg3_clients as svc
from app.services.node_adapter import LocalNodeAdapter
from tests.test_awg3_clients import SERVER_CONF, FakeAwg


class FakeAdapter:
    def __init__(self):
        self.created: list[str] = []
        self.deleted: list[str] = []

    def awg3_health(self):
        return {"tools_present": True, "userspace_present": True, "conf_dir": "x", "ifaces": []}

    def awg3_monitoring(self):
        return {"ifaces": {}}

    def awg3_list_clients(self):
        return [{"name": n, "ip": "10.9.0.2", "public_key": "PUB"} for n in self.created if n not in self.deleted]

    def awg3_create_client(self, name, mode="split"):
        self.created.append(name)
        return {"name": name, "mode": mode, "ip": "10.9.0.2", "public_key": "PUB"}

    def awg3_client_config(self, name):
        if name not in self.created or name in self.deleted:
            raise svc.Awg3ClientError(f"client '{name}' not found")
        return "[Interface]\nAddress = 10.9.0.2/32\n"

    def awg3_delete_client(self, name):
        self.deleted.append(name)


@pytest.fixture
def client_app(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(awg3_router, "get_active_adapter", lambda db: adapter)
    app = FastAPI()
    app.include_router(awg3_router.router)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[require_admin] = lambda: None
    return TestClient(app), adapter


def test_router_create_list_config_delete_go_through_adapter(client_app):
    client, adapter = client_app
    r = client.post("/awg3/clients", json={"name": "router-cudy"})
    assert r.status_code == 201 and r.json()["name"] == "router-cudy"
    assert [c["name"] for c in client.get("/awg3/clients").json()["items"]] == ["router-cudy"]
    cfg = client.get("/awg3/clients/router-cudy/config").json()
    assert cfg["filename"] == "awg3-router-cudy.conf" and "Address = 10.9.0.2/32" in cfg["config"]
    assert client.delete("/awg3/clients/router-cudy").status_code == 204
    assert adapter.deleted == ["router-cudy"]


def test_router_maps_service_errors(client_app):
    client, _ = client_app
    assert client.get("/awg3/clients/nope/config").status_code == 404


def test_local_adapter_full_client_lifecycle(tmp_path: Path, monkeypatch):
    (tmp_path / "awg1.conf").write_text(SERVER_CONF, encoding="utf-8")
    real_store = svc.Awg3Store
    monkeypatch.setattr(svc, "Awg3Store", lambda *a, **k: real_store(conf_dir=tmp_path, client_dir=tmp_path / "client"))
    fake = FakeAwg()
    monkeypatch.setattr(svc, "_default_runner", fake)
    monkeypatch.setenv("AWG3_ENDPOINT_HOST", "nl1.example")
    split = tmp_path / "split.txt"
    split.write_text("1.1.1.1/32\n198.18.0.0/15\n", encoding="utf-8")
    monkeypatch.setenv("AWG3_SPLIT_ALLOWED_FILE", str(split))

    adapter = LocalNodeAdapter.__new__(LocalNodeAdapter)
    created = adapter.awg3_create_client("phone")
    assert created["ip"] == "10.9.0.2"
    assert [c["name"] for c in adapter.awg3_list_clients()] == ["phone"]
    cfg = adapter.awg3_client_config("phone")
    assert "Endpoint = nl1.example:" in cfg and "AllowedIPs = 10.9.0.0/24, 1.1.1.1/32, 198.18.0.0/15" in cfg
    adapter.awg3_delete_client("phone")
    assert adapter.awg3_list_clients() == []
    assert "# phone" not in (tmp_path / "awg1.conf").read_text(encoding="utf-8")


def test_endpoint_and_split_helpers_fail_clearly(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AWG3_ENDPOINT_HOST", raising=False)
    with pytest.raises(svc.Awg3ClientError):
        svc.endpoint_from_env()
    with pytest.raises(svc.Awg3ClientError):
        svc.split_allowed_from_file(str(tmp_path / "missing.txt"))
