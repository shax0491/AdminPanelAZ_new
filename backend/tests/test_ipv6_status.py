from __future__ import annotations

from types import SimpleNamespace

from app.services.antizapret_params import KNOWN_SETTING_KEYS
from app.services.file_editor import EDITABLE_FILES, MIN_AGENT_VERSION_FOR_FILE
from app.services.ipv6_status import read_ipv6_status
from app.services.kresd_aaaa import NODATA_BLOCK, aaaa_mode, with_aaaa_nodata


def _fake_run(cmd, **_):
    if cmd[:3] == ["ip", "-6", "route"]:
        return SimpleNamespace(stdout="default via 2a0c:4ac1:4::1 dev ens3 metric 1024\n")
    return SimpleNamespace(stdout="elements = { fd00:30::1 : 2606:4700::1, fd00:30::2 : 2606:4700::2 }\n")


def test_status_reads_setup_state_and_system(tmp_path):
    setup = tmp_path / "setup"
    setup.write_text("ENABLE_IPV6=y\nIPV6_MODE=c\nIPV6_EGRESS=native\n")
    state = tmp_path / "state"
    state.write_text("requested=c\neffective=a\negress=native\ninterface=ens3\nreason=no egress\nupdated=1\n")
    st = read_ipv6_status(setup, state_file=state, mode_file=tmp_path / "none", run=_fake_run)
    assert st["enabled"] is True and st["requested_mode"] == "c" and st["effective_mode"] == "a"
    assert st["reason"] == "no egress" and st["native_default_route"] is True
    assert st["fake6_mappings"] == 2 and st["profile_default"] == "dual"


def test_status_defaults_when_nothing_present(tmp_path):
    st = read_ipv6_status(tmp_path / "setup", state_file=tmp_path / "s", mode_file=tmp_path / "m", run=lambda *a, **k: SimpleNamespace(stdout=""))
    assert st["enabled"] is False and st["effective_mode"] == "a" and st["fake6_mappings"] == 0


def test_ipv6_params_and_files_are_registered():
    assert {"ENABLE_IPV6", "IPV6_MODE", "IPV6_EGRESS", "IPV6_PROFILE_DEFAULT"} <= KNOWN_SETTING_KEYS
    for fname in ("include-ips6.txt", "exclude-ips6.txt", "ipv6-profile.txt"):
        assert fname in EDITABLE_FILES.values() and MIN_AGENT_VERSION_FOR_FILE[fname] == "1.26.0"


def test_aaaa_block_respects_ipv6_dual_and_upgrades_legacy():
    assert "not ipv6_dual" in NODATA_BLOCK
    legacy = with_aaaa_nodata("x\n", True).replace(" and not ipv6_dual", "")
    assert aaaa_mode(legacy) == "nodata"
    assert "not ipv6_dual" in with_aaaa_nodata(legacy, True)
    assert with_aaaa_nodata(legacy, False).strip() == "x"
