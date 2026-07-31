from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, event, insert, inspect, or_, select, update
from sqlalchemy.orm import Session

from app.database import utcnow
from app.event_contract import (
    EventVisibilityClass,
    event_visibility_class_for_kind,
)
from app.event_cursor import generate_event_cursor
from app.limits import MAX_EVENTS_PER_ACCOUNT

_BUMPED_ACCOUNT_IDS = "_private_comparison_generation_bumped_account_ids"
_PENDING_ACCOUNT_IDS = "_private_comparison_generation_pending_account_ids"

# Values whose meaning is part of the bilateral consent boundary.
_SEMANTIC_VALUE_FIELDS = (
    "is_disabled",
    "official_first_name",
    "official_last_name",
    "program",
    "promotion_year",
    "academic_source",
)
# Timestamp refreshes are routine; only loss/restoration of the underlying
# proof changes the consent generation.
_SEMANTIC_PRESENCE_FIELDS = (
    "official_identity_at",
    "student_status_verified_at",
)
_MUTATION_API_FIELDS = frozenset(
    (
        *_SEMANTIC_VALUE_FIELDS,
        *_SEMANTIC_PRESENCE_FIELDS,
        "academic_verified_at",
    )
)


def apply_private_comparison_account_mutation(account, **changes) -> None:  # noqa: ANN001
    """Single application entry point for fields that affect comparison access."""

    unsupported = set(changes) - _MUTATION_API_FIELDS
    if unsupported:
        raise ValueError("Unsupported private comparison account mutation")
    for field, value in changes.items():
        setattr(account, field, value)


def _presence_changed(history) -> bool:  # noqa: ANN001
    if not history.has_changes():
        return False
    if not history.deleted or not history.added:
        return True
    return (history.deleted[-1] is None) != (history.added[-1] is None)


def _account_semantics_changed(account) -> bool:  # noqa: ANN001
    state = inspect(account)
    if any(state.attrs[field].history.has_changes() for field in _SEMANTIC_VALUE_FIELDS):
        return True
    return any(
        _presence_changed(state.attrs[field].history)
        for field in _SEMANTIC_PRESENCE_FIELDS
    )


def _active_relation_participant_ids(
    session: Session,
    account_ids: Iterable[str],
) -> set[str]:
    from app.models import PrivateComparison

    ids = tuple(sorted(set(account_ids)))
    if not ids:
        return set()
    rows = session.execute(
        select(
            PrivateComparison.account_a_id,
            PrivateComparison.account_b_id,
        ).where(
            PrivateComparison.revoked_at.is_(None),
            or_(
                PrivateComparison.account_a_id.in_(ids),
                PrivateComparison.account_b_id.in_(ids),
            ),
        )
    )
    return {
        participant_id
        for row in rows
        for participant_id in (row.account_a_id, row.account_b_id)
    }


def _lock_accounts_and_generations(
    session: Session,
    account_ids: Iterable[str],
) -> dict[str, int]:
    from app.models import Account

    ids = tuple(sorted(set(account_ids)))
    if not ids:
        return {}
    rows = session.execute(
        select(
            Account.id,
            Account.private_comparison_eligibility_generation,
        )
        .where(Account.id.in_(ids))
        .order_by(Account.id)
        .with_for_update()
    )
    return {account_id: generation for account_id, generation in rows}


def _before_flush(session: Session, _flush_context, _instances) -> None:  # noqa: ANN001
    from app.models import Account

    bumped = session.info.setdefault(_BUMPED_ACCOUNT_IDS, set())
    changed_accounts = [
        account
        for account in session.dirty
        if isinstance(account, Account)
        and inspect(account).persistent
        and account.id not in bumped
        and _account_semantics_changed(account)
    ]
    if not changed_accounts:
        return

    changed_ids = {account.id for account in changed_accounts}
    participant_ids = _active_relation_participant_ids(session, changed_ids)
    generations = _lock_accounts_and_generations(
        session,
        changed_ids | participant_ids,
    )
    pending = session.info.setdefault(_PENDING_ACCOUNT_IDS, set())
    for account in changed_accounts:
        current_generation = generations.get(account.id)
        if current_generation is None:
            continue
        account.private_comparison_eligibility_generation = current_generation + 1
        bumped.add(account.id)
        pending.add(account.id)


def _prune_primary_owner_events(
    session: Session,
    *,
    account_id: str,
) -> None:
    from app.models import Event

    cutoff = session.scalar(
        select(Event.id)
        .where(
            Event.account_id == account_id,
            Event.visibility_class == EventVisibilityClass.PRIMARY_OWNER.value,
        )
        .order_by(Event.id.desc())
        .offset(MAX_EVENTS_PER_ACCOUNT - 1)
        .limit(1)
    )
    if cutoff is not None:
        session.execute(
            delete(Event).where(
                Event.account_id == account_id,
                Event.visibility_class == EventVisibilityClass.PRIMARY_OWNER.value,
                Event.id < cutoff,
            )
        )


def _terminalize_relations(
    session: Session,
    *,
    account_ids: set[str],
    now: datetime,
) -> None:
    from app.models import Event, PrivateComparison

    terminalized = list(
        session.execute(
            update(PrivateComparison)
            .where(
                PrivateComparison.revoked_at.is_(None),
                PrivateComparison.expires_at > now,
                or_(
                    PrivateComparison.account_a_id.in_(account_ids),
                    PrivateComparison.account_b_id.in_(account_ids),
                ),
            )
            .values(
                revoked_at=now,
                revoked_by_account_id=None,
                revoked_reason="eligibility_changed",
                updated_at=now,
            )
            .returning(
                PrivateComparison.public_id,
                PrivateComparison.account_a_id,
                PrivateComparison.account_b_id,
            )
        )
    )
    revoked_event_kind = "private_comparison:revoked"
    revoked_visibility_class = event_visibility_class_for_kind(
        revoked_event_kind
    ).value
    affected_accounts: set[str] = set()
    for relation in terminalized:
        payload = {"public_id": relation.public_id}
        for participant_id in (relation.account_a_id, relation.account_b_id):
            session.execute(
                insert(Event).values(
                    public_cursor=generate_event_cursor(),
                    account_id=participant_id,
                    kind=revoked_event_kind,
                    visibility_class=revoked_visibility_class,
                    payload=payload,
                    actor="system",
                    created_at=now,
                )
            )
            affected_accounts.add(participant_id)
    for participant_id in sorted(affected_accounts):
        _prune_primary_owner_events(session, account_id=participant_id)


def _after_flush_postexec(session: Session, _flush_context) -> None:  # noqa: ANN001
    pending = session.info.get(_PENDING_ACCOUNT_IDS)
    if not pending:
        return
    account_ids = set(pending)
    pending.clear()
    _terminalize_relations(session, account_ids=account_ids, now=utcnow())


def _after_transaction_end(session: Session, transaction) -> None:  # noqa: ANN001
    if transaction.parent is None:
        session.info.pop(_BUMPED_ACCOUNT_IDS, None)
        session.info.pop(_PENDING_ACCOUNT_IDS, None)


_INSTALLED = False


def install_private_comparison_lifecycle_hooks() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush_postexec", _after_flush_postexec)
    event.listen(Session, "after_transaction_end", _after_transaction_end)
    _INSTALLED = True
