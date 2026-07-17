from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.models import Account, CohortPulse
from app.services.sync_schedule import next_business_time

PULSE_COOLDOWN = timedelta(hours=2)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _jitter_seconds(account_id: str, sequence: int, interval_hours: int) -> int:
    key = get_settings().token_pepper.encode("utf-8")
    digest = hmac.new(
        key,
        f"cohort-pulse\0{account_id}\0{sequence}".encode(),
        hashlib.sha256,
    ).digest()
    fraction = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return int(fraction * interval_hours * 3600)


def emit_cohort_pulse(
    db: Session,
    source: Account,
    *,
    now: datetime | None = None,
) -> int:
    current = ensure_utc(now or utcnow())
    if source.program == "unknown" or source.promotion_year is None:
        return 0
    statement = select(CohortPulse).where(
        CohortPulse.program == source.program,
        CohortPulse.promotion_year == source.promotion_year,
    )
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        statement = statement.with_for_update()
    pulse = db.scalar(statement)
    if pulse is not None and ensure_utc(pulse.last_emitted_at) + PULSE_COOLDOWN > current:
        return 0
    if pulse is None:
        pulse = CohortPulse(
            program=source.program,
            promotion_year=source.promotion_year,
            last_emitted_at=current,
        )
        db.add(pulse)
        db.flush()
    else:
        pulse.sequence += 1
        pulse.last_emitted_at = current

    candidates = list(
        db.scalars(
            select(Account).where(
                Account.id != source.id,
                Account.program == source.program,
                Account.promotion_year == source.promotion_year,
                Account.auto_sync_enabled.is_(True),
                Account.auto_sync_consented_at.is_not(None),
                Account.auto_sync_adaptive.is_(True),
                Account.is_disabled.is_(False),
            )
        )
    )
    for account in candidates:
        account.auto_sync_current_interval_hours = account.auto_sync_interval_hours
        account.auto_sync_no_change_streak = 0
        candidate = next_business_time(
            account,
            current
            + timedelta(
                seconds=_jitter_seconds(
                    account.id,
                    pulse.sequence,
                    account.auto_sync_interval_hours,
                )
            ),
        )
        existing = ensure_utc(account.auto_sync_next_at)
        account.auto_sync_next_at = min(existing, candidate) if existing else candidate
    pulse.affected_accounts = len(candidates)
    return len(candidates)
