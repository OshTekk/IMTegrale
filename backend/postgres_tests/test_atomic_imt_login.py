from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
import requests
from app.config import get_settings
from app.database import SessionLocal, get_db, utcnow
from app.main import app
from app.models import Account, PassServiceSession, WebSession
from app.models import Event as AccountEvent
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.security import login_global_rate_limiter, login_rate_limiter
from app.services import imt_login as imt_login_service
from app.services.imt import ImtAuthenticationError, ImtFetchError, ImtNetworkError, PassProfile
from app.services.login_rate_limits import _rate_key
from app.services.pass_gateway import GatewayResult
from app.services.pass_sessions import (
    PassSessionStorageUnavailable,
    serialize_service_cookies,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_AUTHENTICATED_USERNAME = object()


@pytest.fixture(autouse=True)
def reset_imt_login_limiters() -> Iterator[None]:
    keys = (
        _rate_key("peer:testclient|imt"),
        _rate_key("peer:testclient|imt-password"),
    )
    for key in keys:
        login_rate_limiter.reset(key)
    login_global_rate_limiter.reset("all-logins")
    yield
    for key in keys:
        login_rate_limiter.reset(key)
    login_global_rate_limiter.reset("all-logins")
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)


def _session_snapshot() -> str:
    session = requests.Session()
    session.cookies.set(
        "synthetic-session",
        "opaque-fictional-value",
        domain="pass.imt-atlantique.fr",
        path="/",
        secure=True,
    )
    try:
        return serialize_service_cookies(session)
    finally:
        session.close()


def _gateway_result(
    username: str,
    *,
    operation_id: str = "synthetic-cas",
    profile: PassProfile | None = None,
    profile_fetched: bool | None = None,
    authenticated_username: str | None | object = _DEFAULT_AUTHENTICATED_USERNAME,
) -> GatewayResult:
    authenticated = (
        username
        if authenticated_username is _DEFAULT_AUTHENTICATED_USERNAME
        else authenticated_username
    )
    return GatewayResult(
        operation_id=operation_id,
        entries=[],
        profile=profile,
        competency_ues=None,
        request_count=1,
        session_reused=False,
        full_sso_performed=True,
        profile_fetched=profile is not None if profile_fetched is None else profile_fetched,
        session_snapshot=_session_snapshot(),
        hub_attempted=False,
        hub_succeeded=False,
        authenticated_username=authenticated,
    )


def _create_account(
    username: str,
    *,
    access_generation: int = 1,
    is_disabled: bool = False,
) -> str:
    with SessionLocal() as db:
        account = Account(
            imt_username=username,
            display_name=username.split("@", 1)[0],
            access_generation=access_generation,
            is_disabled=is_disabled,
            disabled_at=utcnow() if is_disabled else None,
            disabled_reason="Synthetic disabled account" if is_disabled else None,
        )
        db.add(account)
        db.commit()
        return account.id


def _count(model: type, account_id: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count(model.id)).where(model.account_id == account_id)) or 0


def _login_event_count(account_id: str) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count(AccountEvent.id)).where(
                AccountEvent.account_id == account_id,
                AccountEvent.kind == "auth:login",
            )
        ) or 0


def _successful_gateway(
    *,
    username: str,
    password: str,
    account_id: str | None,
    raw_client_identity: str,
    initial_import: bool,
    operation_kind: str | None = None,
) -> GatewayResult:
    assert password == "synthetic-password"
    assert raw_client_identity
    assert operation_kind is None
    return _gateway_result(
        username,
        operation_id=f"synthetic-{'new' if initial_import else account_id}",
    )


def _login(client: TestClient, username: str):  # noqa: ANN202
    return client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "synthetic-password"},
    )


