from __future__ import annotations

import asyncio
import inspect
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
from app.config import get_settings
from app.database import SessionLocal, get_db, utcnow
from app.learning import access as learning_access_module
from app.learning.access import LEARNING_AUDIENCE_FIP_2028
from app.learning.bundle import (
    LearningBundleSnapshot,
    LearningCatalogUnavailable,
    reset_learning_bundle_cache,
)
from app.main import app
from app.models import (
    Account,
    AdminUser,
    LearningAccessGrant,
    LearningAttempt,
    LearningProgress,
    Note,
    ShareToken,
    UeSetting,
)
from app.routers import learning as learning_router
from app.security import cookie_names, create_web_session
from app.services import leaderboard as leaderboard_service
from app.services.leaderboard import account_leaderboard_score
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import csrf_headers
from tests.learning_bundle_factory import (
    SOURCE_BYTES,
    write_fictitious_learning_bundle,
    write_fictitious_metadata_only_preview_bundle,
)

_PRIVATE_HEADERS = {
    "cache-control": "private, no-store",
    "x-robots-tag": "noindex, nofollow, noarchive",
    "vary": "Cookie",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
_CANARY_PATH = "/srv/fictitious-private/releases/do-not-disclose/source-fiction.bin"
_EXERCISE_ID = "exercise-content-fiction"
_EXERCISE_HINT_ID = "exercise-hint-fiction-one"
_PERSONAL_AUDIENCE_ID = "personal:fictive-owner"


def assert_api_error(response, message: str) -> None:  # noqa: ANN001
    stable_codes = {
        "Authentification requise": "AUTHENTICATION_REQUIRED",
        "Compte désactivé": "ACCOUNT_DISABLED",
        "Jeton CSRF invalide": "CSRF_INVALID",
        "Origine refusée": "ORIGIN_FORBIDDEN",
        "Route introuvable": "RESOURCE_NOT_FOUND",
        "Ressource introuvable": "RESOURCE_NOT_FOUND",
    }
    assert response.json() == {
        "detail": {
            "code": stable_codes.get(message, f"HTTP_{response.status_code}"),
            "message": message,
        }
    }


@dataclass(frozen=True, slots=True)
class InstalledIdentity:
    account_id: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class RouteCase:
    method: Literal["GET", "POST", "PUT", "DELETE"]
    path: str
    payload: dict[str, Any] | None = None


PROTECTED_ROUTE_CASES = (
    RouteCase("GET", "/api/v1/learning/access"),
    RouteCase("GET", "/api/v1/learning/catalog"),
    RouteCase("GET", "/api/v1/learning/catalog/lesson-fiction"),
    RouteCase("GET", "/api/v1/learning/content/content-fiction"),
    RouteCase("GET", "/api/v1/learning/assets/asset-source-fiction"),
    RouteCase("GET", "/api/v1/learning/assets/asset-source-fiction/download"),
    RouteCase("GET", "/api/v1/learning/sources/source-fiction"),
    RouteCase(
        "GET",
        "/api/v1/learning/references/content-fiction/reference-source-fiction",
    ),
    RouteCase(
        "POST",
        "/api/v1/learning/search",
        {"query": "équation fictive", "filters": {}, "limit": 5},
    ),
    RouteCase("GET", "/api/v1/learning/progress"),
    RouteCase("GET", "/api/v1/learning/progress/content-fiction"),
    RouteCase("PUT", "/api/v1/learning/progress/content-fiction", {}),
    RouteCase("DELETE", "/api/v1/learning/progress"),
    RouteCase("GET", "/api/v1/learning/attempts"),
    RouteCase(
        "POST",
        "/api/v1/learning/attempts",
        {"exercise_id": _EXERCISE_ID, "attempt_kind": "viewed"},
    ),
)

ENTITLEMENT_PROTECTED_ROUTE_CASES = tuple(
    case
    for case in PROTECTED_ROUTE_CASES
    if not (case.method == "DELETE" and case.path == "/api/v1/learning/progress")
)


@pytest.fixture
def fictitious_content_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    release = write_fictitious_learning_bundle(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "learning_content_root", release)
    reset_learning_bundle_cache()
    yield release
    reset_learning_bundle_cache()


@pytest.fixture
def fictitious_personal_content_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    release = write_fictitious_learning_bundle(
        tmp_path,
        audience_id=_PERSONAL_AUDIENCE_ID,
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "learning_content_root", release)
    reset_learning_bundle_cache()
    yield release
    reset_learning_bundle_cache()


@pytest.fixture
def fictitious_personal_preview_content_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    release = write_fictitious_metadata_only_preview_bundle(
        tmp_path,
        audience_id=_PERSONAL_AUDIENCE_ID,
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "learning_content_root", release)
    reset_learning_bundle_cache()
    yield release
    reset_learning_bundle_cache()


def _configure_personal_mode(
    monkeypatch: pytest.MonkeyPatch,
    *,
    allowed_username: str = "fictitious-personal-owner@example.invalid",
    allowed_identities: list[str] | None = None,
    trusted_proxy_ips: list[str] | None = None,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "learning_allowed_imt_usernames",
        [allowed_username.casefold()],
    )
    monkeypatch.setattr(
        settings,
        "learning_allowed_identities",
        allowed_identities
        or [
            "lan:192.0.2.10",
            "tailnet:fictitious-personal-owner@example.invalid",
        ],
    )
    monkeypatch.setattr(
        settings,
        "trusted_proxy_ips",
        trusted_proxy_ips or ["testclient"],
    )
    monkeypatch.setattr(settings, "learning_audience_id", _PERSONAL_AUDIENCE_ID)
    monkeypatch.setattr(settings, "learning_audience_label", "[FICTIF] Espace personnel")
    monkeypatch.setattr(settings, "learning_level_label", "[FICTIF] Niveau personnel")
    monkeypatch.setattr(settings, "learning_access_mode", "personal")


def _assert_private_headers(response) -> None:  # noqa: ANN001
    for name, expected in _PRIVATE_HEADERS.items():
        assert response.headers.get(name) == expected
    content_security_policy = response.headers.get("content-security-policy", "")
    assert "object-src blob:" in content_security_policy
    assert "object-src 'self'" not in content_security_policy
    assert "img-src 'self' data: blob:" in content_security_policy


def _install_identity(
    client: TestClient,
    *,
    role: str = "owner",
    auth_method: str = "imt",
    shared: bool = False,
    is_disabled: bool = False,
    program: str = "FIP",
    promotion_year: int | None = 2028,
    academic_source: str = "pass",
    academic_verified: bool = True,
    student_verified_delta: timedelta | None = timedelta(days=-1),
    imt_username: str | None = None,
) -> InstalledIdentity:
    settings = get_settings()
    now = utcnow()
    unique = uuid.uuid4().hex
    with SessionLocal() as db:
        account = Account(
            imt_username=imt_username or f"fictitious-learning-{unique}@example.invalid",
            display_name="Étudiant entièrement fictif",
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

        share_token_id: str | None = None
        if shared:
            share = ShareToken(
                account_id=account.id,
                name="Token fictif de test",
                prefix=unique[:10],
                digest=unique.ljust(64, "0")[:64],
                role=role,
                expires_at=now + timedelta(days=1),
            )
            db.add(share)
            db.flush()
            share_token_id = share.id

        _web_session, raw_session, raw_csrf = create_web_session(
            db,
            account=account,
            role=role,
            auth_method=auth_method,
            share_token_id=share_token_id,
            user_agent="fictitious-learning-api-test",
            settings=settings,
        )
        db.commit()
        account_id = account.id

    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    return InstalledIdentity(account_id=account_id, csrf_token=raw_csrf)


def _request(client: TestClient, case: RouteCase):  # noqa: ANN202
    headers = None
    if case.method in {"POST", "PUT", "DELETE"}:
        headers = {name: value for name, value in csrf_headers(client).items() if value is not None}
    return client.request(case.method, case.path, json=case.payload, headers=headers)


def _add_manual_grant(
    account_id: str,
    *,
    state: Literal["active", "expired", "revoked"] = "active",
) -> str:
    now = utcnow()
    unique = uuid.uuid4().hex
    with SessionLocal() as db:
        admin = AdminUser(
            username=f"fictitious-learning-admin-{unique}",
            password_hash="not-a-real-password-hash",
            must_change_password=False,
        )
        db.add(admin)
        db.flush()
        grant = LearningAccessGrant(
            account_id=account_id,
            audience=LEARNING_AUDIENCE_FIP_2028,
            reason="Autorisation temporaire entièrement fictive",
            granted_by_admin_id=admin.id,
            granted_at=now - timedelta(days=2),
            expires_at=(now - timedelta(days=1) if state == "expired" else now + timedelta(days=1)),
            revoked_at=now - timedelta(hours=1) if state == "revoked" else None,
        )
        db.add(grant)
        db.commit()
        return grant.id


def _seed_learning_state(account_id: str, *, audiences: tuple[str, ...]) -> None:
    with SessionLocal() as db:
        for index, audience in enumerate(audiences):
            db.add(
                LearningProgress(
                    account_id=account_id,
                    audience=audience,
                    content_id=f"fictitious-content-{index}",
                    last_section_id=None,
                    last_page=None,
                    completed=False,
                    exercise_viewed=True,
                    opened_hint_ids=[],
                    self_assessment=None,
                    favorite=False,
                )
            )
            db.add(
                LearningAttempt(
                    account_id=account_id,
                    audience=audience,
                    exercise_id=f"fictitious-exercise-{index}",
                    attempt_kind="viewed",
                    hint_id=None,
                    self_assessment=None,
                )
            )
        db.commit()


@pytest.mark.parametrize("case", PROTECTED_ROUTE_CASES, ids=lambda case: f"{case.method}-{case.path}")
def test_every_learning_route_refuses_anonymous_requests(
    client: TestClient,
    fictitious_content_root: Path,
    case: RouteCase,
) -> None:
    response = _request(client, case)

    assert response.status_code == 401
    _assert_private_headers(response)
    assert _CANARY_PATH not in response.text


@pytest.mark.parametrize(
    "identity",
    ["lan:192.0.2.10", "tailnet:fictitious-personal-owner@example.invalid"],
)
def test_personal_owner_is_allowed_from_exact_lan_and_tailnet_ingress(
    client: TestClient,
    fictitious_personal_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    _configure_personal_mode(monkeypatch)
    client.headers["X-BotNote-Client-Identity"] = identity
    _install_identity(
        client,
        imt_username="fictitious-personal-owner@example.invalid",
    )

    access = client.get("/api/v1/learning/access")
    asset = client.get("/api/v1/learning/assets/asset-source-fiction")
    search = client.post(
        "/api/v1/learning/search",
        json={"query": "équation fictive", "filters": {}, "limit": 5},
        headers=csrf_headers(client),
    )

    assert access.status_code == asset.status_code == search.status_code == 200
    assert access.json()["audience"] == _PERSONAL_AUDIENCE_ID
    assert asset.content == SOURCE_BYTES
    for response in (access, asset, search):
        _assert_private_headers(response)


def test_personal_preview_exposes_citations_but_never_source_assets(
    client: TestClient,
    fictitious_personal_preview_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personal_mode(monkeypatch)
    client.headers["X-BotNote-Client-Identity"] = "lan:192.0.2.10"
    _install_identity(
        client,
        imt_username="fictitious-personal-owner@example.invalid",
    )

    catalog = client.get("/api/v1/learning/catalog")
    content = client.get("/api/v1/learning/content/content-fiction")
    source = client.get("/api/v1/learning/sources/source-fiction")
    reference = client.get("/api/v1/learning/references/content-fiction/reference-source-fiction")
    search = client.post(
        "/api/v1/learning/search",
        json={
            "query": "source page",
            "filters": {"entity_types": ["source"]},
            "limit": 5,
        },
        headers=csrf_headers(client),
    )
    inline = client.get("/api/v1/learning/assets/asset-source-fiction")
    download = client.get("/api/v1/learning/assets/asset-source-fiction/download")

    assert (
        catalog.status_code
        == content.status_code
        == source.status_code
        == reference.status_code
        == search.status_code
        == 200
    )
    assert {node["review_status"] for node in catalog.json()["nodes"]} == {"private_preview"}
    assert content.json()["frontmatter"]["review_status"] == "private_preview"
    source_payload = source.json()
    assert source_payload["asset_id"] is None
    assert source_payload["asset_url"] is None
    assert source_payload["source_serving_allowed"] is False
    assert source_payload["kind"] is None
    assert source_payload["mime_type"] is None
    assert source_payload["filename"] is None
    assert source_payload["page_count"] == 1
    assert source_payload["rights_label"] == "Document source non diffusé"
    assert "None" not in source_payload["rights_label"]
    assert source_payload["pages"] == [{"page": 1, "label": "[FICTIF] Page unique"}]
    assert reference.json()["asset_url"] is None
    assert reference.json()["source_serving_allowed"] is False
    assert [item["entity_id"] for item in search.json()["items"]] == ["source-fiction"]
    for response in (inline, download):
        assert response.status_code == 404
        assert_api_error(response, "Ressource introuvable")
        assert str(fictitious_personal_preview_content_root) not in response.text
    for response in (catalog, content, source, reference, search, inline, download):
        _assert_private_headers(response)


@pytest.mark.parametrize("case", PROTECTED_ROUTE_CASES, ids=lambda case: f"{case.method}-{case.path}")
def test_personal_mode_hides_every_route_from_other_primary_accounts(
    client: TestClient,
    fictitious_personal_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: RouteCase,
) -> None:
    _configure_personal_mode(monkeypatch)
    client.headers["X-BotNote-Client-Identity"] = "lan:192.0.2.10"
    _install_identity(
        client,
        imt_username="fictitious-other-student@example.invalid",
    )

    response = _request(client, case)

    assert response.status_code == 404
    assert_api_error(response, "Ressource introuvable")
    _assert_private_headers(response)


@pytest.mark.parametrize("case", PROTECTED_ROUTE_CASES, ids=lambda case: f"{case.method}-{case.path}")
def test_personal_mode_hides_every_route_from_internet_even_for_allowed_account(
    client: TestClient,
    fictitious_personal_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: RouteCase,
) -> None:
    _configure_personal_mode(monkeypatch)
    client.headers["X-BotNote-Client-Identity"] = "internet:198.51.100.5"
    _install_identity(
        client,
        imt_username="fictitious-personal-owner@example.invalid",
    )

    response = _request(client, case)

    assert response.status_code == 404
    assert_api_error(response, "Route introuvable")
    _assert_private_headers(response)


def test_personal_ingress_cannot_be_spoofed_and_covers_shell_and_early_errors(
    client: TestClient,
    fictitious_personal_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personal_mode(
        monkeypatch,
        trusted_proxy_ips=["some-other-trusted-proxy"],
    )
    client.headers["X-BotNote-Client-Identity"] = "lan:192.0.2.10"

    direct = client.get("/api/v1/learning/access")
    deep_link = client.get("/parcours/lecons/fictive-content")
    malformed = client.post(
        "/api/v1/learning/search",
        content=b'{"query":',
        headers={"Content-Type": "application/json"},
    )
    oversized = client.post(
        "/api/v1/learning/search",
        content=b"x" * (get_settings().max_request_bytes + 1),
        headers={"Content-Type": "application/json"},
    )

    for response in (direct, deep_link, malformed, oversized):
        assert response.status_code == 404
        assert_api_error(response, "Route introuvable")
        _assert_private_headers(response)


def test_personal_session_ux_is_hidden_outside_private_ingress(
    client: TestClient,
    fictitious_personal_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personal_mode(monkeypatch)
    client.headers["X-BotNote-Client-Identity"] = "lan:192.0.2.10"
    _install_identity(
        client,
        imt_username="fictitious-personal-owner@example.invalid",
    )

    assert client.get("/api/v1/auth/session").json()["learning"]["available"] is True

    client.headers["X-BotNote-Client-Identity"] = "internet:198.51.100.5"
    assert client.get("/api/v1/auth/session").json()["learning"] == {
        "available": False,
        "audience_label": None,
        "level_label": None,
        "reverify_required": False,
        "catalog_version": None,
    }


def test_personal_mode_still_refuses_owner_share_tokens(
    client: TestClient,
    fictitious_personal_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personal_mode(monkeypatch)
    client.headers["X-BotNote-Client-Identity"] = "lan:192.0.2.10"
    _install_identity(
        client,
        imt_username="fictitious-personal-owner@example.invalid",
        role="owner",
        auth_method="token",
        shared=True,
    )

    response = client.get("/api/v1/learning/assets/asset-source-fiction")

    assert response.status_code == 404
    assert_api_error(response, "Ressource introuvable")
    _assert_private_headers(response)


@pytest.mark.parametrize("principal", ["viewer", "owner-token"])
@pytest.mark.parametrize(
    "case",
    ENTITLEMENT_PROTECTED_ROUTE_CASES,
    ids=lambda case: f"{case.method}-{case.path}",
)
def test_every_learning_route_hides_itself_from_delegated_sessions(
    client: TestClient,
    fictitious_content_root: Path,
    case: RouteCase,
    principal: str,
) -> None:
    if principal == "viewer":
        _install_identity(client, role="viewer")
    else:
        _install_identity(client, role="owner", auth_method="token", shared=True)

    response = _request(client, case)

    assert response.status_code == 404
    _assert_private_headers(response)
    assert_api_error(response, "Ressource introuvable")


@pytest.mark.parametrize("principal", ["viewer", "owner-token"])
def test_progress_erasure_refuses_non_primary_owner_sessions(
    client: TestClient,
    principal: str,
) -> None:
    identity = (
        _install_identity(client, role="viewer")
        if principal == "viewer"
        else _install_identity(client, role="owner", auth_method="token", shared=True)
    )
    _seed_learning_state(identity.account_id, audiences=("fip:2028",))

    response = client.delete(
        "/api/v1/learning/progress",
        headers=csrf_headers(client),
    )

    assert response.status_code == 403
    _assert_private_headers(response)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningProgress)
                .where(LearningProgress.account_id == identity.account_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningAttempt)
                .where(LearningAttempt.account_id == identity.account_id)
            )
            == 1
        )


def test_progress_erasure_requires_origin_and_csrf(client: TestClient) -> None:
    identity = _install_identity(client)
    _seed_learning_state(identity.account_id, audiences=("fip:2028",))

    invalid_origin = client.delete(
        "/api/v1/learning/progress",
        headers={
            "Origin": "https://invalid.example",
            "X-CSRF-Token": identity.csrf_token,
        },
    )
    missing_csrf = client.delete(
        "/api/v1/learning/progress",
        headers={"Origin": "https://testserver"},
    )

    assert invalid_origin.status_code == 403
    assert_api_error(invalid_origin, "Origine refusée")
    assert missing_csrf.status_code == 403
    assert_api_error(missing_csrf, "Jeton CSRF invalide")
    for response in (invalid_origin, missing_csrf):
        _assert_private_headers(response)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningProgress)
                .where(LearningProgress.account_id == identity.account_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningAttempt)
                .where(LearningAttempt.account_id == identity.account_id)
            )
            == 1
        )


@pytest.mark.parametrize(
    "access_state",
    ["stale", "noneligible", "invalid-catalog"],
)
def test_primary_owner_can_erase_all_learning_state_without_current_entitlement(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    access_state: str,
) -> None:
    identity_options: dict[str, Any] = {}
    if access_state == "stale":
        identity_options = {
            "auth_method": "passkey",
            "student_verified_delta": timedelta(days=-31),
        }
    elif access_state == "noneligible":
        identity_options = {"program": "FIT"}
    else:
        invalid_root = tmp_path / "invalid-learning-catalog"
        invalid_root.mkdir()
        (invalid_root / "manifest.json").write_text(
            '{"schema_version":',
            encoding="utf-8",
        )
        monkeypatch.setattr(get_settings(), "learning_content_root", invalid_root)

    def bundle_must_not_be_loaded(_settings):  # noqa: ANN001, ANN202
        raise AssertionError("progress erasure must not load the learning bundle")

    monkeypatch.setattr(
        learning_access_module,
        "_default_bundle_loader",
        bundle_must_not_be_loaded,
    )
    identity = _install_identity(client, **identity_options)
    other_client = TestClient(client.app, base_url="https://testserver")
    try:
        other = _install_identity(other_client)
        _seed_learning_state(
            identity.account_id,
            audiences=("fip:2028", "future-curriculum:2031"),
        )
        _seed_learning_state(other.account_id, audiences=("fip:2028",))
        with SessionLocal() as db:
            account_before = db.get(Account, identity.account_id)
            assert account_before is not None
            verified_at_before = account_before.student_status_verified_at

        response = client.delete(
            "/api/v1/learning/progress",
            headers=csrf_headers(client),
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"deleted": {"progress": 2, "attempts": 2}}
        _assert_private_headers(response)
        with SessionLocal() as db:
            account = db.get(Account, identity.account_id)
            assert account is not None
            assert account.student_status_verified_at == verified_at_before
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningProgress)
                    .where(LearningProgress.account_id == identity.account_id)
                )
                == 0
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningAttempt)
                    .where(LearningAttempt.account_id == identity.account_id)
                )
                == 0
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningProgress)
                    .where(LearningProgress.account_id == other.account_id)
                )
                == 1
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningAttempt)
                    .where(LearningAttempt.account_id == other.account_id)
                )
                == 1
            )
    finally:
        other_client.close()


