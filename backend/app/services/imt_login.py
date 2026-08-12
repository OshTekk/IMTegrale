from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.models import Account, WebSession
from app.security import cleanup_sessions, create_web_session
from app.services.events import record_event
from app.services.imt import CompetencyUe, PassEntry, PassProfile
from app.services.pass_session_crypto import PassSessionSealer
from app.services.pass_sessions import store_service_session_if_reusable
from app.services.sync import apply_competency_ues, apply_pass_entries, apply_pass_profile
from app.services.sync_control import set_login_sync_cooldown


class ImtLoginFinalizationRejected(RuntimeError):
    """The fresh post-CAS authority no longer permits issuing access."""


@dataclass(frozen=True, slots=True)
class ImtLoginAuthority:
    account_id: str | None
    access_generation: int | None
    imt_username: str
    first_login: bool
    signup_allowed: bool

    @property
    def initial_import(self) -> bool:
        return self.first_login


@dataclass(frozen=True, slots=True)
class LockedImtLogin:
    account: Account
    new_account: bool


@dataclass(frozen=True, slots=True)
class FinalizedImtLogin:
    account: Account
    web_session: WebSession
    session_token: str
    csrf_token: str


def capture_imt_login_authority(
    db: Session,
    *,
    imt_username: str,
    allow_signup: bool,
) -> ImtLoginAuthority:
    """Capture primitives only and end the read transaction before CAS."""

    try:
        if imt_username != imt_username.strip().lower():
            raise ImtLoginFinalizationRejected
        account = db.scalar(select(Account).where(Account.imt_username == imt_username))
        if account is not None and account.is_disabled:
            raise ImtLoginFinalizationRejected
        if account is None:
            if not allow_signup:
                raise ImtLoginFinalizationRejected
            return ImtLoginAuthority(
                account_id=None,
                access_generation=None,
                imt_username=imt_username,
                first_login=True,
                signup_allowed=True,
            )
        return ImtLoginAuthority(
            account_id=account.id,
            access_generation=account.access_generation,
            imt_username=account.imt_username,
            first_login=False,
            signup_allowed=allow_signup,
        )
    finally:
        db.rollback()
        db.expunge_all()


def _lock_first_login(db: Session, imt_username: str) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"imt-first-login\0{imt_username}".encode()).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _locked_account(db: Session, account_id: str) -> Account | None:
    return db.scalar(
        select(Account)
        .where(Account.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _locked_account_by_login(db: Session, imt_username: str) -> Account | None:
    return db.scalar(
        select(Account)
        .where(Account.imt_username == imt_username)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_imt_login_account(
    db: Session,
    *,
    authority: ImtLoginAuthority,
    authenticated_username: str,
    allow_signup: bool,
) -> LockedImtLogin:
    normalized_authenticated = authenticated_username.strip().lower()
    if (
        authenticated_username != normalized_authenticated
        or normalized_authenticated != authority.imt_username
    ):
        raise ImtLoginFinalizationRejected

    if authority.first_login:
        if not authority.signup_allowed or not allow_signup:
            raise ImtLoginFinalizationRejected
        _lock_first_login(db, authority.imt_username)
        account = _locked_account_by_login(db, authority.imt_username)
        new_account = account is None
        if new_account:
            account = Account(
                imt_username=authority.imt_username,
                display_name=authority.imt_username.split("@", 1)[0],
            )
            db.add(account)
            db.flush()
        elif account.access_generation != 1:
            raise ImtLoginFinalizationRejected
    else:
        if authority.account_id is None or authority.access_generation is None:
            raise ImtLoginFinalizationRejected
        account = _locked_account(db, authority.account_id)
        new_account = False
        if account is None or account.access_generation != authority.access_generation:
            raise ImtLoginFinalizationRejected

    if account.is_disabled or account.imt_username != authority.imt_username:
        raise ImtLoginFinalizationRejected
    return LockedImtLogin(account=account, new_account=new_account)


def finalize_imt_login(
    db: Session,
    *,
    locked_login: LockedImtLogin,
    entries: list[PassEntry],
    profile: PassProfile | None,
    competency_ues: list[CompetencyUe] | None,
    session_snapshot: str,
    pass_session_sealer: PassSessionSealer,
    hub_attempted: bool,
    hub_succeeded: bool,
    user_agent: str,
    settings: Settings,
    now: datetime | None = None,
) -> FinalizedImtLogin:
    """Persist every successful-login side effect in the locked transaction."""

    try:
        account = locked_login.account
        current = now or utcnow()
        account.last_login_at = current
        account.student_status_verified_at = current
        if locked_login.new_account:
            set_login_sync_cooldown(account, current)
            apply_pass_entries(
                db,
                account,
                entries,
                actor="owner",
                initial_import=True,
            )
            apply_competency_ues(db, account, competency_ues, actor="owner")
        apply_pass_profile(account, profile)
        stored_session = store_service_session_if_reusable(
            db,
            account,
            session_snapshot,
            sealer=pass_session_sealer,
            hub_attempted=hub_attempted,
            hub_succeeded=hub_succeeded,
            now=current,
        )
        if stored_session is None and account.auto_sync_enabled:
            account.auto_sync_paused_reason = "reauth_required"
            account.auto_sync_paused_at = current

        cleanup_sessions(db)
        web_session, session_token, csrf_token = create_web_session(
            db,
            account=account,
            role="owner",
            auth_method="imt",
            user_agent=user_agent,
            settings=settings,
        )
        record_event(
            db,
            account_id=account.id,
            kind="auth:login",
            actor="owner",
            payload={"method": "imt"},
        )
        db.commit()
        return FinalizedImtLogin(
            account=account,
            web_session=web_session,
            session_token=session_token,
            csrf_token=csrf_token,
        )
    except Exception:
        db.rollback()
        raise
