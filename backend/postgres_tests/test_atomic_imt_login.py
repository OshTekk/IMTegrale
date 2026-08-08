from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from app.database import SessionLocal, get_db, utcnow
from app.main import app
from app.models import Account, WebSession
from app.models import Event as AccountEvent
from app.routers import auth as auth_router
from app.services import imt_login as imt_login_service
from app.services.imt import ImtAuthenticationError, ImtFetchError, ImtNetworkError, PassProfile
from app.services.pass_gateway import GatewayResult
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker


def _gateway_result(
    username: str,
    *,
    operation_id: str = "synthetic-cas",
    profile: PassProfile | None = None,
    profile_fetched: bool | None = None,
) -> GatewayResult:
    return GatewayResult(
        operation_id=operation_id,
        entries=[],
        profile=profile,
        competency_ues=None,
        request_count=1,
        session_reused=False,
        full_sso_performed=True,
        profile_fetched=profile is not None if profile_fetched is None else profile_fetched,
        session_snapshot='{"cookies":[],"version":1}',
        hub_attempted=False,
        hub_succeeded=False,
        authenticated_username=username,
    )


def _create_account(username: str, *, access_generation: int = 1) -> str:
    with SessionLocal() as db:
        account = Account(
            imt_username=username,
            display_name=username.split("@", 1)[0],
            access_generation=access_generation,
        )
        db.add(account)
        db.commit()
        return account.id


def _web_session_count(account_id: str) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count(WebSession.id)).where(WebSession.account_id == account_id)
        ) or 0


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


def test_disable_committed_during_cas_prevents_session_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-race@imt-atlantique.fr"
    entered_cas = Event()
    release_cas = Event()
    session_issue_attempted = Event()
    account_id = _create_account(username)

    def blocked_gateway(
        *,
        username: str,
        password: str,
        account_id: str | None,
        raw_client_identity: str,
        initial_import: bool,
        operation_kind: str | None = None,
    ) -> GatewayResult:
        assert username == "atomic-race@imt-atlantique.fr"
        assert password == "synthetic-password"
        assert account_id is not None
        assert raw_client_identity
        assert initial_import is False
        assert operation_kind is None
        entered_cas.set()
        assert release_cas.wait(timeout=10), "the synthetic CAS barrier was not released"
        return _gateway_result(username, operation_id="synthetic-blocked-cas")

    original_create_web_session = imt_login_service.create_web_session

    def tracked_create_web_session(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        session_issue_attempted.set()
        return original_create_web_session(*args, **kwargs)

    monkeypatch.setattr("app.routers.auth.perform_login_operation", blocked_gateway)
    monkeypatch.setattr(imt_login_service, "create_web_session", tracked_create_web_session)

    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        login = executor.submit(
            client.post,
            "/api/v1/auth/login/imt",
            json={"username": username, "password": "synthetic-password"},
        )
        assert entered_cas.wait(timeout=10), "the login never reached the synthetic CAS barrier"

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

    with SessionLocal() as db:
        session_count = db.scalar(
            select(func.count(WebSession.id)).where(WebSession.account_id == account_id)
        )
        login_event_count = db.scalar(
            select(func.count(AccountEvent.id)).where(
                AccountEvent.account_id == account_id,
                AccountEvent.kind == "auth:login",
            )
        )

    assert (response.status_code, session_count, login_event_count) == (401, 0, 0)
    assert "set-cookie" not in response.headers
    assert not session_issue_attempted.is_set()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.is_disabled, account.access_generation) == (True, 2)
        assert account.last_login_at is None
        assert account.student_status_verified_at is None


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

    def blocked_gateway(**kwargs) -> GatewayResult:  # noqa: ANN003
        entered_cas.set()
        assert release_cas.wait(timeout=10), "the synthetic CAS barrier was not released"
        return _gateway_result(kwargs["username"])

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

    assert (response.status_code, _web_session_count(account_id), _login_event_count(account_id)) == (
        401,
        0,
        0,
    )
    if authority_change == "delete":
        with SessionLocal() as db:
            assert db.get(Account, account_id) is None


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
    assert _login_event_count(account_id) == 1


def test_authenticated_identity_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-identity@imt-atlantique.fr"
    account_id = _create_account(username)

    def mismatched_gateway(**_kwargs) -> GatewayResult:  # noqa: ANN003
        return _gateway_result("different-identity@imt-atlantique.fr")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", mismatched_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert (response.status_code, _web_session_count(account_id), _login_event_count(account_id)) == (
        401,
        0,
        0,
    )


