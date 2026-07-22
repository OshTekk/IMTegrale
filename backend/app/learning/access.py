from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_security import require_allowed_network_identity
from app.config import Settings, get_settings
from app.database import get_db, utcnow
from app.models import Account, LearningAccessGrant, WebSession
from app.security import (
    AuthContext,
    get_auth_context,
    require_action,
    require_primary_owner_action,
)

if TYPE_CHECKING:
    from app.learning.bundle import LearningBundleSnapshot

LEARNING_AUDIENCE_FIP_2028 = "fip:2028"
STUDENT_REVERIFICATION_REQUIRED = "STUDENT_REVERIFICATION_REQUIRED"
LEARNING_CATALOG_UNAVAILABLE = "LEARNING_CATALOG_UNAVAILABLE"

_NOT_FOUND_DETAIL = "Ressource introuvable"
_REVERIFICATION_MESSAGE = "Une nouvelle vérification IMT est requise pour accéder à Parcours."
_CATALOG_UNAVAILABLE_MESSAGE = "Le catalogue Parcours est temporairement indisponible."

BundleLoader = Callable[[Settings], "LearningBundleSnapshot"]


@dataclass(frozen=True, slots=True)
class LearningAccessContext:
    auth: AuthContext
    audience: str
    audience_label: str
    level_label: str
    catalog_version: str | None
    bundle: LearningBundleSnapshot | None
    manual_grant_id: str | None = None

    @property
    def account(self) -> Account:
        return self.auth.account

    @property
    def session(self) -> WebSession:
        return self.auth.session

    @property
    def via_manual_grant(self) -> bool:
        return self.manual_grant_id is not None


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)


def _reverification_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": STUDENT_REVERIFICATION_REQUIRED,
            "message": _REVERIFICATION_MESSAGE,
        },
    )


def _catalog_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": LEARNING_CATALOG_UNAVAILABLE,
            "message": _CATALOG_UNAVAILABLE_MESSAGE,
        },
    )


def _default_bundle_loader(settings: Settings) -> LearningBundleSnapshot:
    # Imported lazily so an absent or disabled Parcours bundle never prevents the
    # main application, authentication, or migrations from starting.
    from app.learning.bundle import get_learning_bundle

    return get_learning_bundle(settings)


def _catalog_version(bundle: object) -> str:
    release_id = getattr(bundle, "catalog_version", None)
    if not isinstance(release_id, str) or not release_id:
        release_id = getattr(bundle, "release_id", None)
    if not isinstance(release_id, str) or not release_id:
        manifest = getattr(bundle, "manifest", None)
        release_id = getattr(manifest, "release_id", None)
    if not isinstance(release_id, str) or not release_id:
        raise _catalog_unavailable()
    return release_id


def _require_supported_audience(bundle: object, audience_id: str) -> None:
    try:
        audience_ids = {audience.id for audience in bundle.audiences}
    except (AttributeError, TypeError):
        raise _catalog_unavailable() from None
    if audience_id not in audience_ids:
        raise _catalog_unavailable()


def _require_runtime_release_compatibility(
    bundle: object,
    settings: Settings,
    audience_id: str,
) -> None:
    manifest = getattr(bundle, "manifest", None)
    release_mode = getattr(manifest, "release_mode", None)
    if release_mode != "personal_library":
        return
    try:
        bundle_audiences = {audience.id for audience in bundle.audiences}
    except (AttributeError, TypeError):
        raise _catalog_unavailable() from None
    if (
        settings.learning_access_mode != "personal"
        or not audience_id.startswith("personal:")
        or audience_id != settings.learning_audience_id
        or bundle_audiences != {settings.learning_audience_id}
    ):
        raise _catalog_unavailable()


def require_learning_ingress(request: Request, settings: Settings) -> str | None:
    """Require an exact LAN/Tailnet identity when personal mode is enabled."""

    if settings.learning_access_mode != "personal":
        return None
    return require_allowed_network_identity(
        request,
        settings,
        settings.learning_allowed_identities,
    )


