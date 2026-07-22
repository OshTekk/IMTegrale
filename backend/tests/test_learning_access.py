from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
from app.config import Settings, get_settings
from app.database import SessionLocal, get_db, utcnow
from app.learning import access as learning_access
from app.learning.access import (
    LEARNING_AUDIENCE_FIP_2028,
    LEARNING_CATALOG_UNAVAILABLE,
    STUDENT_REVERIFICATION_REQUIRED,
    learning_access_for,
    learning_session_view,
    require_learning_action,
    require_learning_ingress,
)
from app.main import app
from app.models import Account, AdminUser, LearningAccessGrant, WebSession
from app.security import AuthContext, cookie_names, token_digest
from app.services.imt import ImtPassClient, PassEntry, PassProfile
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FakeAudience:
    id: str = LEARNING_AUDIENCE_FIP_2028


@dataclass(frozen=True)
class FakeManifest:
    release_id: str = "fictional-release-001"
    release_mode: str = "published"


@dataclass(frozen=True)
class FakeBundle:
    catalog_version: str = "fictional-release-001"
    audiences: tuple[FakeAudience, ...] = (FakeAudience(),)
    manifest: FakeManifest = FakeManifest()


def _auth_context(
    db: Session,
    *,
    role: str = "owner",
    auth_method: str = "imt",
    share_token_id: str | None = None,
    is_disabled: bool = False,
    program: str = "FIP",
    promotion_year: int | None = 2028,
    academic_source: str = "pass",
    academic_verified: bool = True,
    student_verified_delta: timedelta | None = timedelta(days=-1),
    imt_username: str | None = None,
) -> AuthContext:
    now = utcnow()
    account = Account(
        imt_username=imt_username or f"fictional-{uuid.uuid4()}@example.test",
        display_name="Fictional Student",
        is_disabled=is_disabled,
        program=program,
        promotion_year=promotion_year,
        academic_source=academic_source,
        academic_verified_at=now if academic_verified else None,
        student_status_verified_at=(
            now + student_verified_delta if student_verified_delta is not None else None
        ),
    )
    db.add(account)
    db.flush()
    web_session = WebSession(
        account_id=account.id,
        share_token_id=share_token_id,
        digest=f"digest-{uuid.uuid4()}",
        csrf_digest=f"csrf-{uuid.uuid4()}",
        role=role,
        auth_method=auth_method,
        expires_at=now + timedelta(days=1),
    )
    return AuthContext(account=account, session=web_session)


def _fake_bundle_loader(settings: Settings) -> FakeBundle:
    return FakeBundle(audiences=(FakeAudience(id=settings.learning_audience_id),))


def _fake_personal_bundle_loader(settings: Settings) -> FakeBundle:
    return FakeBundle(
        audiences=(FakeAudience(id=settings.learning_audience_id),),
        manifest=FakeManifest(release_mode="personal_library"),
    )


def _personal_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "learning_access_mode": "personal",
        "learning_audience_id": "personal:fictive-owner",
        "learning_audience_label": "[FICTIF] Espace personnel",
        "learning_level_label": "[FICTIF] Niveau personnel",
        "learning_allowed_imt_usernames": ["fictitious-owner@example.invalid"],
        "learning_allowed_identities": [
            "lan:192.0.2.10",
            "tailnet:fictitious-owner@example.invalid",
        ],
        "trusted_proxy_ips": ["trusted-proxy"],
    }
    values.update(overrides)
    return Settings(**values)