def test_disable_committed_during_cas_prevents_every_login_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-race@imt-atlantique.fr"
    account_id = _create_account(username)
    entered_cas = Event()
    release_cas = Event()
    session_issue_attempted = Event()
    event_attempted = Event()

    def blocked_gateway(**kwargs) -> GatewayResult:  # noqa: ANN003
        assert kwargs["account_id"] == account_id
        assert kwargs["initial_import"] is False
        entered_cas.set()
        assert release_cas.wait(timeout=10)
        return _gateway_result(username, operation_id="synthetic-blocked-cas")

    original_create_web_session = imt_login_service.create_web_session
    original_record_event = imt_login_service.record_event

    def tracked_create_web_session(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        session_issue_attempted.set()
        return original_create_web_session(*args, **kwargs)

    def tracked_record_event(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        event_attempted.set()
        return original_record_event(*args, **kwargs)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", blocked_gateway)
    monkeypatch.setattr(imt_login_service, "create_web_session", tracked_create_web_session)
    monkeypatch.setattr(imt_login_service, "record_event", tracked_record_event)

    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        login = executor.submit(_login, client, username)
        assert entered_cas.wait(timeout=10)
        with SessionLocal() as admin_db:
            account = admin_db.scalar(
                select(Account).where(Account.id == account_id).with_for_update()
            )
            assert account is not None
            account.access_generation += 1
            account.is_disabled = True
            account.disabled_at = utcnow()
            account.disabled_reason = "Synthetic concurrent security action"
            admin_db.commit()
        release_cas.set()
        response = login.result(timeout=10)

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert _count(WebSession, account_id) == 0
    assert _count(PassServiceSession, account_id) == 0
    assert _login_event_count(account_id) == 0
    assert not session_issue_attempted.is_set()
    assert not event_attempted.is_set()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.is_disabled, account.access_generation) == (True, 2)
        assert account.last_login_at is None
        assert account.student_status_verified_at is None


def test_post_cas_disabled_flag_is_rechecked_independently_of_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-disabled-flag@imt-atlantique.fr"
    account_id = _create_account(username)
    entered_cas = Event()
    release_cas = Event()

    def blocked_gateway(**_kwargs) -> GatewayResult:
        entered_cas.set()
        assert release_cas.wait(timeout=10)
        return _gateway_result(username)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", blocked_gateway)
    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        login = executor.submit(_login, client, username)
        assert entered_cas.wait(timeout=10)
        with SessionLocal() as admin_db:
            account = admin_db.get(Account, account_id, with_for_update=True)
            assert account is not None
            account.is_disabled = True
            account.disabled_at = utcnow()
            account.disabled_reason = "Synthetic independent disabled flag"
            admin_db.commit()
        release_cas.set()
        response = login.result(timeout=10)

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert _count(WebSession, account_id) == 0
    assert _count(PassServiceSession, account_id) == 0
    assert _login_event_count(account_id) == 0
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.is_disabled is True
        assert account.access_generation == 1
        assert account.last_login_at is None


@pytest.mark.parametrize(
    "authority_change",
    ("disable-reactivate", "delete", "generation-revoke", "login-change"),
)
def test_post_cas_authority_change_rejects_stale_login(
    monkeypatch: pytest.MonkeyPatch,
    authority_change: str,
) -> None:
    username = f"atomic-{authority_change}@imt-atlantique.fr"
    account_id = _create_account(username)
    entered_cas = Event()
    release_cas = Event()

    def blocked_gateway(**_kwargs) -> GatewayResult:
        entered_cas.set()
        assert release_cas.wait(timeout=10)
        return _gateway_result(username)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", blocked_gateway)
    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        login = executor.submit(_login, client, username)
        assert entered_cas.wait(timeout=10)
        try:
            if authority_change == "disable-reactivate":
                with SessionLocal() as admin_db:
                    account = admin_db.get(Account, account_id, with_for_update=True)
                    assert account is not None
                    account.access_generation += 1
                    account.is_disabled = True
                    admin_db.commit()
                with SessionLocal() as admin_db:
                    account = admin_db.get(Account, account_id, with_for_update=True)
                    assert account is not None
                    account.access_generation += 1
                    account.is_disabled = False
                    admin_db.commit()
            elif authority_change == "delete":
                with SessionLocal() as admin_db:
                    account = admin_db.get(Account, account_id, with_for_update=True)
                    assert account is not None
                    admin_db.delete(account)
                    admin_db.commit()
            elif authority_change == "generation-revoke":
                with SessionLocal() as admin_db:
                    account = admin_db.get(Account, account_id, with_for_update=True)
                    assert account is not None
                    account.access_generation += 1
                    admin_db.commit()
            else:
                with SessionLocal() as admin_db:
                    account = admin_db.get(Account, account_id, with_for_update=True)
                    assert account is not None
                    account.imt_username = f"changed-{username}"
                    admin_db.commit()
        finally:
            release_cas.set()
        response = login.result(timeout=10)

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert _count(WebSession, account_id) == 0
    assert _count(PassServiceSession, account_id) == 0
    assert _login_event_count(account_id) == 0
    if authority_change == "delete":
        with SessionLocal() as db:
            assert db.get(Account, account_id) is None
            assert db.scalar(select(func.count(Account.id)).where(Account.imt_username == username)) == 0


def test_disabled_before_start_and_forbidden_signup_never_call_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled_username = "disabled-before-cas@imt-atlantique.fr"
    account_id = _create_account(disabled_username, is_disabled=True)

    def forbidden_gateway(**_kwargs) -> None:
        raise AssertionError("CAS must not run without pre-CAS authority")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", forbidden_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        disabled = _login(client, disabled_username)
    assert disabled.status_code == 401
    assert _count(WebSession, account_id) == 0

    settings = get_settings().model_copy(update={"allow_imt_signup": False})
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, base_url="https://postgres.test") as client:
        forbidden = _login(client, "forbidden-signup@imt-atlantique.fr")
    assert forbidden.status_code == 401
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(Account.id)).where(
                Account.imt_username == "forbidden-signup@imt-atlantique.fr"
            )
        ) == 0


