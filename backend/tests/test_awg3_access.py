"""AmneziaWG 3 deadline and blocks: state machine and node runtime calls."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AmneziaWg3AccessPolicy
from app.services import awg3_access as acc


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, node, client_name, is_blocked):
        self.calls.append((client_name, is_blocked))


NODE = SimpleNamespace(id=1)


def test_temp_block_then_manual_unblock_drives_runtime(db):
    rec = Recorder()
    state = acc.set_temp_block(db, NODE, "ivan", 3, actor="admin", apply=rec)
    assert state["is_blocked"] is True and state["block_mode"] == "temp"
    assert rec.calls == [("ivan", True)]
    row = acc.get_row(db, 1, "ivan")
    assert row.block_reason == "manual_temp" and row.block_days == 3

    state = acc.reconcile(db, NODE, "ivan", force_runtime=True, apply=rec)
    assert state["is_blocked"] is True
    assert rec.calls == [("ivan", True), ("ivan", True)]

    acc.unblock(db, NODE, "ivan", actor="admin", apply=rec)
    row = acc.get_row(db, 1, "ivan")
    assert row.is_temp_blocked is False and row.block_reason is None


def test_deadline_in_past_blocks_and_extension_unblocks(db):
    rec = Recorder()
    row = acc.get_row(db, 1, "anna", create=True)
    row.access_until = datetime.utcnow() - timedelta(minutes=5)
    db.commit()

    state = acc.reconcile(db, NODE, "anna", apply=rec)
    assert state["access_expired"] is True and state["is_blocked"] is True
    assert acc.get_row(db, 1, "anna").block_reason == "access_expired"
    assert rec.calls == [("anna", True)]

    # Extension: runtime restored, reason cleared; a second reconcile sends nothing new.
    row = acc.get_row(db, 1, "anna")
    row.access_until = datetime.utcnow() + timedelta(days=30)
    db.commit()
    state = acc.reconcile(db, NODE, "anna", apply=rec)
    assert state["is_blocked"] is False
    assert acc.get_row(db, 1, "anna").block_reason is None
    assert rec.calls == [("anna", True), ("anna", False)]

    acc.reconcile(db, NODE, "anna", apply=rec)
    assert rec.calls == [("anna", True), ("anna", False)]


def test_permanent_block_survives_deadline_extension(db):
    rec = Recorder()
    acc.set_permanent_block(db, NODE, "bob", actor="admin", apply=rec)
    row = acc.get_row(db, 1, "bob")
    row.access_until = datetime.utcnow() + timedelta(days=10)
    db.commit()
    state = acc.reconcile(db, NODE, "bob", apply=rec)
    assert state["is_blocked"] is True and state["block_mode"] == "permanent"
    assert acc.get_row(db, 1, "bob").block_reason == "manual_permanent"


def test_reconcile_unknown_client_is_noop(db):
    rec = Recorder()
    assert acc.reconcile(db, NODE, "nobody", apply=rec) is None
    assert rec.calls == []


def test_row_is_separate_table_for_awg3():
    assert AmneziaWg3AccessPolicy.__tablename__ == "amneziawg3_access_policies"