def _request_from(
    peer: str,
    *,
    asserted_identity: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if asserted_identity is not None:
        headers.append((b"x-botnote-client-identity", asserted_identity.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/learning/access",
            "headers": headers,
            "client": (peer, 50000),
        }
    )


def _assert_access_error(
    db: Session,
    auth: AuthContext,
    *,
    status_code: int,
    code: str | None = None,
    now=None,
) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        learning_access_for(
            db,
            auth,
            get_settings(),
            bundle_loader=_fake_bundle_loader,
            now=now,
        )
    assert caught.value.status_code == status_code
    if code is not None:
        assert caught.value.detail["code"] == code
    return caught.value


@pytest.mark.parametrize("auth_method", ["imt", "passkey"])
def test_primary_fip_2028_with_fresh_imt_proof_is_allowed(auth_method: str) -> None:
    with SessionLocal() as db:
        auth = _auth_context(db, auth_method=auth_method)

        access = learning_access_for(
            db,
            auth,
            get_settings(),
            bundle_loader=_fake_bundle_loader,
        )

        assert access.account.id == auth.account.id
        assert access.session is auth.session
        assert access.audience == LEARNING_AUDIENCE_FIP_2028
        assert access.audience_label == "FIP 2028"
        assert access.level_label == "2A"
        assert access.catalog_version == "fictional-release-001"
        assert access.via_manual_grant is False


def test_personal_mode_is_explicit_fail_closed_and_keeps_cohort_as_default() -> None:
    defaults = Settings(environment="test")
    assert defaults.learning_access_mode == "cohort"
    assert defaults.learning_audience_id == LEARNING_AUDIENCE_FIP_2028
    assert defaults.learning_allowed_imt_usernames == []
    assert defaults.learning_allowed_identities == []

    with pytest.raises(ValueError, match="IMT account allowlist"):
        Settings(
            environment="test",
            learning_access_mode="personal",
            learning_allowed_identities=["lan:192.0.2.10"],
        )
    with pytest.raises(ValueError, match="private ingress allowlist"):
        Settings(
            environment="test",
            learning_access_mode="personal",
            learning_allowed_imt_usernames=["fictitious-owner@example.invalid"],
        )
    with pytest.raises(ValueError, match="LAN or Tailnet"):
        Settings(
            environment="test",
            learning_access_mode="personal",
            learning_allowed_imt_usernames=["fictitious-owner@example.invalid"],
            learning_allowed_identities=["internet:198.51.100.5"],
        )
    with pytest.raises(ValueError, match="distinct personal:<id> audience"):
        Settings(
            environment="test",
            learning_access_mode="personal",
            learning_allowed_imt_usernames=["fictitious-owner@example.invalid"],
            learning_allowed_identities=["lan:192.0.2.10"],
        )
    with pytest.raises(ValueError, match="distinct personal:<id> audience"):
        Settings(
            environment="test",
            learning_access_mode="personal",
            learning_audience_id="personal::invalid",
            learning_allowed_imt_usernames=["fictitious-owner@example.invalid"],
            learning_allowed_identities=["lan:192.0.2.10"],
        )
    with pytest.raises(ValueError, match="exactly one IMT account"):
        Settings(
            environment="test",
            learning_access_mode="personal",
            learning_audience_id="personal:fictive-owner",
            learning_allowed_imt_usernames=[
                "fictitious-owner@example.invalid",
                "fictitious-other@example.invalid",
            ],
            learning_allowed_identities=["lan:192.0.2.10"],
        )
    with pytest.raises(ValueError, match="requires the fip:2028 audience"):
        Settings(
            environment="test",
            learning_access_mode="cohort",
            learning_audience_id="personal:fictive-owner",
        )


def test_personal_mode_reads_and_normalizes_private_environment_lists(monkeypatch) -> None:
    monkeypatch.setenv("BOTNOTE_LEARNING_ACCESS_MODE", "personal")
    monkeypatch.setenv("BOTNOTE_LEARNING_AUDIENCE_ID", "personal:owner")
    monkeypatch.setenv(
        "BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES",
        '["FICTITIOUS-OWNER@EXAMPLE.INVALID"]',
    )
    monkeypatch.setenv(
        "BOTNOTE_LEARNING_ALLOWED_IDENTITIES",
        '["LAN:192.0.2.10","TAILNET:FICTITIOUS-OWNER@EXAMPLE.INVALID"]',
    )

    settings = Settings(_env_file=None)

    assert settings.learning_audience_id == "personal:owner"
    assert settings.learning_allowed_imt_usernames == ["fictitious-owner@example.invalid"]
    assert settings.learning_allowed_identities == [
        "lan:192.0.2.10",
        "tailnet:fictitious-owner@example.invalid",
    ]


def test_personal_mode_uses_exact_account_allowlist_and_configured_audience() -> None:
    settings = _personal_settings()
    with SessionLocal() as db:
        allowed = _auth_context(
            db,
            imt_username="FICTITIOUS-OWNER@EXAMPLE.INVALID",
        )
        denied = _auth_context(
            db,
            imt_username="fictitious-other@example.invalid",
        )

        access = learning_access_for(
            db,
            allowed,
            settings,
            bundle_loader=_fake_bundle_loader,
        )
        assert access.audience == "personal:fictive-owner"
        assert access.audience_label == "[FICTIF] Espace personnel"
        assert access.level_label == "[FICTIF] Niveau personnel"

        with pytest.raises(HTTPException) as hidden:
            learning_access_for(
                db,
                denied,
                settings,
                bundle_loader=lambda _settings: pytest.fail(
                    "a denied personal account must not load the bundle"
                ),
            )
        assert hidden.value.status_code == 404
        assert hidden.value.detail == "Ressource introuvable"


@pytest.mark.parametrize("auth_method", ["imt", "passkey"])
def test_personal_library_accepts_exact_primary_owner_with_fresh_academic_evidence(
    auth_method: str,
) -> None:
    settings = _personal_settings()
    with SessionLocal() as db:
        auth = _auth_context(
            db,
            auth_method=auth_method,
            imt_username="fictitious-owner@example.invalid",
        )

        access = learning_access_for(
            db,
            auth,
            settings,
            bundle_loader=_fake_personal_bundle_loader,
        )

        assert access.audience == "personal:fictive-owner"
        assert access.via_manual_grant is False


def test_personal_library_does_not_relax_existing_academic_evidence() -> None:
    settings = _personal_settings()
    with SessionLocal() as db:
        auth = _auth_context(
            db,
            imt_username="fictitious-owner@example.invalid",
            program="unknown",
            promotion_year=None,
            academic_source="unknown",
            academic_verified=False,
            student_verified_delta=None,
        )

        with pytest.raises(HTTPException) as denied:
            learning_access_for(
                db,
                auth,
                settings,
                bundle_loader=lambda _settings: pytest.fail(
                    "academic denial must happen before loading the personal bundle"
                ),
            )

        assert denied.value.status_code == 404
        assert denied.value.detail == "Ressource introuvable"


def test_personal_library_is_rejected_by_cohort_or_mismatched_runtime() -> None:
    cohort_settings = Settings(environment="test")
    personal_settings = _personal_settings()
    with SessionLocal() as db:
        cohort_auth = _auth_context(db)
        with pytest.raises(HTTPException) as cohort_denied:
            learning_access_for(
                db,
                cohort_auth,
                cohort_settings,
                bundle_loader=_fake_personal_bundle_loader,
            )
        assert cohort_denied.value.status_code == 503
        assert cohort_denied.value.detail["code"] == LEARNING_CATALOG_UNAVAILABLE

        personal_auth = _auth_context(
            db,
            imt_username="fictitious-owner@example.invalid",
        )

        def mismatched_bundle(_settings: Settings) -> FakeBundle:
            return FakeBundle(
                audiences=(
                    FakeAudience(id="personal:fictive-owner"),
                    FakeAudience(id="personal:fictive-other"),
                ),
                manifest=FakeManifest(release_mode="personal_library"),
            )

        with pytest.raises(HTTPException) as mismatch:
            learning_access_for(
                db,
                personal_auth,
                personal_settings,
                bundle_loader=mismatched_bundle,
            )
        assert mismatch.value.status_code == 503
        assert mismatch.value.detail["code"] == LEARNING_CATALOG_UNAVAILABLE


def test_personal_ingress_accepts_exact_lan_and_tailnet_and_ignores_spoofing() -> None:
    settings = _personal_settings()

    assert (
        require_learning_ingress(
            _request_from("trusted-proxy", asserted_identity="lan:192.0.2.10"),
            settings,
        )
        == "lan:192.0.2.10"
    )
    assert (
        require_learning_ingress(
            _request_from(
                "trusted-proxy",
                asserted_identity="tailnet:fictitious-owner@example.invalid",
            ),
            settings,
        )
        == "tailnet:fictitious-owner@example.invalid"
    )

    for request in (
        _request_from("trusted-proxy", asserted_identity="internet:198.51.100.5"),
        _request_from("untrusted-peer", asserted_identity="lan:192.0.2.10"),
        _request_from("trusted-proxy", asserted_identity="lan:192.0.2.11"),
    ):
        with pytest.raises(HTTPException) as hidden:
            require_learning_ingress(request, settings)
        assert hidden.value.status_code == 404
        assert hidden.value.detail == "Route introuvable"


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        ({"role": "viewer"}, 404),
        ({"auth_method": "token", "share_token_id": "fictional-owner-token"}, 404),
        ({"share_token_id": "fictional-owner-token"}, 404),
        ({"auth_method": "other"}, 404),
        ({"is_disabled": True}, 404),
        ({"program": "FIT"}, 404),
        ({"promotion_year": 2027}, 404),
        ({"academic_source": "unknown"}, 404),
        ({"academic_verified": False}, 404),
    ],
)
def test_noneligible_sessions_are_hidden(overrides: dict, expected_status: int) -> None:
    with SessionLocal() as db:
        auth = _auth_context(db, **overrides)
        error = _assert_access_error(db, auth, status_code=expected_status)

        assert error.detail == "Ressource introuvable"