def test_active_generation_is_bound_to_legitimate_imt_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-active@imt-atlantique.fr"
    account_id = _create_account(username, access_generation=7)
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)

    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert response.status_code == 200
    assert response.json()["role"] == "owner"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.last_login_at == account.student_status_verified_at
        assert account.last_login_at is not None
        session = db.scalar(select(WebSession).where(WebSession.account_id == account_id))
        assert session is not None
        assert (session.access_generation, session.role, session.auth_method) == (7, "owner", "imt")
    assert _count(PassServiceSession, account_id) == 1
    assert _login_event_count(account_id) == 1


def test_authenticated_identity_and_profile_metadata_are_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-identity@imt-atlantique.fr"
    account_id = _create_account(username)

    def mismatched_gateway(**_kwargs) -> GatewayResult:
        return _gateway_result(
            username,
            authenticated_username="different-identity@imt-atlantique.fr",
        )

    monkeypatch.setattr("app.routers.auth.perform_login_operation", mismatched_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        mismatch = _login(client, username)
    assert mismatch.status_code == 401
    assert _count(WebSession, account_id) == 0

    def incoherent_gateway(**_kwargs) -> GatewayResult:
        return _gateway_result(username, profile_fetched=True)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", incoherent_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        incoherent = _login(client, username)
    assert incoherent.status_code == 401
    assert _count(WebSession, account_id) == 0
    assert _login_event_count(account_id) == 0


def test_first_login_creates_one_account_and_commits_access_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-first@imt-atlantique.fr"

    def profiled_gateway(**kwargs) -> GatewayResult:  # noqa: ANN003
        return _gateway_result(
            kwargs["username"],
            profile=PassProfile(
                campus="Rennes",
                program="FIP",
                promotion_year=2028,
                first_name="Atomic",
                last_name="Student",
            ),
        )

    monkeypatch.setattr("app.routers.auth.perform_login_operation", profiled_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert response.status_code == 200
    with SessionLocal() as db:
        accounts = list(db.scalars(select(Account).where(Account.imt_username == username)))
        assert len(accounts) == 1
        account = accounts[0]
        assert (
            account.official_first_name,
            account.official_last_name,
            account.program,
            account.promotion_year,
        ) == ("Atomic", "Student", "FIP", 2028)
        account_id = account.id
    assert _count(WebSession, account_id) == 1
    assert _count(PassServiceSession, account_id) == 1
    assert _login_event_count(account_id) == 1


def test_two_concurrent_first_logins_serialize_by_normalized_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-concurrent-first@imt-atlantique.fr"
    gateway_calls = 0
    gateway_lock = Lock()
    both_in_cas = Event()
    release_cas = Event()

    def concurrent_gateway(**kwargs) -> GatewayResult:  # noqa: ANN003
        nonlocal gateway_calls
        with gateway_lock:
            gateway_calls += 1
            call_number = gateway_calls
            if gateway_calls == 2:
                both_in_cas.set()
        assert release_cas.wait(timeout=10)
        return _gateway_result(
            kwargs["username"],
            operation_id=f"synthetic-first-{call_number}",
        )

    monkeypatch.setattr("app.routers.auth.perform_login_operation", concurrent_gateway)
    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        logins = [executor.submit(_login, client, username) for _ in range(2)]
        assert both_in_cas.wait(timeout=10)
        release_cas.set()
        responses = [login.result(timeout=10) for login in logins]

    assert [response.status_code for response in responses] == [200, 200]
    with SessionLocal() as db:
        accounts = list(db.scalars(select(Account).where(Account.imt_username == username)))
        assert len(accounts) == 1
        account_id = accounts[0].id
    assert _count(WebSession, account_id) == 2
    assert _login_event_count(account_id) == 2


def test_first_login_takes_the_postgresql_advisory_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisory_lock_seen = Event()
    original_execute = Session.execute

    def tracked_execute(self, statement, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003,ANN202
        if "pg_advisory_xact_lock" in str(statement):
            advisory_lock_seen.set()
        return original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", tracked_execute)
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, "advisory-first@imt-atlantique.fr")

    assert response.status_code == 200
    assert advisory_lock_seen.is_set()


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    ((ImtAuthenticationError, 401), (ImtNetworkError, 503)),
)
def test_cas_failure_never_reaches_finalization_or_logs_the_password(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[ImtFetchError | ImtAuthenticationError],
    expected_status: int,
) -> None:
    username = f"atomic-cas-failure-{expected_status}@imt-atlantique.fr"
    account_id = _create_account(username)

    def failing_gateway(**_kwargs) -> GatewayResult:
        raise error_type("synthetic upstream failure")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", failing_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert response.status_code == expected_status
    assert _count(WebSession, account_id) == 0
    assert _count(PassServiceSession, account_id) == 0
    assert _login_event_count(account_id) == 0
    assert "synthetic-password" not in caplog.text


def test_session_storage_failure_rolls_back_every_finalization_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-storage-rollback@imt-atlantique.fr"
    account_id = _create_account(username)
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)

    def fail_storage(*_args, **_kwargs) -> None:
        raise PassSessionStorageUnavailable

    monkeypatch.setattr(imt_login_service, "store_service_session_if_reusable", fail_storage)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert _count(WebSession, account_id) == 0
    assert _count(PassServiceSession, account_id) == 0
    assert _login_event_count(account_id) == 0
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.last_login_at is None
        assert account.student_status_verified_at is None


