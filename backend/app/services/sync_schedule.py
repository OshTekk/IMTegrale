from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.database import utcnow
from app.models import Account

AUTO_SYNC_INTERVALS = (2, 4, 6, 8, 12, 24)
BUSINESS_START = time(8, 0)
BUSINESS_END = time(20, 0)
BUSINESS_WEEKDAYS = frozenset(range(5))


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def account_zone(account: Account) -> ZoneInfo:
    try:
        return ZoneInfo(account.timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("Europe/Paris")


def in_business_window(account: Account, now: datetime | None = None) -> bool:
    local = ensure_utc(now or utcnow()).astimezone(account_zone(account))
    return (
        local.weekday() in BUSINESS_WEEKDAYS
        and BUSINESS_START <= local.time().replace(tzinfo=None) < BUSINESS_END
    )


def auto_sync_is_due(account: Account, now: datetime | None = None) -> bool:
    current = ensure_utc(now or utcnow())
    if (
        not account.auto_sync_enabled
        or account.auto_sync_consented_at is None
        or account.auto_sync_paused_reason is not None
        or account.is_disabled
        or not in_business_window(account, current)
    ):
        return False
    return next_auto_sync_at(account, current) <= current


def next_business_time(account: Account, candidate: datetime) -> datetime:
    zone = account_zone(account)
    local = ensure_utc(candidate).astimezone(zone)
    for _ in range(8):
        local_time = local.time().replace(tzinfo=None)
        if local.weekday() not in BUSINESS_WEEKDAYS:
            local = datetime.combine(local.date() + timedelta(days=1), BUSINESS_START, zone)
            continue
        if local_time < BUSINESS_START:
            return datetime.combine(local.date(), BUSINESS_START, zone).astimezone(UTC)
        if local_time >= BUSINESS_END:
            local = datetime.combine(local.date() + timedelta(days=1), BUSINESS_START, zone)
            continue
        return local.astimezone(UTC)
    raise RuntimeError("Impossible de calculer la prochaine fenêtre de synchronisation")


def next_auto_sync_at(account: Account, now: datetime | None = None) -> datetime | None:
    if (
        not account.auto_sync_enabled
        or account.auto_sync_consented_at is None
        or account.auto_sync_paused_reason is not None
        or account.is_disabled
    ):
        return None
    current = ensure_utc(now or utcnow())
    if account.auto_sync_next_at is not None:
        return next_business_time(account, ensure_utc(account.auto_sync_next_at))
    candidate = current
    if account.last_sync_at is not None:
        scheduled = (
            ensure_utc(account.last_sync_at)
            + timedelta(hours=effective_auto_sync_interval(account))
        )
        candidate = scheduled if in_business_window(account, current) else max(current, scheduled)
    return next_business_time(account, candidate)


def effective_auto_sync_interval(account: Account) -> int:
    if not account.auto_sync_adaptive:
        return account.auto_sync_interval_hours
    return account.auto_sync_current_interval_hours


def auto_sync_overdue_by_full_interval(
    account: Account,
    now: datetime | None = None,
) -> bool:
    current = ensure_utc(now or utcnow())
    due_at = next_auto_sync_at(account, current)
    return bool(
        due_at is not None
        and due_at + timedelta(hours=effective_auto_sync_interval(account)) <= current
    )


def automatic_lateness_ratio(account: Account, now: datetime | None = None) -> float:
    current = ensure_utc(now or utcnow())
    due_at = next_auto_sync_at(account, current)
    if due_at is None or due_at > current:
        return -1.0
    interval_seconds = max(1, effective_auto_sync_interval(account) * 3600)
    return (current - due_at).total_seconds() / interval_seconds


def update_adaptive_cadence(
    account: Account,
    *,
    changed: bool,
    actor: str,
    now: datetime | None = None,
) -> None:
    current = ensure_utc(now or utcnow())
    baseline = account.auto_sync_interval_hours
    if not account.auto_sync_adaptive or changed:
        account.auto_sync_current_interval_hours = baseline
        account.auto_sync_no_change_streak = 0
    elif actor == "automatic":
        account.auto_sync_no_change_streak += 1
        if account.auto_sync_no_change_streak >= 3:
            current_index = AUTO_SYNC_INTERVALS.index(account.auto_sync_current_interval_hours)
            account.auto_sync_current_interval_hours = AUTO_SYNC_INTERVALS[
                min(current_index + 1, len(AUTO_SYNC_INTERVALS) - 1)
            ]
            account.auto_sync_no_change_streak = 0
    account.auto_sync_next_at = next_business_time(
        account,
        current + timedelta(hours=effective_auto_sync_interval(account)),
    )


def defer_automatic_sync(
    account: Account,
    *,
    available_at: datetime | None = None,
    now: datetime | None = None,
) -> datetime:
    current = ensure_utc(now or utcnow())
    if available_at is None:
        retry_delay = timedelta(
            minutes=min(120, max(15, effective_auto_sync_interval(account) * 15))
        )
        candidate = current + retry_delay
    else:
        candidate = max(current + timedelta(minutes=1), ensure_utc(available_at))
    account.auto_sync_next_at = next_business_time(account, candidate)
    return account.auto_sync_next_at


def auto_sync_view(account: Account) -> dict:
    return {
        "enabled": account.auto_sync_enabled,
        "interval_hours": account.auto_sync_interval_hours,
        "adaptive": account.auto_sync_adaptive,
        "current_interval_hours": effective_auto_sync_interval(account),
        "no_change_streak": account.auto_sync_no_change_streak,
        "consented_at": account.auto_sync_consented_at,
        "paused_reason": account.auto_sync_paused_reason,
        "paused_at": account.auto_sync_paused_at,
        "next_eligible_at": next_auto_sync_at(account),
        "allowed_intervals": list(AUTO_SYNC_INTERVALS),
        "business_hours": {
            "weekdays": "monday-friday",
            "start": BUSINESS_START.strftime("%H:%M"),
            "end": BUSINESS_END.strftime("%H:%M"),
            "timezone": account.timezone,
        },
    }