@pytest.mark.parametrize("profile_present", (False, True))
def test_incoherent_cas_profile_metadata_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    profile_present: bool,
) -> None:
    username = f"atomic-profile-{profile_present}@imt-atlantique.fr"
    account_id = _create_account(username)
    profile = (
        PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Fictional",
            last_name="Profile",
        )
        if profile_present
        else None
    )

    def incoherent_gateway(**_kwargs) -> GatewayResult:  # noqa: ANN003
        return _gateway_result(
            username,
            profile=profile,
            profile_fetched=not profile_present,
        )

    monkeypatch.setattr("app.routers.auth.perform_login_operation", incoherent_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert (response.status_code, _web_session_count(account_id), _login_event_count(account_id)) == (
        401,
        0,
        0,
    )


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
            account.imt_username,
            account.official_first_name,
            account.official_last_name,
            account.program,
            account.promotion_year,
        ) == (username, "Atomic", "Student", "FIP", 2028)
        assert db.scalar(
            select(func.count(WebSession.id)).where(WebSession.account_id == account.id)
        ) == 1
        assert db.scalar(
            select(func.count(AccountEvent.id)).where(
                AccountEvent.account_id == account.id,
                AccountEvent.kind == "auth:login",
            )
        ) == 1


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
            if gateway_calls == 2:
                both_in_cas.set()
        assert release_cas.wait(timeout=10), "the concurrent CAS calls were not released"
        return _gateway_result(kwargs["username"], operation_id=f"synthetic-first-{gateway_calls}")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", concurrent_gateway)

    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        logins = [executor.submit(_login, client, username) for _ in range(2)]
        assert both_in_cas.wait(timeout=10), "both first logins must authenticate before finalization"
        release_cas.set()
        responses = [login.result(timeout=10) for login in logins]

    assert [response.status_code for response in responses] == [200, 200]
    with SessionLocal() as db:
        accounts = list(db.scalars(select(Account).where(Account.imt_username == username)))
        assert len(accounts) == 1
        account = accounts[0]
        assert db.scalar(
            select(func.count(WebSession.id)).where(WebSession.account_id == account.id)
        ) == 2
        assert db.scalar(
            select(func.count(AccountEvent.id)).where(
                AccountEvent.account_id == account.id,
                AccountEvent.kind == "auth:login",
            )
        ) == 2


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    ((ImtAuthenticationError, 401), (ImtNetworkError, 503)),
)
def test_cas_failure_never_reaches_finalization(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[ImtFetchError | ImtAuthenticationError],
    expected_status: int,
) -> None:
    username = f"atomic-cas-failure-{expected_status}@imt-atlantique.fr"
    account_id = _create_account(username)

    def failing_gateway(**_kwargs) -> GatewayResult:  # noqa: ANN003
        raise error_type("synthetic upstream failure")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", failing_gateway)
    with TestClient(app, base_url="https://postgres.test") as client:
        response = _login(client, username)

    assert (response.status_code, _web_session_count(account_id), _login_event_count(account_id)) == (
        expected_status,
        0,
        0,
    )


def test_failure_after_session_flush_rolls_back_event_and_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-rollback@imt-atlantique.fr"
    account_id = _create_account(username)
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)

    def fail_before_commit(*_args, **_kwargs) -> None:  # noqa: ANN002,ANN003
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
    assert (_web_session_count(account_id), _login_event_count(account_id)) == (0, 0)
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.last_login_at is None


def test_session_cookie_is_set_only_after_database_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-cookie-order@imt-atlantique.fr"
    _create_account(username)
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)
    commit_completed = Event()
    cookie_set = Event()
    original_commit = Session.commit
    original_set_session_cookies = auth_router.set_session_cookies

    def tracked_commit(session: Session) -> None:
        original_commit(session)
        commit_completed.set()

    def tracked_set_session_cookies(*args, **kwargs) -> None:  # noqa: ANN002,ANN003
        assert commit_completed.is_set(), "cookies must not be set before the final commit"
        cookie_set.set()
        original_set_session_cookies(*args, **kwargs)

    with TestClient(app, base_url="https://postgres.test") as client:
        monkeypatch.setattr(Session, "commit", tracked_commit)
        monkeypatch.setattr(auth_router, "set_session_cookies", tracked_set_session_cookies)
        response = _login(client, username)

    assert response.status_code == 200
    assert commit_completed.is_set() and cookie_set.is_set()
    assert "set-cookie" in response.headers


def test_account_row_lock_orders_login_commit_before_concurrent_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "atomic-lock-order@imt-atlantique.fr"
    account_id = _create_account(username)
    finalizer_holds_account = Event()
    release_finalizer = Event()
    disable_committed = Event()
    monkeypatch.setattr("app.routers.auth.perform_login_operation", _successful_gateway)

    def paused_session_store(*_args, **_kwargs):  # noqa: ANN002,ANN003,ANN202
        finalizer_holds_account.set()
        assert release_finalizer.wait(timeout=10), "the finalizer barrier was not released"
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

    monkeypatch.setattr(
        auth_router,
        "store_service_session_if_reusable",
        paused_session_store,
    )
    with (
        TestClient(app, base_url="https://postgres.test") as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        login = executor.submit(_login, client, username)
        assert finalizer_holds_account.wait(timeout=10)
        disable = executor.submit(disable_account)
        try:
            assert not disable_committed.wait(timeout=0.25), (
                "disable must wait for the login finalizer's account row lock"
            )
        finally:
            release_finalizer.set()
        response = login.result(timeout=10)
        disable.result(timeout=10)

    assert response.status_code == 200
    assert disable_committed.is_set()
    assert _web_session_count(account_id) == 0
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.is_disabled, account.access_generation) == (True, 2)


def test_slow_cas_releases_a_single_connection_pool(
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

    def small_pool_db() -> Iterator[Session]:
        with SmallPoolSession() as db:
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
        assert release_cas.wait(timeout=10), "the slow CAS calls were not released"
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
                assert all_in_cas.wait(timeout=5), (
                    "every slow CAS call must start with only one SQL connection available"
                )
                assert small_engine.pool.checkedout() == 0
            finally:
                release_cas.set()
            responses = [login.result(timeout=10) for login in logins]
    finally:
        app.dependency_overrides.pop(get_db, None)
        small_engine.dispose()

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert [_web_session_count(account_id) for account_id in account_ids] == [1, 1, 1]
