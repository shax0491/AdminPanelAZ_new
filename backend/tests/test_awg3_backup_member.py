"""AWG 3.0 layer rides in the panel backup archive like AWG 2.0 does."""

import sqlite3
from pathlib import Path

from app.services.backup_manager import BackupManager


def _manager(tmp_path: Path) -> BackupManager:
    db = tmp_path / "adminpanel.db"
    sqlite3.connect(db).close()
    env = tmp_path / ".env"
    env.write_text("X=1\n", encoding="utf-8")
    return BackupManager(
        app_root=tmp_path,
        backup_root=tmp_path / "backups",
        db_path=db,
        env_path=env,
    )


def test_awg3_archive_is_recorded_inspected_and_returned_on_restore(tmp_path: Path):
    mgr = _manager(tmp_path)
    payload = b"fake-awg3-state-archive"

    result = mgr.create_backup(awg3_archive=payload)
    assert "awg3" in result["components"]

    archive = Path(result["file_path"])
    assert "awg3" in mgr.inspect_backup_archive(archive)["components"]

    restored = mgr.load_restore_payload(result["file_name"])
    assert restored["_files"]["awg3"] == payload
    assert "awg3" in restored["restored"]


def test_archive_without_awg3_has_no_awg3_component(tmp_path: Path):
    mgr = _manager(tmp_path)
    result = mgr.create_backup()
    assert "awg3" not in result["components"]
    assert "awg3" not in mgr.load_restore_payload(result["file_name"])["_files"]