def test_failure_after_session_flush_rolls_back_event_session_and_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-event-rollback@imt-atlantique.fr"
    account_id = _create_account(username)
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)

    def fail_before_commit(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic finalization rollback")

    monkeypatch.setattr(imt_login_service, "record_event", fail_before_commit)
    with TestClient(
        app,
        base_url="https://postgres.test",
        raise_server_exceptions=False,
    ) as client:
        response = _login(client, username)

    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert _count(WebSession, account_id) == 0
    assert _count(PassServiceSession, account_id) == 0
    assert _login_event_count(account_id) == 0
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.last_login_at is None


def test_service_session_and_event_share_the_fresh_final_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-final-transaction@imt-atlantique.fr"
    account_id = _create_account(username)
    store_called = Event()
    event_called = Event()
    original_store = imt_login_service.store_service_session_if_reusable
    original_event = imt_login_service.record_event

    def tracked_store(db: Session, *args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        assert db.in_transaction()
        store_called.set()
        return original_store(db, *args, **kwargs)

    def tracked_event(db: Session, *args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        assert db.in_transaction()
        assert store_called.is_set()
        event_called.set()
        return original_event(db, *args, **kwargs)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)
    monkeypatch.setattr(imt_login_service, "store_service_session_if_reusable", tracked_store)
    monkeypatch.setattr(imt_login_service, "record_event", tracked_event)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert response.status_code == 200
    assert store_called.is_set() and event_called.is_set()
    assert _count(WebSession, account_id) == 1
    assert _count(PassServiceSession, account_id) == 1
    assert _login_event_count(account_id) == 1


def test_session_cookie_is_set_only_after_database_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-cookie-order@imt-atlantique.fr"
    _create_account(username)
    commit_completed = Event()
    cookie_set = Event()
    original_commit = Session.commit
    original_set_session_cookies = auth_router.set_session_cookies

    def tracked_commit(session: Session) -> None:
        original_commit(session)
        commit_completed.set()

    def tracked_set_session_cookies(*args, **kwargs) -> None:  # noqa: ANN002,ANN003
        assert commit_completed.is_set()
        cookie_set.set()
        original_set_session_cookies(*args, **kwargs)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        monkeypatch.setattr(Session, "commit", tracked_commit)
        monkeypatch.setattr(auth_router, "set_session_cookies", tracked_set_session_cookies)
        response = _login(client, username)

    assert response.status_code == 200
    assert commit_completed.is_set() and cookie_set.is_set()
    assert "set-cookie" in response.headers


def test_fresh_locked_read_replaces_an_identity_map_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-populate-existing@imt-atlantique.fr"
    account_id = _create_account(username)
    with SessionLocal() as stale_db:
        stale = stale_db.get(Account, account_id)
        assert stale is not None
        assert stale.access_generation == 1
        with SessionLocal() as concurrent_db:
            concurrent_db.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(access_generation=9)
            )
            concurrent_db.commit()

        refreshed = admin_router._get_account_for_update(stale_db, account_id)
        assert refreshed.access_generation == 9
        stale_db.rollback()


def test_account_row_lock_orders_login_commit_before_concurrent_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-lock-order@imt-atlantique.fr"
    account_id = _create_account(username)
    finalizer_holds_account = Event()
    release_finalizer = Event()
    disable_committed = Event()

    def paused_session_store(*_args, **_kwargs):  # noqa: ANN002,ANN003,ANN202
        finalizer_holds_account.set()
        assert release_finalizer.wait(timeout=10)
        return None

    def disable_account() -> None:
        with SessionLocal() as db:
            account = db.get(Account, account_id, with_for_update=True)
            assert account is not None
            account.access_generation += 1
            account.is_disabled = True
            db.execute(delete(WebSession).where(WebSession.account_id == account_id))
            db.commit()
        disable_committed.set()

    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)
    monkeypatch.setattr(imt_login_service, "store_service_session_if_reusable", paused_session_store)
    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        login = executor.submit(_login, client, username)
        assert finalizer_holds_account.wait(timeout=10)
        disable = executor.submit(disable_account)
        try:
            assert not disable_committed.wait(timeout=0.25)
        finally:
            release_finalizer.set()
        response = login.result(timeout=10)
        disable.result(timeout=10)

    assert response.status_code == 200
    assert disable_committed.is_set()
    assert _count(WebSession, account_id) == 0
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.is_disabled, account.access_generation) == (True, 2)


