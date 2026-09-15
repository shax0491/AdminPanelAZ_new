"""Warp Geo: безопасный статус WARP из /root/antizapret/setup + парсинг гео-проверки.

Covers app/services/warp_geo.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.services.warp_geo as warp_geo
from app.services.warp_geo import check_warp_geo, read_warp_status

SETUP_CONTENT = """SETUP_DATE=2026-09-15T00:00:00+00:00
WARP_PROVIDER=proton
ANTIZAPRET_WARP=2
ANTIZAPRET_WARP_PRIVATE_KEY=
ANTIZAPRET_WARP_PUBLIC_KEY=
VPN_WARP=2
PROTON_ANTIZAPRET_PRIVATE_KEY=abcdef1234567890
PROTON_ANTIZAPRET_PUBLIC_KEY=pubkeyhere
PROTON_ANTIZAPRET_ADDRESS=10.2.0.2
PROTON_VPN_PRIVATE_KEY=
PROTON_VPN_PUBLIC_KEY=
PROTON_VPN_ADDRESS=
ANTIZAPRET_DNS=1
VPN_DNS=1
"""


def test_read_warp_status_returns_safe_fields_without_keys(tmp_path):
    (tmp_path / "setup").write_text(SETUP_CONTENT, encoding="utf-8")

    status = read_warp_status(tmp_path)

    assert status["warp_provider"] == "proton"
    assert status["antizapret_warp"] == "2"
    assert status["vpn_warp"] == "2"
    # Proton configured for antizapret (private key non-empty), not for vpn (empty).
    assert status["proton_antizapret_configured"] is True
    assert status["proton_vpn_configured"] is False
    # Никакие ключи не должны попасть в ответ ни под каким именем.
    dumped = str(status)
    assert "abcdef1234567890" not in dumped
    assert "pubkeyhere" not in dumped


def test_read_warp_status_missing_setup_file_returns_empty_safe_defaults(tmp_path):
    status = read_warp_status(tmp_path)

    assert status["warp_provider"] == ""
    assert status["proton_antizapret_configured"] is False


def _fake_run(responses: dict[str, tuple[int, str]]):
    def fake(args, **_kwargs):
        url = args[-1]
        rc, out = responses.get(url, (1, ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr="" if rc == 0 else "curl error")

    return fake


def _patch_subprocess(monkeypatch, curl_responses: dict[str, tuple[int, str]], *, ip_addr_output: str = ""):
    def fake_run(args, **kwargs):
        if args[:1] == ["curl"]:
            return _fake_run(curl_responses)(args, **kwargs)
        if args[:1] == ["ip"]:
            return SimpleNamespace(returncode=0, stdout=ip_addr_output, stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(
        warp_geo,
        "subprocess",
        SimpleNamespace(run=fake_run, TimeoutExpired=Exception),
    )


def test_check_warp_geo_reports_gemini_blocked(monkeypatch, tmp_path):
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=1.2.3.4\nloc=RU\ncolo=DME\n"),
            "https://www.youtube.com/": (0, '{"GL":"RU"}'),
            "https://gemini.google.com/": (0, "<html>Gemini is not available in your country</html>"),
        },
    )

    result = check_warp_geo("raw", tmp_path)

    assert result["gemini_status"] == "blocked"


def test_check_warp_geo_reports_gemini_ok(monkeypatch, tmp_path):
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=1.2.3.4\nloc=GB\ncolo=LHR\n"),
            "https://www.youtube.com/": (0, '{"GL":"CA"}'),
            "https://gemini.google.com/": (0, "<html>normal page</html>"),
        },
    )

    result = check_warp_geo("raw", tmp_path)

    assert result["gemini_status"] == "ok"


def test_check_warp_geo_reports_gemini_unknown_on_curl_failure(monkeypatch, tmp_path):
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=1.2.3.4\nloc=GB\ncolo=LHR\n"),
            "https://www.youtube.com/": (0, '{"GL":"CA"}'),
            # gemini.google.com not in the map -> curl "fails" (rc=1)
        },
    )

    result = check_warp_geo("raw", tmp_path)

    assert result["gemini_status"] == "unknown"


def test_check_warp_geo_parses_trace_and_youtube_gl(monkeypatch, tmp_path):
    trace_out = "ip=185.193.51.13\nts=169000.0\nloc=LV\ncolo=ARN\n"
    youtube_out = '<html>...{"GL":"LV","other":1}...</html>'
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, trace_out),
            "https://www.youtube.com/": (0, youtube_out),
        },
    )

    result = check_warp_geo("antizapret", tmp_path)

    assert result["interface"] == "warp-antizapret"
    assert result["cloudflare_loc"] == "LV"
    assert result["cloudflare_colo"] == "ARN"
    assert result["youtube_gl"] == "LV"
    assert result["flagged_as_ru"] is False


def test_check_warp_geo_flags_ru(monkeypatch, tmp_path):
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=1.2.3.4\nloc=RU\ncolo=DME\n"),
            "https://www.youtube.com/": (0, '{"GL":"RU"}'),
        },
    )

    result = check_warp_geo("raw", tmp_path)

    assert result["interface"] is None
    assert result["flagged_as_ru"] is True


def test_check_warp_geo_interface_down_returns_error_not_raw_ip_geo(monkeypatch, tmp_path):
    # Regression: if warp-antizapret isn't up, must not silently fall back to
    # testing the raw host route and report it as if it were the WARP egress.
    _patch_subprocess(monkeypatch, {})  # every curl call "fails" (not in the map -> rc=1)

    result = check_warp_geo("vpn", tmp_path)

    assert "error" in result
    assert "warp-vpn" in result["error"]
    assert "cloudflare_loc" not in result


def test_check_warp_geo_rejects_unknown_scope_at_type_level():
    # scope is validated at the router layer (400 for anything outside the
    # three known values) - the service itself only maps known scopes.
    assert set(warp_geo._SCOPE_INTERFACE) == {"antizapret", "vpn", "raw"}


def test_check_warp_geo_flags_stale_tunnel_not_matching_proton_config(monkeypatch, tmp_path):
    # Real incident: setup was hand-edited to Proton with valid keys, but
    # /root/antizapret/up.sh was never re-run - the live warp-antizapret
    # interface kept an OLD Cloudflare registration (different local IP) for
    # days, silently. The geo-check result was technically accurate for
    # whatever was actually running, but nothing flagged that it wasn't the
    # provider the config claims. Must be surfaced explicitly.
    (tmp_path / "setup").write_text(SETUP_CONTENT, encoding="utf-8")
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=104.28.254.37\nloc=LV\ncolo=ARN\n"),
            "https://www.youtube.com/": (0, '{"GL":"RU"}'),
        },
        ip_addr_output="12: warp-antizapret    inet 172.16.0.2/32 scope global warp-antizapret\n",
    )

    result = check_warp_geo("antizapret", tmp_path)

    assert result["tunnel_matches_config"] is False
    assert "172.16.0.2" in result["tunnel_mismatch_detail"]
    assert "10.2.0.2" in result["tunnel_mismatch_detail"]
    assert "up.sh" in result["tunnel_mismatch_detail"]


def test_check_warp_geo_tunnel_matches_config_when_addresses_agree(monkeypatch, tmp_path):
    (tmp_path / "setup").write_text(SETUP_CONTENT, encoding="utf-8")
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=74.118.126.132\nloc=GB\ncolo=LHR\n"),
            "https://www.youtube.com/": (0, '{"GL":"CA"}'),
        },
        ip_addr_output="16: warp-antizapret    inet 10.2.0.2/32 scope global warp-antizapret\n",
    )

    result = check_warp_geo("antizapret", tmp_path)

    assert result["tunnel_matches_config"] is True
    assert "tunnel_mismatch_detail" not in result


def test_check_warp_geo_skips_tunnel_check_for_cloudflare_provider(monkeypatch, tmp_path):
    # No fixed expected address for Cloudflare's anonymous auto-registration -
    # nothing to compare against, so the field must simply be absent, not a
    # false mismatch.
    (tmp_path / "setup").write_text(SETUP_CONTENT.replace("WARP_PROVIDER=proton", "WARP_PROVIDER=cloudflare"), encoding="utf-8")
    _patch_subprocess(
        monkeypatch,
        {
            "https://1.1.1.1/cdn-cgi/trace": (0, "ip=1.2.3.4\nloc=LV\ncolo=ARN\n"),
            "https://www.youtube.com/": (0, '{"GL":"LV"}'),
        },
        ip_addr_output="12: warp-antizapret    inet 172.16.0.2/32 scope global warp-antizapret\n",
    )

    result = check_warp_geo("antizapret", tmp_path)

    assert "tunnel_matches_config" not in result


def test_local_node_adapter_delegates_to_warp_geo_service(tmp_path):
    from unittest.mock import MagicMock, patch

    from app.services.node_adapter import LocalNodeAdapter

    adapter = LocalNodeAdapter(service=MagicMock(base_path=tmp_path))
    with patch("app.services.warp_geo.read_warp_status", return_value={"warp_provider": "proton"}) as read_status:
        result = adapter.get_warp_geo_status()
    assert result == {"warp_provider": "proton"}
    read_status.assert_called_once_with(tmp_path)

    with patch("app.services.warp_geo.check_warp_geo", return_value={"flagged_as_ru": False}) as check:
        result = adapter.check_warp_geo("antizapret")
    assert result == {"flagged_as_ru": False}
    check.assert_called_once_with("antizapret", tmp_path)


def test_remote_node_adapter_hits_warp_geo_agent_paths():
    from unittest.mock import patch

    from app.services.node_adapter import RemoteNodeAdapter

    adapter = RemoteNodeAdapter("127.0.0.1", 9100, "secret")

    with patch.object(adapter, "_request", return_value={"warp_provider": "proton"}) as request:
        result = adapter.get_warp_geo_status()
    assert result == {"warp_provider": "proton"}
    request.assert_called_once_with("GET", "/warp-geo/status", timeout=30.0)

    with patch.object(adapter, "_request", return_value={"flagged_as_ru": True}) as request:
        result = adapter.check_warp_geo("vpn")
    assert result == {"flagged_as_ru": True}
    request.assert_called_once_with("GET", "/warp-geo/check", params={"scope": "vpn"}, timeout=30.0)
