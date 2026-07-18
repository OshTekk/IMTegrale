from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import math
import re
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import recurring_ical_events
import requests
from icalendar import Calendar
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, utcnow
from app.limits import (
    MAX_CALENDAR_EVENTS_PER_ACCOUNT,
    MAX_CALENDAR_EVENTS_PER_RESPONSE,
    MAX_CALENDAR_FEED_BYTES,
)
from app.models import Account, CalendarEvent, CalendarFetchAttempt, CalendarSubscription
from app.security import cipher_for, ensure_utc, token_digest
from app.services.events import record_event

CALENDAR_FEED_HOST = "inpass.imt-atlantique.fr"
CALENDAR_FEED_PATH = "/passcal/getics"
CALENDAR_REFRESH_INTERVAL = timedelta(hours=1)
CALENDAR_HISTORY_WINDOW = timedelta(days=400)
CALENDAR_FUTURE_WINDOW = timedelta(days=730)
CALENDAR_CONNECT_WINDOW = timedelta(hours=1)
CALENDAR_CONNECT_MIN_DELAY = timedelta(seconds=60)
CALENDAR_CONNECT_LIMIT = 3
CALENDAR_FETCH_ATTEMPT_RETENTION = timedelta(days=7)
CALENDAR_FETCH_TIMEOUT = (5, 20)
CALENDAR_UPSTREAM_MIN_INTERVAL_SECONDS = 1.0
_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,80}@imta\.fr$", re.IGNORECASE)
_CHECK_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")
_UPSTREAM_LOCK = threading.Lock()
_UPSTREAM_LAST_CALL = 0.0


class CalendarFeedError(RuntimeError):
    code = "CALENDAR_ERROR"
    status_code = 400


class CalendarUrlInvalid(CalendarFeedError):
    code = "CALENDAR_URL_INVALID"


class CalendarUrlMismatch(CalendarFeedError):
    code = "CALENDAR_ACCOUNT_MISMATCH"


class CalendarFeedUnavailable(CalendarFeedError):
    code = "CALENDAR_UPSTREAM_UNAVAILABLE"
    status_code = 502


class CalendarFeedRejected(CalendarFeedError):
    code = "CALENDAR_LINK_REJECTED"


class CalendarFeedTooLarge(CalendarFeedError):
    code = "CALENDAR_FEED_TOO_LARGE"
    status_code = 413


class CalendarFeedInvalid(CalendarFeedError):
    code = "CALENDAR_FEED_INVALID"


class CalendarSecretInvalid(CalendarFeedError):
    code = "CALENDAR_SECRET_INVALID"


class CalendarFetchThrottled(CalendarFeedError):
    code = "CALENDAR_FETCH_COOLDOWN"
    status_code = 429

    def __init__(self, available_at: datetime) -> None:
        super().__init__("Trop de tentatives de connexion au calendrier")
        self.available_at = ensure_utc(available_at)

    @property
    def retry_after_seconds(self) -> int:
        return max(1, math.ceil((self.available_at - utcnow()).total_seconds()))


class CalendarAlreadyRefreshing(CalendarFeedError):
    code = "CALENDAR_REFRESH_RUNNING"
    status_code = 409


@dataclass(frozen=True, slots=True)
class ValidatedFeedUrl:
    normalized: str
    login: str
    account_hint: str


@dataclass(frozen=True, slots=True)
class FeedEvent:
    source_key: str
    title: str
    location: str | None
    starts_at: datetime
    ends_at: datetime
    all_day: bool


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    events: tuple[FeedEvent, ...]
    ignored_count: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    body: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool
    upstream_status: int


def _masked_login(login: str) -> str:
    local, _, domain = login.partition("@")
    visible = local[:2]
    return f"{visible}{'*' * max(3, min(6, len(local) - len(visible)))}@{domain.lower()}"