@pytest.mark.parametrize("student_verified_delta", [None, timedelta(days=-31)])
def test_automatic_access_requires_recent_student_verification(
    student_verified_delta: timedelta | None,
) -> None:
    with SessionLocal() as db:
        auth = _auth_context(db, student_verified_delta=student_verified_delta)

        error = _assert_access_error(
            db,
            auth,
            status_code=403,
            code=STUDENT_REVERIFICATION_REQUIRED,
        )

        assert set(error.detail) == {"code", "message"}
        assert "IMT" in error.detail["message"]


def test_student_verification_expires_at_the_exact_configured_boundary() -> None:
    settings = get_settings()
    now = utcnow()
    with SessionLocal() as db:
        auth = _auth_context(db)
        auth.account.student_status_verified_at = now - timedelta(
            days=settings.learning_student_status_max_age_days
        )

        _assert_access_error(
            db,
            auth,
            status_code=403,
            code=STUDENT_REVERIFICATION_REQUIRED,
            now=now,
        )


def _add_grant(
    db: Session,
    auth: AuthContext,
    *,
    expires_delta: timedelta,
    revoked: bool = False,
    audience: str = LEARNING_AUDIENCE_FIP_2028,
) -> LearningAccessGrant:
    now = utcnow()
    admin = AdminUser(
        username=f"fictional-admin-{uuid.uuid4()}",
        password_hash="not-a-real-password-hash",
        must_change_password=False,
    )
    db.add(admin)
    db.flush()
    grant = LearningAccessGrant(
        account_id=auth.account.id,
        audience=audience,
        reason="Fictional time-limited test grant",
        granted_by_admin_id=admin.id,
        granted_at=now - timedelta(hours=1),
        expires_at=now + expires_delta,
        revoked_at=now if revoked else None,
    )
    db.add(grant)
    db.flush()
    return grant


