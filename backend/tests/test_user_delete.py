"""Regression: deleting a user who created an UnlockCode or a ClientPortalToken must not 500.

The 2.25.0 upstream merge added unlock_codes.created_by_user_id and
client_portal_tokens.created_by_user_id (both FKs to users.id), but
_purge_user_before_delete was never updated to null them out before the delete — with SQLite
foreign_keys=ON (enabled on every real connection, see app/database.py), deleting such a user
raised an uncaught IntegrityError, surfacing to the admin as a plain 500 Internal Server Error.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import ClientPortalToken, Node, UnlockCode, User, UserRole
from app.routers.users import delete_user


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_delete_user_who_created_unlock_code_and_portal_token(db, monkeypatch):
    monkeypatch.setattr("app.routers.users.settings", SimpleNamespace(audit_log_enabled=False))
    monkeypatch.setattr("app.routers.users.admin_notify_service.send_user_delete", lambda *_a, **_kw: None)

    admin = User(username="admin", password_hash="x", role=UserRole.admin, is_active=True)
    creator = User(username="creator", password_hash="x", role=UserRole.user, is_active=True)
    db.add_all([admin, creator])
    db.commit()

    node = Node(name="node-1", host="127.0.0.1", is_local=True)
    db.add(node)
    db.commit()

    db.add(
        UnlockCode(
            code="ABCD-1234",
            grant_days=30,
            mode="single",
            max_redemptions=1,
            created_by_user_id=creator.id,
        )
    )
    db.add(
        ClientPortalToken(
            token="tok-1",
            node_id=node.id,
            client_name="alice",
            created_by_user_id=creator.id,
        )
    )
    db.commit()

    request = SimpleNamespace(headers={})
    result = delete_user(creator.id, request, db=db, current_user=admin)

    assert result.message == "Пользователь удалён"
    assert db.query(User).filter(User.id == creator.id).first() is None
    assert db.query(UnlockCode).filter(UnlockCode.code == "ABCD-1234").one().created_by_user_id is None
    assert db.query(ClientPortalToken).filter(ClientPortalToken.token == "tok-1").one().created_by_user_id is None