def validate_feed_url(value: str, account: Account | None = None) -> ValidatedFeedUrl:
    raw = value.strip()
    if not raw or len(raw) > 1_024:
        raise CalendarUrlInvalid("Le lien de calendrier est invalide")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise CalendarUrlInvalid("Le lien de calendrier est invalide") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != CALENDAR_FEED_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != CALENDAR_FEED_PATH
        or parsed.fragment
    ):
        raise CalendarUrlInvalid("Utilise uniquement un lien calendrier officiel INPASS")
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise CalendarUrlInvalid("Le lien de calendrier est invalide") from exc
    if len(pairs) != 2 or {key for key, _value in pairs} != {"login", "check"}:
        raise CalendarUrlInvalid("Le lien INPASS doit contenir login et check uniquement")
    values = {key: value for key, value in pairs}
    login = values["login"].strip().lower()
    check = values["check"].strip().lower()
    if not _LOGIN_PATTERN.fullmatch(login) or not _CHECK_PATTERN.fullmatch(check):
        raise CalendarUrlInvalid("Le lien de calendrier est invalide")
    if account is not None:
        expected = (account.imt_username or "").strip().lower().split("@", 1)[0]
        if not expected or login.split("@", 1)[0] != expected:
            raise CalendarUrlMismatch("Ce lien calendrier ne correspond pas à ton compte IMT")
    query = urlencode((("login", login), ("check", check)))
    normalized = urlunsplit(("https", CALENDAR_FEED_HOST, CALENDAR_FEED_PATH, query, ""))
    return ValidatedFeedUrl(normalized=normalized, login=login, account_hint=_masked_login(login))


def _assert_public_resolution() -> None:
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                CALENDAR_FEED_HOST,
                443,
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as exc:
        raise CalendarFeedUnavailable("Le service calendrier INPASS est indisponible") from exc
    if not addresses:
        raise CalendarFeedUnavailable("Le service calendrier INPASS est indisponible")
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError as exc:
            raise CalendarFeedUnavailable("La résolution INPASS est invalide") from exc
        if not resolved.is_global:
            raise CalendarFeedUnavailable("La résolution INPASS est refusée")