@pytest.mark.parametrize(
    "account_overrides",
    [
        {"promotion_year": 2027},
        {"program": "FIT"},
        {"academic_source": "unknown"},
        {"academic_verified": False},
    ],
    ids=["other-promotion", "other-program", "unverified-source", "no-academic-proof"],
)
def test_noneligible_accounts_cannot_discover_the_catalog(
    client: TestClient,
    fictitious_content_root: Path,
    account_overrides: dict[str, Any],
) -> None:
    _install_identity(client, **account_overrides)

    response = client.get("/api/v1/learning/access")

    assert response.status_code == 404
    assert_api_error(response, "Ressource introuvable")
    _assert_private_headers(response)


def test_disabled_account_is_refused_before_learning_access(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client, is_disabled=True)

    response = client.get("/api/v1/learning/access")

    assert response.status_code == 403
    assert_api_error(response, "Compte désactivé")
    _assert_private_headers(response)


@pytest.mark.parametrize("auth_method", ["imt", "passkey"])
def test_fresh_primary_fip_2028_sessions_are_allowed(
    client: TestClient,
    fictitious_content_root: Path,
    auth_method: str,
) -> None:
    _install_identity(client, auth_method=auth_method)

    response = client.get("/api/v1/learning/access")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "available": True,
        "audience": "fip:2028",
        "audience_label": "FIP 2028",
        "level_label": "2A",
        "reverify_required": False,
        "catalog_version": "fictitious-release-a",
        "release_id": "fictitious-release-a",
    }
    _assert_private_headers(response)