def test_active_manual_grant_is_an_explicit_academic_and_freshness_alternative() -> None:
    with SessionLocal() as db:
        auth = _auth_context(
            db,
            program="unknown",
            promotion_year=None,
            academic_source="unknown",
            academic_verified=False,
            student_verified_delta=None,
        )
        grant = _add_grant(db, auth, expires_delta=timedelta(days=2))

        access = learning_access_for(
            db,
            auth,
            get_settings(),
            bundle_loader=_fake_bundle_loader,
        )

        assert access.manual_grant_id == grant.id
        assert access.via_manual_grant is True


@pytest.mark.parametrize(
    ("expires_delta", "revoked"),
    [(timedelta(seconds=-1), False), (timedelta(days=1), True)],
)
def test_expired_or_revoked_manual_grant_is_refused(
    expires_delta: timedelta,
    revoked: bool,
) -> None:
    with SessionLocal() as db:
        auth = _auth_context(
            db,
            program="unknown",
            promotion_year=None,
            academic_source="unknown",
            academic_verified=False,
            student_verified_delta=None,
        )
        _add_grant(db, auth, expires_delta=expires_delta, revoked=revoked)

        _assert_access_error(db, auth, status_code=404)


def test_manual_grant_never_allows_shared_token_or_disabled_account() -> None:
    with SessionLocal() as db:
        token_auth = _auth_context(
            db,
            auth_method="token",
            share_token_id="fictional-owner-token",
            program="unknown",
        )
        disabled_auth = _auth_context(db, is_disabled=True, program="unknown")
        _add_grant(db, token_auth, expires_delta=timedelta(days=1))
        _add_grant(db, disabled_auth, expires_delta=timedelta(days=1))

        _assert_access_error(db, token_auth, status_code=404)
        _assert_access_error(db, disabled_auth, status_code=404)


