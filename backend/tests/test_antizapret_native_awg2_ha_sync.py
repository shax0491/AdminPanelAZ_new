"""Native AmneziaWG 2.0 HA-sync building blocks on AntiZapretService: server config/key
read-write and client profile archive export/import, mirroring the existing WireGuard
equivalents (read_wireguard_server_config, export_wireguard_client_profiles_archive, ...).
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.antizapret import AntiZapretService


def _make_service(tmp_path: Path) -> AntiZapretService:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "client.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return AntiZapretService(base_path=tmp_path)


def test_server_config_roundtrip_and_listing(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    az_dir = tmp_path / "etc-amneziawg"
    monkeypatch.setattr("app.services.antizapret.NATIVE_AWG2_SERVER_DIR", az_dir)

    assert service.list_amneziawg2_server_config_files() == []
    assert service.read_amneziawg2_server_config("antizapret2") == ""

    service.write_amneziawg2_server_config("antizapret2", "content-a")
    service.write_amneziawg2_server_config("vpn2", "content-v")

    assert service.list_amneziawg2_server_config_files() == ["antizapret2.conf", "vpn2.conf"]
    assert service.read_amneziawg2_server_config("antizapret2") == "content-a"

    service.delete_amneziawg2_server_config_file("vpn2.conf")
    assert service.list_amneziawg2_server_config_files() == ["antizapret2.conf"]


def test_server_config_rejects_unknown_interface(tmp_path):
    service = _make_service(tmp_path)
    with pytest.raises(HTTPException):
        service.read_amneziawg2_server_config("not-a-real-interface")


def test_server_config_delete_rejects_path_traversal(tmp_path):
    service = _make_service(tmp_path)
    with pytest.raises(HTTPException):
        service.delete_amneziawg2_server_config_file("../../etc/passwd")
    with pytest.raises(HTTPException):
        service.delete_amneziawg2_server_config_file("antizapret2")  # no .conf suffix


def test_server_key_roundtrip(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    az_dir = tmp_path / "etc-amneziawg"
    monkeypatch.setattr("app.services.antizapret.NATIVE_AWG2_SERVER_DIR", az_dir)

    assert service.read_amneziawg2_server_key() == ""
    service.write_amneziawg2_server_key("PRIVATE_KEY=abc\nPUBLIC_KEY=def\n")
    assert service.read_amneziawg2_server_key() == "PRIVATE_KEY=abc\nPUBLIC_KEY=def\n"

    # Writing empty content must not clobber an already-synced key (HA sync passes
    # read_amneziawg2_server_key()'s "" when the primary genuinely has none yet).
    service.write_amneziawg2_server_key("")
    assert service.read_amneziawg2_server_key() == "PRIVATE_KEY=abc\nPUBLIC_KEY=def\n"


def test_client_profiles_archive_roundtrip(tmp_path):
    service = _make_service(tmp_path)
    profile_dir = service.client_dir / "amneziawg2" / "antizapret"
    profile_dir.mkdir(parents=True)
    (profile_dir / "antizapret2-alice-am2.conf").write_text("alice-profile", encoding="utf-8")

    archive = service.export_amneziawg2_client_profiles_archive()
    assert archive

    with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:gz") as tar:
        names = tar.getnames()
    assert "client/amneziawg2/antizapret/antizapret2-alice-am2.conf" in names

    # Import into a fresh service (simulating the replica) and verify the file lands.
    replica = _make_service(tmp_path / "replica")
    replica.import_amneziawg2_client_profiles_archive(archive)
    restored = replica.client_dir / "amneziawg2" / "antizapret" / "antizapret2-alice-am2.conf"
    assert restored.read_text(encoding="utf-8") == "alice-profile"


def test_import_client_profiles_rejects_empty_or_foreign_archive(tmp_path):
    service = _make_service(tmp_path)

    with pytest.raises(HTTPException):
        service.import_amneziawg2_client_profiles_archive(b"")

    buffer = __import__("io").BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="client/openvpn/antizapret/antizapret-alice.ovpn")
        data = b"not an awg2 profile"
        info.size = len(data)
        tar.addfile(info, __import__("io").BytesIO(data))

    with pytest.raises(HTTPException):
        service.import_amneziawg2_client_profiles_archive(buffer.getvalue())


def test_apply_amneziawg2_runtime_delegates_to_native_runtime(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    monkeypatch.setattr(
        "app.services.native_awg2_runtime.sync_all_native_awg2_interfaces",
        lambda: {"success": True, "synced": ["antizapret2", "vpn2"], "error_count": 0, "errors": []},
    )
    result = service.apply_amneziawg2_runtime()
    assert result["success"] is True
    assert result["synced"] == ["antizapret2", "vpn2"]