def test_stale_passkey_session_receives_only_the_stable_reverification_error(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(
        client,
        auth_method="passkey",
        student_verified_delta=timedelta(days=-31),
    )

    response = client.get("/api/v1/learning/catalog")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "STUDENT_REVERIFICATION_REQUIRED"
    assert set(response.json()["detail"]) == {"code", "message"}
    assert "fictitious-release" not in response.text
    _assert_private_headers(response)


def test_active_manual_grant_allows_a_primary_nonacademic_account(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    identity = _install_identity(
        client,
        program="unknown",
        promotion_year=None,
        academic_source="unknown",
        academic_verified=False,
        student_verified_delta=None,
    )
    grant_id = _add_manual_grant(identity.account_id)

    response = client.get("/api/v1/learning/access")

    assert response.status_code == 200, response.text
    assert grant_id not in response.text
    assert response.json()["audience"] == LEARNING_AUDIENCE_FIP_2028
    _assert_private_headers(response)


@pytest.mark.parametrize("grant_state", ["expired", "revoked"])
def test_expired_or_revoked_manual_grant_is_hidden(
    client: TestClient,
    fictitious_content_root: Path,
    grant_state: Literal["expired", "revoked"],
) -> None:
    identity = _install_identity(
        client,
        program="unknown",
        promotion_year=None,
        academic_source="unknown",
        academic_verified=False,
        student_verified_delta=None,
    )
    grant_id = _add_manual_grant(identity.account_id, state=grant_state)

    response = client.get("/api/v1/learning/access")

    assert response.status_code == 404
    assert grant_id not in response.text
    assert_api_error(response, "Ressource introuvable")
    _assert_private_headers(response)


def test_manual_grant_never_upgrades_an_owner_share_token(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    identity = _install_identity(
        client,
        role="owner",
        auth_method="token",
        shared=True,
        program="unknown",
        academic_verified=False,
        student_verified_delta=None,
    )
    _add_manual_grant(identity.account_id)

    response = client.get("/api/v1/learning/access")

    assert response.status_code == 404
    assert_api_error(response, "Ressource introuvable")
    _assert_private_headers(response)


def test_eligible_owner_can_use_the_complete_learning_api_surface(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    identity = _install_identity(client)
    with SessionLocal() as db:
        account_before = db.get(Account, identity.account_id)
        assert account_before is not None
        verified_at_before = account_before.student_status_verified_at

    access = client.get("/api/v1/learning/access")
    catalog = client.get("/api/v1/learning/catalog")
    catalog_node = client.get("/api/v1/learning/catalog/lesson-fiction")
    content = client.get("/api/v1/learning/content/content-fiction")
    source = client.get("/api/v1/learning/sources/source-fiction")
    reference = client.get("/api/v1/learning/references/content-fiction/reference-source-fiction")
    inline_asset = client.get("/api/v1/learning/assets/asset-source-fiction")
    downloaded_asset = client.get("/api/v1/learning/assets/asset-source-fiction/download")
    search = client.post(
        "/api/v1/learning/search",
        json={"query": "équation", "filters": {}, "limit": 5},
        headers=csrf_headers(client),
    )
    initial_progress = client.get("/api/v1/learning/progress")
    updated_progress = client.put(
        "/api/v1/learning/progress/content-fiction",
        json={"last_section_id": "section-demonstration", "favorite": True},
        headers=csrf_headers(client),
    )
    progress_item = client.get("/api/v1/learning/progress/content-fiction")
    attempt = client.post(
        "/api/v1/learning/attempts",
        json={
            "exercise_id": _EXERCISE_ID,
            "attempt_kind": "hint_opened",
            "hint_id": _EXERCISE_HINT_ID,
        },
        headers=csrf_headers(client),
    )
    attempts = client.get(f"/api/v1/learning/attempts?exercise_id={_EXERCISE_ID}")

    responses = (
        access,
        catalog,
        catalog_node,
        content,
        source,
        reference,
        inline_asset,
        downloaded_asset,
        search,
        initial_progress,
        updated_progress,
        progress_item,
        attempt,
        attempts,
    )
    assert [response.status_code for response in responses] == [
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        201,
        200,
    ]
    for response in responses:
        _assert_private_headers(response)

    assert catalog_node.json()["node"]["id"] == "lesson-fiction"
    assert content.json()["id"] == "content-fiction"
    assert source.json()["asset_url"] == ("/api/v1/learning/assets/asset-source-fiction")
    assert reference.json() == {
        "release_id": "fictitious-release-a",
        "id": "reference-source-fiction",
        "content_id": "content-fiction",
        "source_id": "source-fiction",
        "source_title": "[FICTIF] Source de démonstration",
        "page": 1,
        "end_page": None,
        "label": "page fictive",
        "source_url": "/api/v1/learning/sources/source-fiction",
        "source_serving_allowed": True,
        "asset_url": "/api/v1/learning/assets/asset-source-fiction",
    }
    assert inline_asset.content == SOURCE_BYTES
    assert downloaded_asset.content == SOURCE_BYTES
    assert search.json()["items"][0]["entity_id"] == "content-fiction"
    assert initial_progress.json()["items"] == []
    assert progress_item.json()["favorite"] is True
    assert attempt.json()["attempt_kind"] == "hint_opened"
    assert attempt.json()["hint_id"] == _EXERCISE_HINT_ID
    assert len(attempts.json()["items"]) == 1
    with SessionLocal() as db:
        account_after = db.get(Account, identity.account_id)
        assert account_after is not None
        assert account_after.student_status_verified_at == verified_at_before

    reset = client.delete("/api/v1/learning/progress", headers=csrf_headers(client))
    assert reset.status_code == 200
    assert reset.json() == {"deleted": {"progress": 2, "attempts": 1}}
    _assert_private_headers(reset)


def test_search_and_attempts_return_canonical_resource_ids(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    identity = _install_identity(client)

    source_search = client.post(
        "/api/v1/learning/search",
        json={
            "query": "source page",
            "filters": {"entity_types": ["source"]},
            "limit": 1,
        },
        headers=csrf_headers(client),
    )
    attempt = client.post(
        "/api/v1/learning/attempts",
        json={"exercise_id": "exercise-fiction", "attempt_kind": "viewed"},
        headers=csrf_headers(client),
    )

    assert source_search.status_code == 200, source_search.text
    assert len(source_search.json()["items"]) == 1
    source_result = source_search.json()["items"][0]
    assert source_result["entity_id"] == "source-fiction"
    assert source_result["catalog_node_id"] == "source-node-fiction"
    assert source_result["entity_type"] == "source"
    assert attempt.status_code == 201, attempt.text
    assert attempt.json()["exercise_id"] == _EXERCISE_ID
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningAttempt)
                .where(
                    LearningAttempt.account_id == identity.account_id,
                    LearningAttempt.exercise_id == _EXERCISE_ID,
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningProgress)
                .where(
                    LearningProgress.account_id == identity.account_id,
                    LearningProgress.content_id == _EXERCISE_ID,
                )
            )
            == 1
        )


def test_learning_search_is_rate_limited_per_account(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _install_identity(client)
    limiter = learning_router.learning_search_rate_limiter
    limiter.reset(identity.account_id)
    monkeypatch.setattr(limiter, "limit", 2)
    monkeypatch.setattr(limiter, "window_seconds", 60)

    responses = [
        client.post(
            "/api/v1/learning/search",
            json={"query": "fictif", "filters": {}, "limit": 1},
            headers=csrf_headers(client),
        )
        for _ in range(3)
    ]

    assert [response.status_code for response in responses] == [200, 200, 429]
    assert responses[-1].headers["retry-after"]
    for response in responses:
        _assert_private_headers(response)
    limiter.reset(identity.account_id)


def test_saturated_search_returns_only_catalog_unavailable(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_identity(client)

    def saturated_search(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise LearningCatalogUnavailable()

    monkeypatch.setattr(LearningBundleSnapshot, "search", saturated_search)

    response = client.post(
        "/api/v1/learning/search",
        json={"query": "fictif", "filters": {}, "limit": 1},
        headers=csrf_headers(client),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "LEARNING_CATALOG_UNAVAILABLE",
            "message": "Le catalogue Parcours est temporairement indisponible.",
        }
    }
    assert _CANARY_PATH not in response.text
    _assert_private_headers(response)


def test_assets_use_only_the_manifest_filename_for_inline_and_download(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def filename_with_characters(manifest: dict[str, Any]) -> None:
        manifest["assets"][0]["filename"] = 'DÉMO FICTIVE "nom" (v1).pdf'

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=filename_with_characters,
    )
    monkeypatch.setattr(get_settings(), "learning_content_root", release)
    reset_learning_bundle_cache()
    _install_identity(client)

    inline = client.get(
        "/api/v1/learning/assets/asset-source-fiction?filename=injected.pdf&path=../../manifest.json"
    )
    download = client.get("/api/v1/learning/assets/asset-source-fiction/download?filename=injected.pdf")

    assert inline.status_code == download.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")
    assert download.headers["content-disposition"].startswith("attachment;")
    for response in (inline, download):
        disposition = response.headers["content-disposition"]
        assert "injected.pdf" not in disposition
        assert "manifest.json" not in disposition
        assert "filename*=UTF-8''D%C3%89MO%20FICTIVE%20nom%20%28v1%29.pdf" in disposition
        assert "\r" not in disposition and "\n" not in disposition
        assert response.headers["content-type"] == "application/pdf"
        assert response.content == SOURCE_BYTES
        _assert_private_headers(response)
    reset_learning_bundle_cache()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/learning/assets/asset-source-fiction",
        "/api/v1/learning/assets/asset-source-fiction/download",
    ],
)
def test_asset_db_dependency_closes_before_stream_consumption(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    _install_identity(client)
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

    original_loader = learning_access_module._default_bundle_loader

    def load_after_db_cleanup(settings):  # noqa: ANN001, ANN202
        events.append("bundle-loaded")
        assert "db-closed" in events
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return original_loader(settings)

    original_open_asset = LearningBundleSnapshot.open_asset

    def open_after_db_cleanup(self, asset_id, audience_id):  # noqa: ANN001, ANN202
        events.append("asset-opened")
        assert "db-closed" in events
        return original_open_asset(self, asset_id, audience_id)

    original_stream_file = learning_router._stream_file

    def stream_after_db_cleanup(stream: object):  # noqa: ANN202
        events.append("stream-consumed")
        assert "db-closed" in events
        yield from original_stream_file(stream)

    app.dependency_overrides[get_db] = tracked_db
    monkeypatch.setattr(learning_access_module, "_default_bundle_loader", load_after_db_cleanup)
    monkeypatch.setattr(LearningBundleSnapshot, "open_asset", open_after_db_cleanup)
    monkeypatch.setattr(learning_router, "_stream_file", stream_after_db_cleanup)
    try:
        response = client.get(path)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.content == SOURCE_BYTES
    assert events == [
        "db-open",
        "db-closed",
        "bundle-loaded",
        "asset-opened",
        "stream-consumed",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/learning/assets/asset-source-fiction",
        "/api/v1/learning/assets/asset-source-fiction/download",
    ],
)
def test_asset_bundle_failure_is_generic_after_db_cleanup(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    _install_identity(client)
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

    def unavailable_after_db_cleanup(_settings):  # noqa: ANN001, ANN202
        events.append("bundle-loaded")
        assert "db-closed" in events
        raise RuntimeError(_CANARY_PATH)

    app.dependency_overrides[get_db] = tracked_db
    monkeypatch.setattr(
        learning_access_module,
        "_default_bundle_loader",
        unavailable_after_db_cleanup,
    )
    try:
        response = client.get(path)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "LEARNING_CATALOG_UNAVAILABLE",
            "message": "Le catalogue Parcours est temporairement indisponible.",
        }
    }
    assert _CANARY_PATH not in response.text
    assert events == ["db-open", "db-closed", "bundle-loaded"]
    _assert_private_headers(response)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/v1/learning/access", None),
        ("GET", "/api/v1/auth/session", None),
        (
            "POST",
            "/api/v1/learning/search",
            {"query": "fictif", "filters": {}, "limit": 1},
        ),
    ],
)
def test_learning_dependencies_release_db_before_bundle_loading(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    _install_identity(client)
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

    original_loader = learning_access_module._default_bundle_loader

    def load_after_db_cleanup(settings):  # noqa: ANN001, ANN202
        events.append("bundle-loaded")
        assert "db-closed" in events
        return original_loader(settings)

    app.dependency_overrides[get_db] = tracked_db
    monkeypatch.setattr(learning_access_module, "_default_bundle_loader", load_after_db_cleanup)
    try:
        response = client.request(
            method,
            path,
            json=payload,
            headers=csrf_headers(client) if method == "POST" else None,
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    assert events == ["db-open", "db-closed", "bundle-loaded"]


def test_progress_reuses_session_only_after_bundle_loading(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_identity(client)
    events: list[str] = []

    def tracked_db():  # noqa: ANN202
        db = SessionLocal()
        events.append("db-open")
        original_close = db.close
        original_scalars = db.scalars
        close_recorded = False

        def tracked_close() -> None:
            nonlocal close_recorded
            original_close()
            if not close_recorded:
                events.append("db-closed")
                close_recorded = True

        def tracked_scalars(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            events.append("db-reused")
            assert "db-closed" in events
            assert "bundle-loaded" in events
            return original_scalars(*args, **kwargs)

        db.close = tracked_close  # type: ignore[method-assign]
        db.scalars = tracked_scalars  # type: ignore[method-assign]
        try:
            yield db
        finally:
            db.close()

    original_loader = learning_access_module._default_bundle_loader

    def load_after_db_cleanup(settings):  # noqa: ANN001, ANN202
        events.append("bundle-loaded")
        assert "db-closed" in events
        return original_loader(settings)

    app.dependency_overrides[get_db] = tracked_db
    monkeypatch.setattr(learning_access_module, "_default_bundle_loader", load_after_db_cleanup)
    try:
        response = client.get("/api/v1/learning/progress")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert events == ["db-open", "db-closed", "bundle-loaded", "db-reused"]


def test_learning_validation_handler_releases_db_before_bundle_loading(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_identity(client)
    events: list[str] = []

    def tracked_session_local():  # noqa: ANN202
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
        return db

    original_loader = learning_access_module._default_bundle_loader

    def load_after_db_cleanup(settings):  # noqa: ANN001, ANN202
        events.append("bundle-loaded")
        assert "db-closed" in events
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return original_loader(settings)

    monkeypatch.setattr(sys.modules["app.main"], "SessionLocal", tracked_session_local)
    monkeypatch.setattr(learning_access_module, "_default_bundle_loader", load_after_db_cleanup)

    response = client.post(
        "/api/v1/learning/search",
        content=b'{"query":',
        headers={**csrf_headers(client), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert events == ["db-open", "db-closed", "bundle-loaded"]
    _assert_private_headers(response)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/learning/catalog/unknown-fiction",
        "/api/v1/learning/content/unknown-fiction",
        "/api/v1/learning/sources/unknown-fiction",
        "/api/v1/learning/references/content-fiction/unknown-reference",
        "/api/v1/learning/references/unknown-content/reference-source-fiction",
        "/api/v1/learning/assets/unknown-fiction",
        "/api/v1/learning/assets/..%5C..%5Cmanifest.json",
    ],
)
def test_unknown_ids_and_encoded_traversal_are_generic_not_found(
    client: TestClient,
    fictitious_content_root: Path,
    path: str,
) -> None:
    _install_identity(client)

    response = client.get(path)

    assert response.status_code == 404
    assert_api_error(response, "Ressource introuvable")
    assert "manifest.json" not in response.text
    assert str(fictitious_content_root) not in response.text
    _assert_private_headers(response)


def test_anonymous_malformed_search_body_does_not_echo_input(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    response = client.post(
        "/api/v1/learning/search",
        content=b'{"query":"' + _CANARY_PATH.encode(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 401
    assert_api_error(response, "Authentification requise")
    assert _CANARY_PATH not in response.text
    _assert_private_headers(response)


def test_authenticated_validation_errors_do_not_echo_private_input(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client)

    response = client.post(
        "/api/v1/learning/search",
        json={"query": "fictif", "unexpected": _CANARY_PATH},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422
    assert _CANARY_PATH not in response.text
    assert '"input"' not in response.text
    _assert_private_headers(response)


def test_client_cannot_select_or_assert_a_learning_audience(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client)

    response = client.post(
        "/api/v1/learning/search",
        json={
            "query": "fictif",
            "audience": "fip:2028",
            "program": "FIP",
            "promotion_year": 2028,
        },
        headers=csrf_headers(client),
    )

    assert response.status_code == 422
    assert '"input"' not in response.text
    _assert_private_headers(response)


@pytest.mark.parametrize("case", [case for case in PROTECTED_ROUTE_CASES if case.method != "GET"])
def test_learning_mutations_and_search_require_csrf(
    client: TestClient,
    fictitious_content_root: Path,
    case: RouteCase,
) -> None:
    identity = _install_identity(client)

    response = client.request(case.method, case.path, json=case.payload)

    assert response.status_code == 403
    assert_api_error(response, "Jeton CSRF invalide")
    _assert_private_headers(response)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningProgress)
                .where(LearningProgress.account_id == identity.account_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningAttempt)
                .where(LearningAttempt.account_id == identity.account_id)
            )
            == 0
        )


@pytest.mark.parametrize("security_case", ["missing", "invalid-origin", "invalid-csrf"])
@pytest.mark.parametrize("principal", ["viewer", "owner-token", "noneligible"])
def test_hidden_principals_are_refused_before_action_security_errors(
    client: TestClient,
    fictitious_content_root: Path,
    principal: str,
    security_case: str,
) -> None:
    if principal == "viewer":
        identity = _install_identity(client, role="viewer")
    elif principal == "owner-token":
        identity = _install_identity(client, auth_method="token", shared=True)
    else:
        identity = _install_identity(client, program="FIT")

    headers: dict[str, str] = {}
    if security_case == "invalid-origin":
        headers = {
            "Origin": "https://invalid.example",
            "X-CSRF-Token": identity.csrf_token,
        }
    elif security_case == "invalid-csrf":
        headers = {
            "Origin": "https://testserver",
            "X-CSRF-Token": "invalid-fictional-csrf",
        }

    response = client.post(
        "/api/v1/learning/search",
        json={"query": "fictif", "filters": {}, "limit": 1},
        headers=headers,
    )

    assert response.status_code == 404
    assert_api_error(response, "Ressource introuvable")
    _assert_private_headers(response)


@pytest.mark.parametrize("security_case", ["missing", "invalid-origin", "invalid-csrf"])
def test_eligible_principal_receives_action_security_errors(
    client: TestClient,
    fictitious_content_root: Path,
    security_case: str,
) -> None:
    identity = _install_identity(client)
    headers: dict[str, str] = {}
    expected_detail = "Jeton CSRF invalide"
    if security_case == "invalid-origin":
        headers = {
            "Origin": "https://invalid.example",
            "X-CSRF-Token": identity.csrf_token,
        }
        expected_detail = "Origine refusée"
    elif security_case == "invalid-csrf":
        headers = {
            "Origin": "https://testserver",
            "X-CSRF-Token": "invalid-fictional-csrf",
        }

    response = client.post(
        "/api/v1/learning/search",
        json={"query": "fictif", "filters": {}, "limit": 1},
        headers=headers,
    )

    assert response.status_code == 403
    assert_api_error(response, expected_detail)
    _assert_private_headers(response)


@pytest.mark.parametrize("security_case", ["missing", "invalid-origin", "invalid-csrf"])
def test_stale_principal_receives_reverification_before_action_security_errors(
    client: TestClient,
    fictitious_content_root: Path,
    security_case: str,
) -> None:
    identity = _install_identity(
        client,
        auth_method="passkey",
        student_verified_delta=timedelta(days=-31),
    )
    headers: dict[str, str] = {}
    if security_case == "invalid-origin":
        headers = {
            "Origin": "https://invalid.example",
            "X-CSRF-Token": identity.csrf_token,
        }
    elif security_case == "invalid-csrf":
        headers = {
            "Origin": "https://testserver",
            "X-CSRF-Token": "invalid-fictional-csrf",
        }

    response = client.post(
        "/api/v1/learning/search",
        json={"query": "fictif", "filters": {}, "limit": 1},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "STUDENT_REVERIFICATION_REQUIRED"
    _assert_private_headers(response)


def test_progress_accepts_only_resolved_sections_hints_and_source_pages(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client)

    content_progress = client.put(
        "/api/v1/learning/progress/content-fiction",
        json={
            "last_section_id": "section-demonstration",
            "completed": True,
            "favorite": True,
        },
        headers=csrf_headers(client),
    )
    exercise_progress = client.put(
        f"/api/v1/learning/progress/{_EXERCISE_ID}",
        json={
            "last_section_id": "exercise-section-fiction",
            "opened_hint_ids": [_EXERCISE_HINT_ID, _EXERCISE_HINT_ID],
            "self_assessment": 4,
        },
        headers=csrf_headers(client),
    )
    source_progress = client.put(
        "/api/v1/learning/progress/source-fiction",
        json={"last_page": 1},
        headers=csrf_headers(client),
    )

    assert content_progress.status_code == 200, content_progress.text
    assert content_progress.json()["last_section_id"] == "section-demonstration"
    assert exercise_progress.status_code == 200, exercise_progress.text
    assert exercise_progress.json()["opened_hint_ids"] == [_EXERCISE_HINT_ID]
    assert exercise_progress.json()["last_section_id"] == "exercise-section-fiction"
    assert source_progress.status_code == 200, source_progress.text
    assert source_progress.json()["last_page"] == 1

    invalid_payloads = (
        ("content-fiction", {"last_section_id": "unknown-section"}),
        (_EXERCISE_ID, {"opened_hint_ids": ["unknown-hint"]}),
        ("source-fiction", {"last_page": 2}),
    )
    for content_id, payload in invalid_payloads:
        response = client.put(
            f"/api/v1/learning/progress/{content_id}",
            json=payload,
            headers=csrf_headers(client),
        )
        assert response.status_code == 422
        assert _CANARY_PATH not in response.text
        _assert_private_headers(response)


def test_progress_and_attempts_are_isolated_and_reset_only_for_the_caller(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    first = _install_identity(client)
    second_client = TestClient(client.app, base_url="https://testserver")
    try:
        second = _install_identity(second_client, auth_method="passkey")

        first_progress = client.put(
            "/api/v1/learning/progress/content-fiction",
            json={"completed": True, "favorite": True},
            headers=csrf_headers(client),
        )
        first_attempt = client.post(
            "/api/v1/learning/attempts",
            json={"exercise_id": _EXERCISE_ID, "attempt_kind": "completed"},
            headers=csrf_headers(client),
        )
        second_progress = second_client.put(
            "/api/v1/learning/progress/content-fiction",
            json={"self_assessment": 2},
            headers=csrf_headers(second_client),
        )
        second_attempt = second_client.post(
            "/api/v1/learning/attempts",
            json={
                "exercise_id": _EXERCISE_ID,
                "attempt_kind": "self_assessed",
                "self_assessment": 2,
            },
            headers=csrf_headers(second_client),
        )
        assert {
            first_progress.status_code,
            second_progress.status_code,
        } == {200}
        assert {first_attempt.status_code, second_attempt.status_code} == {201}

        first_list = client.get("/api/v1/learning/progress").json()
        second_list = second_client.get("/api/v1/learning/progress").json()
        assert first_list["summary"] == {
            "started_count": 2,
            "completed_lessons": 2,
            "viewed_exercises": 1,
            "favorite_count": 1,
        }
        assert second_list["summary"] == {
            "started_count": 2,
            "completed_lessons": 0,
            "viewed_exercises": 1,
            "favorite_count": 0,
        }
        assert client.get("/api/v1/learning/attempts").json()["items"][0]["self_assessment"] is None
        assert second_client.get("/api/v1/learning/attempts").json()["items"][0]["self_assessment"] == 2

        reset = client.delete(
            "/api/v1/learning/progress",
            headers=csrf_headers(client),
        )
        assert reset.status_code == 200
        assert client.get("/api/v1/learning/progress").json()["items"] == []
        assert client.get("/api/v1/learning/attempts").json()["items"] == []
        assert len(second_client.get("/api/v1/learning/progress").json()["items"]) == 2
        assert len(second_client.get("/api/v1/learning/attempts").json()["items"]) == 1

        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningProgress)
                    .where(LearningProgress.account_id == first.account_id)
                )
                == 0
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningAttempt)
                    .where(LearningAttempt.account_id == first.account_id)
                )
                == 0
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningProgress)
                    .where(LearningProgress.account_id == second.account_id)
                )
                == 2
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningAttempt)
                    .where(LearningAttempt.account_id == second.account_id)
                )
                == 1
            )
    finally:
        second_client.close()


