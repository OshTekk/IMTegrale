from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.models import Account, WebSession
from app.security import create_web_session
from app.services.events import record_event
from app.services.imt import CompetencyUe, PassEntry, PassProfile
from app.services.sync import apply_competency_ues, apply_pass_entries, apply_pass_profile
from app.services.sync_control import set_login_sync_cooldown


class ImtLoginFinalizationRejected(RuntimeError):
    """The post-CAS account authority no longer permits issuing access."""


@dataclass(frozen=True, slots=True)
class ImtLoginAuthority:
    account_id: str | None
    access_generation: int | None
    imt_username: str

    @property
    def initial_import(self) -> bool:
        return self.account_id is None


@dataclass(frozen=True, slots=True)
class FinalizedImtLogin:
    account: Account
    web_session: WebSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class LockedImtLogin:
    account: Account
    initial_import: bool


def capture_imt_login_authority(
    db: Session,
    *,
    imt_username: str,
    allow_signup: bool,
) -> ImtLoginAuthority:
    """Capture primitive authority and end the read transaction before CAS."""

    try:
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
            )
        return ImtLoginAuthority(
            account_id=account.id,
            access_generation=account.access_generation,
            imt_username=account.imt_username,
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
) -> LockedImtLogin:
    if (
        authenticated_username != authority.imt_username
        or authenticated_username != authenticated_username.strip().lower()
    ):
        raise ImtLoginFinalizationRejected

    if authority.initial_import:
        _lock_first_login(db, authority.imt_username)
        account = _locked_account_by_login(db, authority.imt_username)
        if account is None:
            account = Account(
                imt_username=authority.imt_username,
                display_name=authority.imt_username.split("@", 1)[0],
            )
            db.add(account)
            db.flush()
        if account.access_generation != 1:
            raise ImtLoginFinalizationRejected
    else:
        if authority.account_id is None or authority.access_generation is None:
            raise ImtLoginFinalizationRejected
        account = _locked_account(db, authority.account_id)
        if account is None or account.access_generation != authority.access_generation:
            raise ImtLoginFinalizationRejected

    if account.is_disabled or account.imt_username != authority.imt_username:
        raise ImtLoginFinalizationRejected
    return LockedImtLogin(account=account, initial_import=authority.initial_import)


def finalize_imt_login(
    db: Session,
    *,
    locked_login: LockedImtLogin,
    entries: list[PassEntry],
    profile: PassProfile | None,
    competency_ues: list[CompetencyUe] | None,
    service_session_stored: bool,
    user_agent: str,
    settings: Settings,
    now: datetime | None = None,
) -> FinalizedImtLogin:
    """Issue IMT access only against fresh authority in one transaction."""

    try:
        account = locked_login.account
        current = now or utcnow()
        account.last_login_at = current
        account.student_status_verified_at = current
        if locked_login.initial_import:
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
        if not service_session_stored and account.auto_sync_enabled:
            account.auto_sync_paused_reason = "reauth_required"
            account.auto_sync_paused_at = current

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