def load_learning_catalog_for_access(
    context: LearningAccessContext,
    settings: Settings,
    *,
    bundle_loader: BundleLoader | None = None,
) -> LearningAccessContext:
    """Attach a validated bundle to an already-authorized context."""

    try:
        bundle = (bundle_loader or _default_bundle_loader)(settings)
    except Exception:
        raise _catalog_unavailable() from None
    try:
        _require_supported_audience(bundle, context.audience)
        _require_runtime_release_compatibility(bundle, settings, context.audience)
        catalog_version = _catalog_version(bundle)
    except HTTPException:
        raise
    except Exception:
        raise _catalog_unavailable() from None
    return LearningAccessContext(
        auth=context.auth,
        audience=context.audience,
        audience_label=context.audience_label,
        level_label=context.level_label,
        catalog_version=catalog_version,
        bundle=bundle,
        manual_grant_id=context.manual_grant_id,
    )


def _has_primary_session(auth: AuthContext) -> bool:
    return bool(
        auth.account.is_disabled is False
        and auth.role == "owner"
        and auth.session.share_token_id is None
        and auth.session.auth_method in {"imt", "passkey"}
    )


def _account_is_allowed(account: Account, settings: Settings) -> bool:
    if settings.learning_access_mode != "personal":
        return True
    username = account.imt_username.strip().casefold()
    return username in settings.learning_allowed_imt_usernames


def _has_automatic_audience(account: Account) -> bool:
    return bool(
        isinstance(account.program, str)
        and account.program.strip().casefold() == "fip"
        and account.promotion_year == 2028
        and isinstance(account.academic_source, str)
        and account.academic_source.strip().casefold() in {"pass", "admin"}
        and account.academic_verified_at is not None
    )


def _student_status_is_fresh(account: Account, settings: Settings, now: datetime) -> bool:
    verified_at = account.student_status_verified_at
    if verified_at is None:
        return False
    maximum_age = timedelta(days=settings.learning_student_status_max_age_days)
    return _ensure_utc(verified_at) + maximum_age > now


def _active_manual_grant(
    db: Session,
    *,
    account_id: str,
    audience: str,
    now: datetime,
) -> LearningAccessGrant | None:
    return db.scalar(
        select(LearningAccessGrant)
        .where(
            LearningAccessGrant.account_id == account_id,
            LearningAccessGrant.audience == audience,
            LearningAccessGrant.granted_at <= now,
            LearningAccessGrant.expires_at > now,
            LearningAccessGrant.revoked_at.is_(None),
        )
        .order_by(LearningAccessGrant.expires_at.desc(), LearningAccessGrant.id.desc())
        .limit(1)
    )


def learning_access_for(
    db: Session,
    auth: AuthContext,
    settings: Settings | None = None,
    *,
    load_catalog: bool = True,
    bundle_loader: BundleLoader | None = None,
    now: datetime | None = None,
) -> LearningAccessContext:
    """Resolve the sole supported learning audience from server-side evidence.

    The caller cannot select an audience. Primary-session checks happen before
    querying grants, and entitlement is established before the bundle is read.
    """

    resolved_settings = settings or get_settings()
    current = _ensure_utc(now or utcnow())
    if not _has_primary_session(auth):
        raise _not_found()
    if not _account_is_allowed(auth.account, resolved_settings):
        raise _not_found()

    grant: LearningAccessGrant | None = None
    automatic_audience = _has_automatic_audience(auth.account)
    fresh_student_status = _student_status_is_fresh(
        auth.account,
        resolved_settings,
        current,
    )
    if not (automatic_audience and fresh_student_status):
        grant = _active_manual_grant(
            db,
            account_id=auth.account.id,
            audience=resolved_settings.learning_audience_id,
            now=current,
        )
        if grant is None and not automatic_audience:
            raise _not_found()
        if grant is None:
            raise _reverification_required()

    context = LearningAccessContext(
        auth=auth,
        audience=resolved_settings.learning_audience_id,
        audience_label=resolved_settings.learning_audience_label,
        level_label=resolved_settings.learning_level_label,
        catalog_version=None,
        bundle=None,
        manual_grant_id=grant.id if grant is not None else None,
    )
    if not load_catalog:
        return context
    return load_learning_catalog_for_access(
        context,
        resolved_settings,
        bundle_loader=bundle_loader,
    )


