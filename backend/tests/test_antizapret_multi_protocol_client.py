"""Regression tests for a real production bug: the panel's "create client" dialog calls
add_openvpn_client/add_wireguard_client/add_amneziawg2_client separately, once per selected
protocol checkbox. client.sh has no per-protocol "add" anymore (option 1 creates OpenVPN +
WireGuard + AmneziaWG 1.5 + native AmneziaWG 2.0 together), so the second call for the same
client name used to re-hit client.sh's addOpenVPN() "already exists" branch, which — with no
cert-expire-days carried over — falls into an interactive `read` that dies on closed stdin
under `set -e`, surfacing as "Client with that name already exists!" and a hard failure.
"""

from __future__ import annotations

from pathlib import Path

from app.services.antizapret import AntiZapretService


def _make_service(tmp_path: Path) -> AntiZapretService:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "client.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return AntiZapretService(base_path=tmp_path)


def test_not_provisioned_for_a_brand_new_name(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    monkeypatch.setattr("app.services.antizapret.EASYRSA3_ROOT", tmp_path / "easyrsa3")
    monkeypatch.setattr("app.services.antizapret.WIREGUARD_SERVER_CONFIG_DIR", tmp_path / "wireguard")
    monkeypatch.setattr("app.services.antizapret.NATIVE_AWG2_SERVER_DIR", tmp_path / "amneziawg")
    monkeypatch.setattr(
        "app.services.antizapret.NATIVE_AWG2_TUNNELS",
        {
            "antizapret": (tmp_path / "amneziawg" / "antizapret2.conf", "antizapret"),
            "vpn": (tmp_path / "amneziawg" / "vpn2.conf", "vpn"),
        },
    )
    assert service._client_already_provisioned("newbie") is False


def test_provisioned_when_openvpn_cert_already_issued(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    issued = tmp_path / "easyrsa3" / "pki" / "issued"
    issued.mkdir(parents=True)
    (issued / "alice.crt").write_text("cert", encoding="utf-8")
    monkeypatch.setattr("app.services.antizapret.EASYRSA3_ROOT", tmp_path / "easyrsa3")
    monkeypatch.setattr("app.services.antizapret.WIREGUARD_SERVER_CONFIG_DIR", tmp_path / "wireguard")
    monkeypatch.setattr("app.services.antizapret.NATIVE_AWG2_SERVER_DIR", tmp_path / "amneziawg")
    monkeypatch.setattr(
        "app.services.antizapret.NATIVE_AWG2_TUNNELS",
        {"antizapret": (tmp_path / "amneziawg" / "antizapret2.conf", "antizapret")},
    )
    assert service._client_already_provisioned("alice") is True


def test_provisioned_when_wireguard_peer_already_exists(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    wg_dir = tmp_path / "wireguard"
    wg_dir.mkdir()
    (wg_dir / "antizapret.conf").write_text("# Client = bob\n[Peer]\n", encoding="utf-8")
    (wg_dir / "vpn.conf").write_text("", encoding="utf-8")
    monkeypatch.setattr("app.services.antizapret.EASYRSA3_ROOT", tmp_path / "easyrsa3")
    monkeypatch.setattr("app.services.antizapret.WIREGUARD_SERVER_CONFIG_DIR", wg_dir)
    monkeypatch.setattr("app.services.antizapret.NATIVE_AWG2_SERVER_DIR", tmp_path / "amneziawg")
    monkeypatch.setattr(
        "app.services.antizapret.NATIVE_AWG2_TUNNELS",
        {"antizapret": (tmp_path / "amneziawg" / "antizapret2.conf", "antizapret")},
    )
    assert service._client_already_provisioned("bob") is True
    assert service._client_already_provisioned("carol") is False


def test_provisioned_when_native_awg2_peer_already_exists(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    az_dir = tmp_path / "amneziawg"
    az_dir.mkdir()
    (az_dir / "antizapret2.conf").write_text("# Client = dave\n[Peer]\n", encoding="utf-8")
    monkeypatch.setattr("app.services.antizapret.EASYRSA3_ROOT", tmp_path / "easyrsa3")
    monkeypatch.setattr("app.services.antizapret.WIREGUARD_SERVER_CONFIG_DIR", tmp_path / "wireguard")
    monkeypatch.setattr("app.services.antizapret.NATIVE_AWG2_SERVER_DIR", az_dir)
    monkeypatch.setattr(
        "app.services.antizapret.NATIVE_AWG2_TUNNELS",
        {"antizapret": (az_dir / "antizapret2.conf", "antizapret")},
    )
    assert service._client_already_provisioned("dave") is True


def test_add_wireguard_client_skips_client_sh_when_already_provisioned(tmp_path, monkeypatch):
    """The exact bug: openvpn was added first (real client.sh call), then the panel's second
    call — for wireguard, same name — must NOT re-invoke client.sh."""
    service = _make_service(tmp_path)
    monkeypatch.setattr(service, "_client_already_provisioned", lambda _name: True)
    calls: list[tuple] = []
    monkeypatch.setattr(
        AntiZapretService, "_run_client_script", lambda self, *a, **kw: calls.append(a) or "unexpected"
    )

    result = service.add_wireguard_client("existing")

    assert calls == []
    assert "уже существует" in result


def test_add_amneziawg2_client_skips_but_still_syncs_obfuscation(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    monkeypatch.setattr(service, "_client_already_provisioned", lambda _name: True)
    override_calls: list[str] = []
    monkeypatch.setattr(service, "_apply_native_awg2_overrides", lambda name: override_calls.append(name))
    monkeypatch.setattr(
        AntiZapretService, "_run_client_script", lambda self, *a, **kw: (_ for _ in ()).throw(AssertionError("must not run client.sh"))
    )

    result = service.add_amneziawg2_client("existing")

    assert override_calls == ["existing"]
    assert "уже существует" in result


def test_add_openvpn_client_skips_when_provisioned_and_not_forced(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    monkeypatch.setattr(service, "_client_already_provisioned", lambda _name: True)
    monkeypatch.setattr(
        AntiZapretService, "_run_client_script", lambda self, *a, **kw: (_ for _ in ()).throw(AssertionError("must not run client.sh"))
    )

    result = service.add_openvpn_client("existing", 3650)

    assert "уже существует" in result


def test_add_openvpn_client_force_bypasses_guard_for_cert_renewal(tmp_path, monkeypatch):
    """renew_cert / PATCH-config-with-cert_expire_days legitimately need to re-hit client.sh
    (addOpenVPN's "already exists" branch re-signs when a valid days value is supplied) —
    force=True must skip the idempotency guard entirely."""
    service = _make_service(tmp_path)
    monkeypatch.setattr(service, "_client_already_provisioned", lambda _name: True)
    calls: list[tuple] = []
    monkeypatch.setattr(
        AntiZapretService,
        "_run_client_script",
        lambda self, *a, **kw: calls.append(a) or "renewed",
    )

    result = service.add_openvpn_client("existing", 30, force=True)

    assert calls == [("1", "existing", "30")]
    assert result == "renewed"