def test_progress_limit_is_enforced_per_account(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_identity(client)
    monkeypatch.setattr(learning_router, "MAX_LEARNING_PROGRESS_ITEMS_PER_ACCOUNT", 1)

    first = client.put(
        "/api/v1/learning/progress/content-fiction",
        json={},
        headers=csrf_headers(client),
    )
    limited = client.put(
        "/api/v1/learning/progress/source-fiction",
        json={},
        headers=csrf_headers(client),
    )

    assert first.status_code == 200
    assert limited.status_code == 409
    assert_api_error(limited, "Limite de progression atteinte")
    _assert_private_headers(limited)


def test_attempt_limit_and_event_payload_constraints_are_enforced(
    client: TestClient,
    fictitious_content_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_identity(client)
    monkeypatch.setattr(learning_router, "MAX_LEARNING_ATTEMPTS_PER_ACCOUNT", 1)

    invalid_payloads = (
        {"exercise_id": _EXERCISE_ID, "attempt_kind": "hint_opened"},
        {
            "exercise_id": _EXERCISE_ID,
            "attempt_kind": "viewed",
            "hint_id": _EXERCISE_HINT_ID,
        },
        {
            "exercise_id": _EXERCISE_ID,
            "attempt_kind": "self_assessed",
            "self_assessment": 0,
        },
        {
            "exercise_id": _EXERCISE_ID,
            "attempt_kind": "viewed",
            "free_answer": "Aucune réponse libre longue ne doit être stockée en v1.",
        },
    )
    for payload in invalid_payloads:
        response = client.post(
            "/api/v1/learning/attempts",
            json=payload,
            headers=csrf_headers(client),
        )
        assert response.status_code == 422
        _assert_private_headers(response)

    first = client.post(
        "/api/v1/learning/attempts",
        json={"exercise_id": _EXERCISE_ID, "attempt_kind": "viewed"},
        headers=csrf_headers(client),
    )
    limited = client.post(
        "/api/v1/learning/attempts",
        json={"exercise_id": _EXERCISE_ID, "attempt_kind": "completed"},
        headers=csrf_headers(client),
    )

    assert first.status_code == 201
    assert limited.status_code == 409
    assert_api_error(limited, "Limite de tentatives atteinte")
    _assert_private_headers(limited)


@pytest.mark.parametrize("corruption", ["unknown-key", "checksum", "unresolved-reference"])
def test_invalid_bundle_is_unavailable_without_paths_or_private_details(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    corruption: str,
) -> None:
    def mutate_manifest(manifest: dict[str, Any]) -> None:
        if corruption == "unknown-key":
            manifest["fictitious_private_path"] = _CANARY_PATH
        elif corruption == "unresolved-reference":
            reference = manifest["content"][0]["blocks"][1]["inlines"][1]
            reference["source_id"] = "missing-source-fiction"

    release = write_fictitious_learning_bundle(
        tmp_path,
        manifest_mutator=mutate_manifest,
    )
    if corruption == "checksum":
        (release / "assets" / "source-fiction.bin").write_bytes(b"FICTITIOUS-CORRUPTED-BYTES\n")
    monkeypatch.setattr(get_settings(), "learning_content_root", release)
    reset_learning_bundle_cache()
    _install_identity(client)

    response = client.get("/api/v1/learning/catalog")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "LEARNING_CATALOG_UNAVAILABLE",
            "message": "Le catalogue Parcours est temporairement indisponible.",
        }
    }
    _assert_private_headers(response)
    forbidden_fragments = (
        _CANARY_PATH,
        str(release),
        "source-fiction.bin",
        "manifest.json",
        "missing-source-fiction",
        "sha256",
    )
    for fragment in forbidden_fragments:
        assert fragment not in response.text
        assert fragment not in caplog.text
    reset_learning_bundle_cache()