def learning_access_after_database_release(
    db: Session,
    auth: AuthContext,
    settings: Settings,
    *,
    bundle_loader: BundleLoader | None = None,
) -> LearningAccessContext:
    """Resolve entitlement, return SQL resources, then validate the bundle.

    ``Session.close()`` releases the current transaction/connection while
    leaving SQLAlchemy's Session reusable. Progress and attempt routes can
    therefore start a new transaction after catalog validation without holding
    a pool slot while a cold release waits on its lock or hashes files.
    """

    try:
        context = learning_access_for(db, auth, settings, load_catalog=False)
    finally:
        db.close()
    return load_learning_catalog_for_access(
        context,
        settings,
        bundle_loader=bundle_loader,
    )


def require_learning_access(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LearningAccessContext:
    require_learning_ingress(request, settings)
    return learning_access_after_database_release(db, auth, settings)


def _get_stream_auth_context(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Authenticate a streaming response without retaining its DB session.

    FastAPI otherwise gives yield dependencies request scope, which would keep
    ``get_db`` open until the complete response body has been sent. Asset
    authorization is fully resolved before the path operation returns, so a
    function-scoped session is both sufficient and safer for long downloads.
    """

    require_learning_ingress(request, settings)
    return get_auth_context(request, db, settings)


def require_learning_stream_access(
    auth: AuthContext = Depends(_get_stream_auth_context),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> LearningAccessContext:
    """Authorize an asset, release SQL, then load its immutable bundle."""

    return learning_access_after_database_release(db, auth, settings)


def require_learning_action(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LearningAccessContext:
    require_learning_ingress(request, settings)
    try:
        # Entitlement deliberately precedes CSRF so delegated/non-eligible
        # sessions cannot use mutation errors as a Parcours discovery oracle.
        context = learning_access_for(db, auth, settings, load_catalog=False)
        require_action(request, auth, settings)
    finally:
        db.close()
    return load_learning_catalog_for_access(context, settings)


def require_learning_progress_erasure(
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """Authorize privacy erasure without requiring a current learning entitlement.

    A primary owner must be able to erase previously stored state after their
    audience, verification freshness, grant, or bundle availability changes.
    ``require_primary_owner_action`` keeps authentication, account activity,
    Origin, CSRF, owner role, primary auth method, and shared-token rejection.
    """

    require_learning_ingress(request, settings)
    if not _account_is_allowed(auth.account, settings):
        raise _not_found()
    return auth


def learning_session_view(
    db: Session,
    auth: AuthContext,
    settings: Settings,
    *,
    request: Request | None = None,
) -> dict[str, Any]:
    """Return UX hints without ever making the main session endpoint fail."""

    unavailable = {
        "available": False,
        "audience_label": None,
        "level_label": None,
        "reverify_required": False,
        "catalog_version": None,
    }
    if settings.learning_access_mode == "personal":
        if request is None:
            return unavailable
        try:
            require_learning_ingress(request, settings)
        except HTTPException:
            return unavailable
    try:
        context = learning_access_after_database_release(db, auth, settings)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        if code in {STUDENT_REVERIFICATION_REQUIRED, LEARNING_CATALOG_UNAVAILABLE}:
            return {
                **unavailable,
                "audience_label": settings.learning_audience_label,
                "level_label": settings.learning_level_label,
                "reverify_required": code == STUDENT_REVERIFICATION_REQUIRED,
            }
        return unavailable
    except Exception:
        return unavailable
    return {
        "available": True,
        "audience_label": context.audience_label,
        "level_label": context.level_label,
        "reverify_required": False,
        "catalog_version": context.catalog_version,
    }