def _bounded_header(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= maximum else None


def fetch_feed(
    validated: ValidatedFeedUrl,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    global _UPSTREAM_LAST_CALL
    _assert_public_resolution()
    headers = {
        "Accept": "text/calendar, text/plain;q=0.9, */*;q=0.1",
        "User-Agent": "IMTegrale-calendar/1.0",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    with _UPSTREAM_LOCK:
        remaining = CALENDAR_UPSTREAM_MIN_INTERVAL_SECONDS - (
            time.monotonic() - _UPSTREAM_LAST_CALL
        )
        if remaining > 0:
            time.sleep(remaining)
        with requests.Session() as session:
            session.trust_env = False
            try:
                with session.get(
                    validated.normalized,
                    headers=headers,
                    timeout=CALENDAR_FETCH_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    response_etag = _bounded_header(response.headers.get("ETag"), 256)
                    response_modified = _bounded_header(
                        response.headers.get("Last-Modified"), 128
                    )
                    if response.status_code == 304:
                        return FetchResult(
                            body=None,
                            etag=response_etag or etag,
                            last_modified=response_modified or last_modified,
                            not_modified=True,
                            upstream_status=304,
                        )
                    if response.status_code in {301, 302, 303, 307, 308}:
                        raise CalendarFeedRejected(
                            "Les redirections de calendrier sont refusées"
                        )
                    if response.status_code in {400, 401, 403, 404}:
                        raise CalendarFeedRejected(
                            "Le lien calendrier est refusé ou n'est plus valide"
                        )
                    if response.status_code != 200:
                        raise CalendarFeedUnavailable(
                            "Le service calendrier INPASS est indisponible"
                        )
                    declared = response.headers.get("Content-Length")
                    if declared:
                        try:
                            if int(declared) > MAX_CALENDAR_FEED_BYTES:
                                raise CalendarFeedTooLarge(
                                    "Le calendrier INPASS est trop volumineux"
                                )
                        except ValueError:
                            pass
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_CALENDAR_FEED_BYTES:
                            raise CalendarFeedTooLarge(
                                "Le calendrier INPASS est trop volumineux"
                            )
                        chunks.append(chunk)
            except requests.RequestException as exc:
                raise CalendarFeedUnavailable(
                    "Le service calendrier INPASS est indisponible"
                ) from exc
            finally:
                _UPSTREAM_LAST_CALL = time.monotonic()
    body = b"".join(chunks)
    if not body.lstrip(b"\xef\xbb\xbf\r\n\t ").startswith(b"BEGIN:VCALENDAR"):
        raise CalendarFeedInvalid("INPASS n'a pas renvoyé un calendrier valide")
    return FetchResult(
        body=body,
        etag=response_etag,
        last_modified=response_modified,
        not_modified=False,
        upstream_status=200,
    )


def _recurrence_values(rule: Any, key: str) -> list[Any]:
    value = rule.get(key)
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _estimate_occurrences(component: Any, window_days: int) -> int:
    rule = component.get("RRULE")
    estimate = 1
    if rule:
        frequencies = _recurrence_values(rule, "FREQ")
        frequency = str(frequencies[0]).upper() if frequencies else ""
        if frequency not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
            raise CalendarFeedInvalid("Une récurrence de calendrier trop fréquente est refusée")
        intervals = _recurrence_values(rule, "INTERVAL")
        try:
            interval = max(1, int(intervals[0])) if intervals else 1
        except (TypeError, ValueError) as exc:
            raise CalendarFeedInvalid("Une récurrence de calendrier est invalide") from exc
        periods = {
            "DAILY": window_days,
            "WEEKLY": math.ceil(window_days / 7),
            "MONTHLY": math.ceil(window_days / 28),
            "YEARLY": math.ceil(window_days / 365),
        }[frequency]
        estimate = max(1, math.ceil(periods / interval))
        counts = _recurrence_values(rule, "COUNT")
        if counts:
            try:
                estimate = min(estimate, max(1, int(counts[0])))
            except (TypeError, ValueError) as exc:
                raise CalendarFeedInvalid("Une récurrence de calendrier est invalide") from exc
        for key in ("BYHOUR", "BYMINUTE", "BYSECOND"):
            values = _recurrence_values(rule, key)
            if values:
                estimate *= len(values)
    rdates = component.get("RDATE")
    if rdates is not None:
        date_values = getattr(rdates, "dts", ())
        estimate += len(date_values)
    return estimate


def _calendar_timezone(calendar: Calendar) -> ZoneInfo:
    value = str(calendar.get("X-WR-TIMEZONE", "Europe/Paris")).strip()
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("Europe/Paris")


def _event_datetime(value: date | datetime, timezone: ZoneInfo) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        resolved = value.replace(tzinfo=timezone) if value.tzinfo is None else value
        return resolved.astimezone(UTC), False
    return datetime.combine(value, datetime_time.min, tzinfo=UTC), True


def _clean_event_text(value: Any, *, maximum: int, fallback: str | None = None) -> str | None:
    if value is None:
        return fallback
    normalized = " ".join(_CONTROL_PATTERN.sub(" ", str(value)).split())
    if not normalized or normalized == "-":
        return fallback
    return normalized[:maximum]


def parse_feed(body: bytes, *, now: datetime | None = None) -> ParsedFeed:
    current = ensure_utc(now or utcnow())
    window_start = current - CALENDAR_HISTORY_WINDOW
    window_end = current + CALENDAR_FUTURE_WINDOW
    try:
        calendar = Calendar.from_ical(body)
    except Exception as exc:
        raise CalendarFeedInvalid("Le calendrier INPASS est illisible") from exc
    source_events = calendar.walk("VEVENT")
    if len(source_events) > MAX_CALENDAR_EVENTS_PER_ACCOUNT:
        raise CalendarFeedTooLarge("Le calendrier contient trop d'événements")
    window_days = (window_end - window_start).days + 1
    estimate = sum(_estimate_occurrences(item, window_days) for item in source_events)
    if estimate > MAX_CALENDAR_EVENTS_PER_ACCOUNT:
        raise CalendarFeedTooLarge("Le calendrier contient trop de récurrences")
    try:
        occurrences = recurring_ical_events.of(calendar, skip_bad_series=True).between(
            window_start,
            window_end,
        )
    except Exception as exc:
        raise CalendarFeedInvalid("Les récurrences du calendrier sont invalides") from exc
    if len(occurrences) > MAX_CALENDAR_EVENTS_PER_ACCOUNT:
        raise CalendarFeedTooLarge("Le calendrier contient trop d'événements")
    timezone = _calendar_timezone(calendar)
    parsed: dict[str, FeedEvent] = {}
    ignored = 0
    for component in occurrences:
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        try:
            start_value = component.start
            end_value = component.end
            starts_at, all_day = _event_datetime(start_value, timezone)
            if end_value is None:
                ends_at = starts_at + (timedelta(days=1) if all_day else timedelta(hours=1))
            else:
                ends_at, end_all_day = _event_datetime(end_value, timezone)
                all_day = all_day and end_all_day
            if ends_at <= starts_at:
                raise ValueError("invalid event duration")
        except Exception:
            ignored += 1
            continue
        title = _clean_event_text(component.get("SUMMARY"), maximum=300, fallback="Cours")
        location = _clean_event_text(component.get("LOCATION"), maximum=300)
        uid = _clean_event_text(component.get("UID"), maximum=512, fallback="event")
        source_key = token_digest(
            "\0".join((uid or "event", starts_at.isoformat(), ends_at.isoformat(), title or "Cours"))
        )
        parsed[source_key] = FeedEvent(
            source_key=source_key,
            title=title or "Cours",
            location=location,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
        )
    if source_events and not parsed:
        raise CalendarFeedInvalid("Aucun événement exploitable n'a été trouvé")
    ordered = tuple(sorted(parsed.values(), key=lambda item: (item.starts_at, item.ends_at, item.title)))
    return ParsedFeed(events=ordered, ignored_count=ignored)


def _record_attempt(db: Session, account_id: str, *, kind: str, outcome: str) -> None:
    db.add(
        CalendarFetchAttempt(
            account_id=account_id,
            kind=kind,
            outcome=outcome,
            attempted_at=utcnow(),
        )
    )


def assert_connect_allowed(db: Session, account_id: str) -> None:
    now = utcnow()
    attempts = list(
        db.scalars(
            select(CalendarFetchAttempt.attempted_at)
            .where(
                CalendarFetchAttempt.account_id == account_id,
                CalendarFetchAttempt.kind == "connect",
                CalendarFetchAttempt.attempted_at >= now - CALENDAR_CONNECT_WINDOW,
            )
            .order_by(CalendarFetchAttempt.attempted_at.desc())
        )
    )
    if attempts:
        latest = ensure_utc(attempts[0])
        if latest + CALENDAR_CONNECT_MIN_DELAY > now:
            raise CalendarFetchThrottled(latest + CALENDAR_CONNECT_MIN_DELAY)
    if len(attempts) >= CALENDAR_CONNECT_LIMIT:
        oldest = ensure_utc(attempts[CALENDAR_CONNECT_LIMIT - 1])
        raise CalendarFetchThrottled(oldest + CALENDAR_CONNECT_WINDOW)


def _replace_events(
    db: Session,
    subscription: CalendarSubscription,
    parsed: ParsedFeed,
) -> None:
    db.execute(delete(CalendarEvent).where(CalendarEvent.account_id == subscription.account_id))
    db.add_all(
        CalendarEvent(
            account_id=subscription.account_id,
            source_key=item.source_key,
            title=item.title,
            location=item.location,
            starts_at=item.starts_at,
            ends_at=item.ends_at,
            all_day=item.all_day,
        )
        for item in parsed.events
    )


def connect_feed(db: Session, account: Account, value: str, *, actor: str) -> dict:
    assert_connect_allowed(db, account.id)
    validated = validate_feed_url(value, account)
    digest = token_digest(validated.normalized)
    duplicate = db.scalar(
        select(CalendarSubscription.account_id).where(
            CalendarSubscription.url_digest == digest,
            CalendarSubscription.account_id != account.id,
        )
    )
    if duplicate is not None:
        raise CalendarFeedRejected("Ce lien calendrier est déjà rattaché à un autre compte")
    try:
        fetched = fetch_feed(validated)
        if fetched.body is None:
            raise CalendarFeedInvalid("Le calendrier INPASS est vide")
        parsed = parse_feed(fetched.body)
    except CalendarFeedError as exc:
        outcome = "invalid" if isinstance(
            exc,
            (CalendarFeedInvalid, CalendarFeedRejected, CalendarFeedTooLarge),
        ) else "upstream"
        _record_attempt(db, account.id, kind="connect", outcome=outcome)
        db.commit()
        raise
    now = utcnow()
    subscription = db.get(CalendarSubscription, account.id)
    if subscription is None:
        subscription = CalendarSubscription(
            account_id=account.id,
            encrypted_url="",
            url_digest=digest,
            account_hint=validated.account_hint,
            next_refresh_at=now + CALENDAR_REFRESH_INTERVAL,
        )
        db.add(subscription)
        db.flush()
    subscription.encrypted_url = cipher_for().encrypt(
        validated.normalized,
        context=f"calendar-feed:{account.id}",
    )
    subscription.url_digest = digest
    subscription.account_hint = validated.account_hint
    subscription.content_digest = hashlib.sha256(fetched.body).hexdigest()
    subscription.etag = fetched.etag
    subscription.last_modified = fetched.last_modified
    subscription.last_attempt_at = now
    subscription.last_success_at = now
    subscription.next_refresh_at = now + CALENDAR_REFRESH_INTERVAL
    subscription.last_status = "success"
    subscription.last_error_code = None
    _replace_events(db, subscription, parsed)
    _record_attempt(db, account.id, kind="connect", outcome="success")
    record_event(
        db,
        account_id=account.id,
        kind="calendar:connected",
        actor=actor,
        payload={"event_count": len(parsed.events)},
    )
    db.commit()
    return subscription_view(db, account.id)


def disconnect_feed(db: Session, account: Account, *, actor: str) -> None:
    subscription = db.get(CalendarSubscription, account.id)
    if subscription is None:
        return
    db.delete(subscription)
    record_event(
        db,
        account_id=account.id,
        kind="calendar:disconnected",
        actor=actor,
        payload={},
    )
    db.commit()


@contextmanager
def _calendar_lock(account_id: str):
    directory = get_settings_lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = Path(directory) / f"calendar-{account_id}.lock"
    with lock_path.open("w") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CalendarAlreadyRefreshing("Le calendrier est déjà en cours d'actualisation") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def get_settings_lock_dir() -> Path:
    from app.config import get_settings

    return Path(get_settings().sync_lock_dir)


def refresh_subscription(account_id: str) -> bool:
    try:
        with _calendar_lock(account_id), SessionLocal() as db:
            subscription = db.get(CalendarSubscription, account_id)
            if subscription is None:
                return False
            account = db.get(Account, account_id)
            if account is None or account.is_disabled:
                return False
            now = utcnow()
            try:
                try:
                    clear_url = cipher_for().decrypt(
                        subscription.encrypted_url,
                        context=f"calendar-feed:{account_id}",
                    )
                except RuntimeError as exc:
                    raise CalendarSecretInvalid(
                        "Le secret du calendrier doit être reconnecté"
                    ) from exc
                validated = validate_feed_url(clear_url, account)
                fetched = fetch_feed(
                    validated,
                    etag=subscription.etag,
                    last_modified=subscription.last_modified,
                )
                outcome = "not_modified"
                if fetched.body is not None:
                    content_digest = hashlib.sha256(fetched.body).hexdigest()
                    if content_digest != subscription.content_digest:
                        parsed = parse_feed(fetched.body)
                        _replace_events(db, subscription, parsed)
                        subscription.content_digest = content_digest
                        outcome = "success"
                        record_event(
                            db,
                            account_id=account_id,
                            kind="calendar:updated",
                            actor="scheduler",
                            payload={"event_count": len(parsed.events)},
                        )
                subscription.etag = fetched.etag or subscription.etag
                subscription.last_modified = fetched.last_modified or subscription.last_modified
                subscription.last_attempt_at = now
                subscription.last_success_at = now
                subscription.next_refresh_at = now + CALENDAR_REFRESH_INTERVAL
                subscription.last_status = "success"
                subscription.last_error_code = None
                _record_attempt(db, account_id, kind="automatic", outcome=outcome)
                db.commit()
                return True
            except CalendarFeedError as exc:
                subscription.last_attempt_at = now
                subscription.next_refresh_at = now + CALENDAR_REFRESH_INTERVAL
                subscription.last_status = "error"
                subscription.last_error_code = exc.code
                outcome = "invalid" if isinstance(
                    exc,
                    (CalendarFeedInvalid, CalendarFeedRejected, CalendarFeedTooLarge),
                ) else "upstream"
                _record_attempt(db, account_id, kind="automatic", outcome=outcome)
                db.commit()
                return False
    except CalendarAlreadyRefreshing:
        return False


def refresh_due_subscriptions(*, limit: int = 4) -> int:
    now = utcnow()
    with SessionLocal() as db:
        account_ids = list(
            db.scalars(
                select(CalendarSubscription.account_id)
                .join(Account, Account.id == CalendarSubscription.account_id)
                .where(
                    CalendarSubscription.next_refresh_at <= now,
                    Account.is_disabled.is_(False),
                )
                .order_by(CalendarSubscription.next_refresh_at.asc())
                .limit(limit)
            )
        )
    completed = 0
    for account_id in account_ids:
        completed += int(refresh_subscription(account_id))
    return completed


def cleanup_fetch_attempts() -> int:
    cutoff = utcnow() - CALENDAR_FETCH_ATTEMPT_RETENTION
    with SessionLocal() as db:
        result = db.execute(
            delete(CalendarFetchAttempt).where(CalendarFetchAttempt.attempted_at < cutoff)
        )
        db.commit()
        return int(result.rowcount or 0)


def subscription_view(db: Session, account_id: str) -> dict:
    subscription = db.get(CalendarSubscription, account_id)
    if subscription is None:
        return {
            "configured": False,
            "refresh_interval_minutes": 60,
            "account_hint": None,
            "last_attempt_at": None,
            "last_success_at": None,
            "next_refresh_at": None,
            "last_status": None,
            "last_error_code": None,
            "event_count": 0,
        }
    event_count = db.scalar(
        select(func.count(CalendarEvent.id)).where(CalendarEvent.account_id == account_id)
    ) or 0
    return {
        "configured": True,
        "refresh_interval_minutes": 60,
        "account_hint": subscription.account_hint,
        "last_attempt_at": (
            ensure_utc(subscription.last_attempt_at) if subscription.last_attempt_at else None
        ),
        "last_success_at": (
            ensure_utc(subscription.last_success_at) if subscription.last_success_at else None
        ),
        "next_refresh_at": ensure_utc(subscription.next_refresh_at),
        "last_status": subscription.last_status,
        "last_error_code": subscription.last_error_code,
        "event_count": event_count,
    }


def _api_event(event: CalendarEvent) -> dict:
    start = ensure_utc(event.starts_at)
    end = ensure_utc(event.ends_at)
    return {
        "id": event.id,
        "title": event.title,
        "location": event.location,
        "start": start.date().isoformat() if event.all_day else start.isoformat(),
        "end": end.date().isoformat() if event.all_day else end.isoformat(),
        "all_day": event.all_day,
    }


def event_view(
    db: Session,
    account_id: str,
    *,
    starts_at: datetime,
    ends_at: datetime,
) -> list[dict]:
    start = ensure_utc(starts_at)
    end = ensure_utc(ends_at)
    if end <= start or end - start > timedelta(days=400):
        raise CalendarFeedInvalid("La période demandée est invalide")
    events = list(
        db.scalars(
            select(CalendarEvent)
            .where(
                CalendarEvent.account_id == account_id,
                CalendarEvent.starts_at < end,
                CalendarEvent.ends_at > start,
            )
            .order_by(CalendarEvent.starts_at.asc(), CalendarEvent.ends_at.asc())
            .limit(MAX_CALENDAR_EVENTS_PER_RESPONSE + 1)
        )
    )
    if len(events) > MAX_CALENDAR_EVENTS_PER_RESPONSE:
        raise CalendarFeedTooLarge("La période contient trop d'événements")
    return [_api_event(event) for event in events]