def test_personal_allowlist_cannot_be_bypassed_by_grant_or_shared_token() -> None:
    settings = _personal_settings()
    with SessionLocal() as db:
        other_account = _auth_context(
            db,
            imt_username="fictitious-other@example.invalid",
            program="unknown",
        )
        allowed_token = _auth_context(
            db,
            imt_username="fictitious-owner@example.invalid",
            auth_method="token",
            share_token_id="fictional-owner-token",
            program="unknown",
        )
        for auth in (other_account, allowed_token):
            _add_grant(
                db,
                auth,
                expires_delta=timedelta(days=1),
                audience=settings.learning_audience_id,
            )
            with pytest.raises(HTTPException) as hidden:
                learning_access_for(
                    db,
                    auth,
                    settings,
                    bundle_loader=_fake_bundle_loader,
                )
            assert hidden.value.status_code == 404


def test_bundle_is_loaded_only_after_entitlement_and_errors_are_sanitized() -> None:
    calls = 0

    def unavailable_loader(_settings: Settings) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("FICTITIOUS_LEARNING_PATH_CANARY")

    with SessionLocal() as db:
        denied = _auth_context(db, program="FIT")
        eligible = _auth_context(db)

        with pytest.raises(HTTPException) as hidden:
            learning_access_for(db, denied, bundle_loader=unavailable_loader)
        assert hidden.value.status_code == 404
        assert calls == 0

        with pytest.raises(HTTPException) as unavailable:
            learning_access_for(db, eligible, bundle_loader=unavailable_loader)
        assert unavailable.value.status_code == 503
        assert unavailable.value.detail == {
            "code": LEARNING_CATALOG_UNAVAILABLE,
            "message": "Le catalogue Parcours est temporairement indisponible.",
        }
        assert unavailable.value.__cause__ is None
        assert "FICTITIOUS_LEARNING_PATH_CANARY" not in str(unavailable.value.detail)
        assert calls == 1


def test_session_learning_view_is_exact_and_never_raises(monkeypatch) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        eligible = _auth_context(db)
        stale = _auth_context(db, student_verified_delta=timedelta(days=-31))
        denied = _auth_context(db, program="FIT")

        monkeypatch.setattr(learning_access, "_default_bundle_loader", _fake_bundle_loader)
        assert learning_session_view(db, eligible, settings) == {
            "available": True,
            "audience_label": "FIP 2028",
            "level_label": "2A",
            "reverify_required": False,
            "catalog_version": "fictional-release-001",
        }
        assert learning_session_view(db, stale, settings) == {
            "available": False,
            "audience_label": "FIP 2028",
            "level_label": "2A",
            "reverify_required": True,
            "catalog_version": None,
        }
        assert learning_session_view(db, denied, settings) == {
            "available": False,
            "audience_label": None,
            "level_label": None,
            "reverify_required": False,
            "catalog_version": None,
        }

        def broken_loader(_settings: Settings) -> object:
            raise RuntimeError("FICTITIOUS_SESSION_PATH_CANARY")

        monkeypatch.setattr(learning_access, "_default_bundle_loader", broken_loader)
        assert learning_session_view(db, eligible, settings) == {
            "available": False,
            "audience_label": "FIP 2028",
            "level_label": "2A",
            "reverify_required": False,
            "catalog_version": None,
        }