def test_progress_hint_list_is_bounded_and_unknown_fields_are_rejected(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client)

    too_many_hints = client.put(
        "/api/v1/learning/progress/content-fiction",
        json={"opened_hint_ids": [f"hint-{index}" for index in range(65)]},
        headers=csrf_headers(client),
    )
    long_answer = client.put(
        "/api/v1/learning/progress/content-fiction",
        json={"free_answer": "Contenu libre non accepté"},
        headers=csrf_headers(client),
    )

    assert too_many_hints.status_code == 422
    assert long_answer.status_code == 422
    _assert_private_headers(too_many_hints)
    _assert_private_headers(long_answer)


def test_asset_filename_has_no_header_injection_surface(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client)

    response = client.get("/api/v1/learning/assets/asset-source-fiction/download")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert re.fullmatch(
        r"attachment; filename=\"source-fictive\.pdf\"; "
        r"filename\*=UTF-8''source-fictive\.pdf",
        disposition,
    )
    assert response.headers["content-length"] == str(len(SOURCE_BYTES))
    _assert_private_headers(response)


def test_real_owner_share_token_cannot_read_known_progress_or_attempts(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    _install_identity(client)
    progress = client.put(
        "/api/v1/learning/progress/content-fiction",
        json={"completed": True, "favorite": True},
        headers=csrf_headers(client),
    )
    assert progress.status_code == 200

    created = client.post(
        "/api/v1/tokens",
        json={"name": "Token owner entièrement fictif", "role": "owner", "expires_in_days": 1},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text

    delegated = TestClient(client.app, base_url="https://testserver")
    try:
        login = delegated.post(
            "/api/v1/auth/login/token",
            json={"token": created.json()["token"]},
        )
        assert login.status_code == 200, login.text
        assert login.json()["role"] == "owner"

        known_item = delegated.get("/api/v1/learning/progress/content-fiction")
        progress_list = delegated.get("/api/v1/learning/progress")
        attempts = delegated.get("/api/v1/learning/attempts")
        for response in (known_item, progress_list, attempts):
            assert response.status_code == 404
            assert_api_error(response, "Ressource introuvable")
            assert "content-fiction" not in response.text
            _assert_private_headers(response)
    finally:
        delegated.close()


def test_learning_state_does_not_change_official_notes_or_leaderboard_score(
    client: TestClient,
    fictitious_content_root: Path,
) -> None:
    identity = _install_identity(client)
    metadata_at = utcnow() - timedelta(hours=1)
    with SessionLocal() as db:
        account = db.get(Account, identity.account_id)
        assert account is not None
        account.ue_metadata_refreshed_at = metadata_at
        db.add(
            UeSetting(
                account_id=identity.account_id,
                code="FIC100",
                credits_ects=4,
                earned_credits_ects=4,
                title="UE officielle fictive",
                year="2A",
                semester="S7",
                official_code="FICTITIOUS-FIC100",
                official_grade="B",
                metadata_source="competences",
                metadata_refreshed_at=metadata_at,
            )
        )
        db.add(
            Note(
                account_id=identity.account_id,
                source="pass",
                source_key="fictitious-official-note",
                ue_code="FIC100",
                raw_label="Évaluation officielle fictive",
                raw_score=15,
                raw_coefficient=1,
                raw_is_resit=False,
            )
        )
        db.commit()
        score_before = account_leaderboard_score(db, identity.account_id)

    notes_before = client.get("/api/v1/notes")
    assert notes_before.status_code == 200

    progress = client.put(
        f"/api/v1/learning/progress/{_EXERCISE_ID}",
        json={
            "completed": True,
            "opened_hint_ids": [_EXERCISE_HINT_ID],
            "self_assessment": 5,
            "favorite": True,
        },
        headers=csrf_headers(client),
    )
    attempt = client.post(
        "/api/v1/learning/attempts",
        json={
            "exercise_id": _EXERCISE_ID,
            "attempt_kind": "self_assessed",
            "self_assessment": 1,
        },
        headers=csrf_headers(client),
    )
    assert progress.status_code == 200, progress.text
    assert attempt.status_code == 201, attempt.text

    notes_after = client.get("/api/v1/notes")
    with SessionLocal() as db:
        score_after = account_leaderboard_score(db, identity.account_id)
        official_note = db.scalar(
            select(Note).where(
                Note.account_id == identity.account_id,
                Note.source_key == "fictitious-official-note",
            )
        )
        assert official_note is not None
        assert official_note.raw_score == 15
        assert official_note.raw_coefficient == 1

    assert notes_after.status_code == 200
    assert notes_after.json() == notes_before.json()
    assert score_after == score_before
    leaderboard_source = inspect.getsource(leaderboard_service)
    assert "LearningProgress" not in leaderboard_source
    assert "LearningAttempt" not in leaderboard_source
