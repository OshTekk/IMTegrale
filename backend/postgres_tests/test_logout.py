from __future__ import annotations

import json

import pytest
from app.config import get_settings
from app.database import SessionLocal
from app.models import Account, WebSession
from app.routers import auth as auth_router
from app.security import AuthContext, create_web_session
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError


def _seed_two_sessions() -> tuple[str, str, str]:
    settings = get_settings()
    with SessionLocal() as db:
        account = Account(
            imt_username="postgres.logout.fictif@example.test",
            display_name="[FICTIF] PostgreSQL logout",
        )
        db.add(account)
        db.flush()
        current, _current_token, _current_csrf = create_web_session(
            db,
            account=account,
            role="owner",
            auth_method="imt",
            user_agent="postgres-logout-current-fictif",
            settings=settings,
        )
        other, _other_token, _other_csrf = create_web_session(
            db,
            account=account,
            role="owner",
            auth_method="imt",
            user_agent="postgres-logout-other-fictif",
            settings=settings,
        )
        db.commit()
        return account.id, current.id, other.id


def test_postgres_logout_commits_only_the_current_web_session() -> None:
    account_id, current_id, other_id = _seed_two_sessions()

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        current = db.get(WebSession, current_id)
        assert account is not None
        assert current is not None
        response = auth_router.logout(
            auth=AuthContext(account=account, session=current),
            db=db,
            settings=get_settings(),
        )

    assert response.status_code == 200
    assert json.loads(response.body) == {"ok": True}
    with SessionLocal() as reader:
        assert reader.get(WebSession, current_id) is None
        assert reader.get(WebSession, other_id) is not None


def test_postgres_logout_commit_error_rolls_back_without_success_or_cookie_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_id, current_id, other_id = _seed_two_sessions()
    cookie_clear_calls: list[object] = []
    monkeypatch.setattr(
        auth_router,
        "clear_session_cookies",
        lambda response, _settings: cookie_clear_calls.append(response),
    )

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        current = db.get(WebSession, current_id)
        assert account is not None
        assert current is not None

        def reject_commit(session):  # noqa: ANN001
            session.connection().exec_driver_sql("SELECT 1 / 0")

        event.listen(db, "before_commit", reject_commit, once=True)
        with pytest.raises(DBAPIError):
            auth_router.logout(
                auth=AuthContext(account=account, session=current),
                db=db,
                settings=get_settings(),
            )
        db.rollback()

    assert cookie_clear_calls == []
    with SessionLocal() as reader:
        assert reader.get(WebSession, current_id) is not None
        assert reader.get(WebSession, other_id) is not None