def test_three_slow_cas_calls_release_a_single_connection_pool_and_identity_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usernames = [f"atomic-pool-{index}@imt-atlantique.fr" for index in range(3)]
    account_ids = [_create_account(username) for username in usernames]
    database_url = os.environ["BOTNOTE_POSTGRES_TEST_URL"]
    small_engine = create_engine(
        database_url,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    SmallPoolSession = sessionmaker(
        bind=small_engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    request_sessions: list[Session] = []
    sessions_lock = Lock()

    def small_pool_db() -> Iterator[Session]:
        with SmallPoolSession() as db:
            with sessions_lock:
                request_sessions.append(db)
            yield db

    entered_count = 0
    entered_lock = Lock()
    all_in_cas = Event()
    release_cas = Event()

    def slow_gateway(**kwargs) -> GatewayResult:  # noqa: ANN003
        nonlocal entered_count
        with entered_lock:
            entered_count += 1
            call_number = entered_count
            if entered_count == len(usernames):
                all_in_cas.set()
        assert release_cas.wait(timeout=10)
        return _gateway_result(
            kwargs["username"],
            operation_id=f"synthetic-pool-{call_number}",
        )

    monkeypatch.setattr("app.routers.auth.perform_login_operation", slow_gateway)
    app.dependency_overrides[get_db] = small_pool_db
    try:
        with (
            TestClient(app, base_url="https://postgres.test") as client,
            ThreadPoolExecutor(max_workers=len(usernames)) as executor,
        ):
            logins = [executor.submit(_login, client, username) for username in usernames]
            try:
                assert all_in_cas.wait(timeout=5)
                assert small_engine.pool.checkedout() == 0
                with sessions_lock:
                    assert len(request_sessions) == 3
                    assert all(not db.in_transaction() for db in request_sessions)
                    assert all(not db.identity_map for db in request_sessions)
            finally:
                release_cas.set()
            responses = [login.result(timeout=10) for login in logins]
    finally:
        app.dependency_overrides.pop(get_db, None)
        small_engine.dispose()

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert [_count(WebSession, account_id) for account_id in account_ids] == [1, 1, 1]
