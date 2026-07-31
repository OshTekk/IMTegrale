from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated, NoReturn

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.models import Account, WebSession
from app.security import (
    AuthContext,
    browser_session_scope,
    ensure_utc,
    secure_compare,
)

PRIVATE_COMPARISON_SESSION_BINDING_HEADER = "X-IMTEGRALE-SESSION-BINDING"
_SESSION_BINDING_PATTERN_PREFIX = "bss1_"


def raise_private_comparison_session_mismatch() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "PRIVATE_COMPARISON_SESSION_MISMATCH",
            "message": "La session de comparaison privée a changé. Réessaie depuis son état actuel.",
        },
    )


def require_private_comparison_session_binding(
    value: Annotated[
        str,
        Header(alias=PRIVATE_COMPARISON_SESSION_BINDING_HEADER),
    ],
) -> str:
    if (
        len(value) != len(_SESSION_BINDING_PATTERN_PREFIX) + 64
        or not value.startswith(_SESSION_BINDING_PATTERN_PREFIX)
    ):
        raise_private_comparison_session_mismatch()
    try:
        int(value[len(_SESSION_BINDING_PATTERN_PREFIX) :], 16)
    except ValueError:
        raise_private_comparison_session_mismatch()
    return value


def canonical_private_comparison_account_ids(account_ids: Iterable[str]) -> tuple[str, ...]:
    """Canonical UUID-string order used by every bilateral comparison path."""

    return tuple(sorted(set(account_ids)))


def private_comparison_account_lock_statement(
    account_ids: Iterable[str],
    *,
    include_disabled: bool,
    shared: bool,
):
    """Build the only allowed participant-account lock: canonical ascending IDs."""

    statement = select(Account).where(
        Account.id.in_(canonical_private_comparison_account_ids(account_ids))
    )
    if not include_disabled:
        statement = statement.where(Account.is_disabled.is_(False))
    return (
        statement.order_by(Account.id)
        .execution_options(populate_existing=True)
        .with_for_update(read=shared)
    )


@dataclass(frozen=True, slots=True)
class PrivateComparisonLockPlan:
    """Total lock order: WebSession, sorted accounts, invitation, relation."""

    session_id: str
    account_ids: tuple[str, ...]
    invitation_id: str | None = None
    relation_id: str | None = None


@dataclass(slots=True)
class ReboundPrivateComparisonSession:
    auth: AuthContext
    expected_binding: str
    expected_session_values: tuple[str, str, str, str | None, int]
    accounts: dict[str, Account]


def private_comparison_lock_plan(
    auth: AuthContext,
    account_ids: Iterable[str],
    *,
    invitation_id: str | None = None,
    relation_id: str | None = None,
) -> PrivateComparisonLockPlan:
    return PrivateComparisonLockPlan(
        session_id=auth.session.id,
        account_ids=canonical_private_comparison_account_ids((*account_ids, auth.account.id)),
        invitation_id=invitation_id,
        relation_id=relation_id,
    )


def _locked_accounts_in_plan(db: Session, plan: PrivateComparisonLockPlan) -> dict[str, Account]:
    rows = list(
        db.scalars(
            private_comparison_account_lock_statement(
                plan.account_ids,
                include_disabled=True,
                shared=False,
            )
        )
    )
    return {account.id: account for account in rows}


def rebind_primary_web_session_for_mutation(
    db: Session,
    *,
    auth: AuthContext,
    expected_binding: str,
    settings: Settings,
    account_ids: Iterable[str],
) -> ReboundPrivateComparisonSession:
    """Acquire the session and participant accounts in the one allowed order."""

    plan = private_comparison_lock_plan(auth, account_ids)
    expected_session_values = (
        auth.session.account_id,
        auth.session.role,
        auth.session.auth_method,
        auth.session.share_token_id,
        auth.session.access_generation,
    )
    fresh_session = db.scalar(
        select(WebSession)
        .where(WebSession.id == plan.session_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if fresh_session is None:
        raise_private_comparison_session_mismatch()
    accounts = _locked_accounts_in_plan(db, plan)
    rebound = ReboundPrivateComparisonSession(
        auth=AuthContext(account=accounts.get(auth.account.id, auth.account), session=fresh_session),
        expected_binding=expected_binding,
        expected_session_values=expected_session_values,
        accounts=accounts,
    )
    validate_rebound_primary_web_session(rebound, settings=settings)
    return rebound


def validate_rebound_primary_web_session(
    rebound: ReboundPrivateComparisonSession,
    *,
    settings: Settings,
) -> None:
    session = rebound.auth.session
    account = rebound.accounts.get(session.account_id)
    current_values = (
        session.account_id,
        session.role,
        session.auth_method,
        session.share_token_id,
        session.access_generation,
    )
    valid = (
        current_values == rebound.expected_session_values
        and account is not None
        and not account.is_disabled
        and session.account_id == rebound.auth.account.id
        and session.role == "owner"
        and session.auth_method in {"imt", "passkey"}
        and session.share_token_id is None
        and session.access_generation == account.access_generation
        and ensure_utc(session.expires_at) > utcnow()
        and settings.private_comparisons_enabled
    )
    recalculated = (
        browser_session_scope(
            session,
            settings,
            private_comparisons_available=True,
        )
        if valid
        else ""
    )
    if not valid or not secure_compare(recalculated, rebound.expected_binding):
        raise_private_comparison_session_mismatch()


def initial_private_comparison_session_preflight(
    db: Session,
    *,
    auth: AuthContext,
    expected_binding: str,
    settings: Settings,
) -> None:
    """Short first check; commit releases it before the final atomic lock plan."""

    rebind_primary_web_session_for_mutation(
        db,
        auth=auth,
        expected_binding=expected_binding,
        settings=settings,
        account_ids=(auth.account.id,),
    )
    db.commit()


def final_rebind_callback(
    rebound: ReboundPrivateComparisonSession,
    *,
    settings: Settings,
) -> Callable[[], None]:
    return lambda: validate_rebound_primary_web_session(rebound, settings=settings)