def test_auth_session_exposes_only_the_learning_ux_contract(client, monkeypatch) -> None:
    def fictional_login(
        pass_client: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        pass_client.last_profile = PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Fictional",
            last_name="STUDENT",
        )
        return []

    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fictional_login)
    events: list[str] = []

    def tracked_db():  # noqa: ANN202
        db = SessionLocal()
        events.append("db-open")
        original_close = db.close
        close_recorded = False

        def tracked_close() -> None:
            nonlocal close_recorded
            original_close()
            if not close_recorded:
                events.append("db-closed")
                close_recorded = True

        db.close = tracked_close  # type: ignore[method-assign]
        try:
            yield db
        finally:
            db.close()

    def worker_thread_bundle_loader(_settings: Settings) -> FakeBundle:
        events.append("bundle-loaded")
        assert "db-closed" in events
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return FakeBundle()
        raise AssertionError("Le bundle ne doit pas être chargé sur l'event loop")

    monkeypatch.setattr(
        learning_access,
        "_default_bundle_loader",
        worker_thread_bundle_loader,
    )

    app.dependency_overrides[get_db] = tracked_db
    try:
        login = client.post(
            "/api/v1/auth/login/imt",
            json={
                "username": "learning-session-contract@imt-atlantique.fr",
                "password": "fictional-password",
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    expected = {
        "available": True,
        "audience_label": "FIP 2028",
        "level_label": "2A",
        "reverify_required": False,
        "catalog_version": "fictional-release-001",
    }
    assert login.status_code == 200, login.text
    assert login.json()["learning"] == expected
    assert events == ["db-open", "db-closed", "bundle-loaded"]
    assert client.get("/api/v1/auth/session").json()["learning"] == expected

    with SessionLocal() as db:
        account = db.get(Account, login.json()["account"]["id"])
        assert account is not None
        account.student_status_verified_at = utcnow() - timedelta(days=31)
        db.commit()

    assert client.get("/api/v1/auth/session").json()["learning"] == {
        "available": False,
        "audience_label": "FIP 2028",
        "level_label": "2A",
        "reverify_required": True,
        "catalog_version": None,
    }


def test_learning_action_checks_csrf_before_loading_the_bundle(monkeypatch) -> None:
    settings = get_settings()
    calls = 0

    def counted_loader(_settings: Settings) -> FakeBundle:
        nonlocal calls
        calls += 1
        return FakeBundle()

    monkeypatch.setattr(learning_access, "_default_bundle_loader", counted_loader)
    with SessionLocal() as db:
        auth = _auth_context(db)
        missing_csrf = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/learning/progress",
                "headers": [],
            }
        )

        with pytest.raises(HTTPException) as denied:
            require_learning_action(missing_csrf, auth, db, settings)

        assert denied.value.status_code == 403
        assert denied.value.detail == "Jeton CSRF invalide"
        assert calls == 0

        raw_csrf = "fictional-learning-csrf"
        auth.session.csrf_digest = token_digest(raw_csrf, settings)
        _session_cookie, csrf_cookie = cookie_names(settings)
        valid_request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/learning/progress",
                "headers": [
                    (b"origin", settings.public_origin.encode()),
                    (b"x-csrf-token", raw_csrf.encode()),
                    (b"cookie", f"{csrf_cookie}={raw_csrf}".encode()),
                ],
            }
        )

        context = require_learning_action(valid_request, auth, db, settings)

        assert context.audience == LEARNING_AUDIENCE_FIP_2028
        assert calls == 1
