"""The node agent installs the AWG 3 rules scripts and unit from the panel copy, only when they differ."""

from pathlib import Path
from types import SimpleNamespace

from app.services import awg3_clients as svc


def _store(tmp_path, *, installed=True):
    (tmp_path / "conf").mkdir()
    if installed:
        (tmp_path / "conf" / "awg1.conf").write_text("[Interface]\n", encoding="utf-8")
    return svc.Awg3Store(conf_dir=tmp_path / "conf", client_dir=tmp_path / "client")


def _layout(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "awg3-rules.sh").write_bytes(b"#!/bin/sh\nnew rules\n")
    (src / "awg3-up.sh").write_bytes(b"#!/bin/sh\nup\n")
    (src / "awg3@.service").write_bytes(b"[Unit]\nPartOf=antizapret.service\n")
    dst = tmp_path / "dst"
    dst.mkdir()
    files = (
        ("awg3-rules.sh", dst / "awg3-rules.sh", 0o755),
        ("awg3-up.sh", dst / "awg3-up.sh", 0o755),
        ("awg3@.service", dst / "awg3@.service", 0o644),
    )
    return src, dst, files


class _Run:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        return SimpleNamespace(returncode=self.rc, stderr="boom" if self.rc else "")


def test_skipped_without_awg3_layer(tmp_path):
    src, _, files = _layout(tmp_path)
    run = _Run()
    res = svc.ensure_awg3_runtime(_store(tmp_path, installed=False), src_dir=src, files=files, run=run)
    assert res["skipped"] is True and run.calls == []


def test_installs_changed_files_reloads_and_restarts_once(tmp_path):
    src, dst, files = _layout(tmp_path)
    (dst / "awg3-rules.sh").write_bytes(b"old rules")
    run = _Run()
    res = svc.ensure_awg3_runtime(_store(tmp_path), src_dir=src, files=files, run=run)
    assert sorted(res["changed"]) == ["awg3-rules.sh", "awg3-up.sh", "awg3@.service"]
    assert (dst / "awg3-rules.sh").read_bytes() == b"#!/bin/sh\nnew rules\n"
    assert (dst / "awg3@.service").read_bytes().startswith(b"[Unit]")
    assert run.calls == [["systemctl", "daemon-reload"], ["systemctl", "restart", svc.UNIT]]
    assert res["restarted"] is True and res["error"] is None


def test_second_run_changes_nothing(tmp_path):
    src, _, files = _layout(tmp_path)
    store = _store(tmp_path)
    svc.ensure_awg3_runtime(store, src_dir=src, files=files, run=_Run())
    run = _Run()
    res = svc.ensure_awg3_runtime(store, src_dir=src, files=files, run=run)
    assert res == {"skipped": False, "changed": [], "restarted": False, "error": None}
    assert run.calls == []


def test_scripts_only_skip_daemon_reload(tmp_path):
    src, dst, files = _layout(tmp_path)
    (dst / "awg3@.service").write_bytes((src / "awg3@.service").read_bytes())
    (dst / "awg3-up.sh").write_bytes((src / "awg3-up.sh").read_bytes())
    run = _Run()
    res = svc.ensure_awg3_runtime(_store(tmp_path), src_dir=src, files=files, run=run)
    assert res["changed"] == ["awg3-rules.sh"]
    assert run.calls == [["systemctl", "restart", svc.UNIT]]


def test_reports_restart_failure(tmp_path):
    src, _, files = _layout(tmp_path)
    res = svc.ensure_awg3_runtime(_store(tmp_path), src_dir=src, files=files, run=_Run(rc=1))
    assert res["restarted"] is False and res["error"] == "boom"


def test_panel_copy_has_unix_endings_and_partof():
    root = svc.AWG3_RUNTIME_SRC
    for name in ("awg3-rules.sh", "awg3-up.sh", "awg3@.service"):
        assert b"\r" not in (root / name).read_bytes(), name
    assert "PartOf=antizapret.service" in (root / "awg3@.service").read_text(encoding="utf-8")


def test_panel_copy_matches_base_repo_when_available():
    """Drift guard: the panel copy must equal setup/root/antizapret/awg3 of the base repo (checked when it sits next to this repo)."""
    import os

    import pytest

    base = Path(os.environ.get("ANTIZAPRET_BASE_REPO", Path(__file__).resolve().parents[3] / "AntiZapret-VPN")) / "setup/root/antizapret/awg3"
    if not base.is_dir():
        pytest.skip("base repo not found next to the panel repo")
    for name in ("awg3-rules.sh", "awg3-up.sh", "awg3@.service"):
        base_text = (base / name).read_bytes().replace(b"\r\n", b"\n")
        assert (svc.AWG3_RUNTIME_SRC / name).read_bytes() == base_text, f"{name} differs from the base repo"
