from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.database import SessionLocal, utcnow
from app.models import (
    Account,
    CalendarEvent,
    CalendarFetchAttempt,
    CalendarSubscription,
)
from app.security import cipher_for
from app.services import calendar_feed
from app.services.imt import ImtPassClient, PassEntry, PassProfile
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.conftest import csrf_headers

VALID_URL = (
    "https://inpass.imt-atlantique.fr/passcal/getics"
    "?login=calendar@imta.fr&check=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)
VALID_NORMALIZED_URL = VALID_URL.replace("calendar@imta.fr", "calendar%40imta.fr")

CALENDAR_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//IMTegrale tests//FR
X-WR-TIMEZONE:Europe/Paris
BEGIN:VEVENT
UID:course-1@example.test
DTSTART;TZID=Europe/Paris:20260901T080000
DTEND;TZID=Europe/Paris:20260901T100000
SUMMARY:R\xc3\xa9seaux avanc\xc3\xa9s
LOCATION:B02-134
END:VEVENT
BEGIN:VEVENT
UID:all-day@example.test
DTSTART;VALUE=DATE:20260903
DTEND;VALUE=DATE:20260904
SUMMARY:Journ\xc3\xa9e d'int\xc3\xa9gration
END:VEVENT
BEGIN:VEVENT
UID:recurring@example.test
DTSTART;TZID=Europe/Paris:20260907T140000
DTEND;TZID=Europe/Paris:20260907T160000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Projet collectif
LOCATION:FabLab
END:VEVENT
END:VCALENDAR
"""


def calendar_notes(self: ImtPassClient, _username: str, _password: str) -> list[PassEntry]:
    self.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=2028,
        first_name="Calendar",
        last_name="STUDENT",
    )
    return [PassEntry("RES110", "Examen", 15, 1, False)]


def login_calendar_owner(client: TestClient, monkeypatch) -> dict:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", calendar_notes)
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "calendar@imt-atlantique.fr", "password": "correct-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def fetched_calendar() -> calendar_feed.FetchResult:
    return calendar_feed.FetchResult(
        body=CALENDAR_ICS,
        etag='"calendar-v1"',
        last_modified="Wed, 15 Jul 2026 08:00:00 GMT",
        not_modified=False,
        upstream_status=200,
    )


def test_feed_url_validation_is_exact_and_account_bound() -> None:
    account = Account(
        imt_username="calendar@imt-atlantique.fr",
        display_name="Calendar",
    )
    validated = calendar_feed.validate_feed_url(VALID_URL, account)

    assert validated.normalized == VALID_NORMALIZED_URL
    assert validated.account_hint == "ca******@imta.fr"

    rejected = [
        VALID_URL.replace("https://", "http://"),
        VALID_URL.replace("inpass.imt-atlantique.fr", "inpass.imt-atlantique.fr.evil.test"),
        VALID_URL.replace("/passcal/getics", "/passcal/getics/"),
        f"{VALID_URL}&next=https://evil.test",
        f"{VALID_URL}#secret",
        VALID_URL.replace("check=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "check=short"),
        VALID_URL.replace("login=calendar", "login=calendar&login=calendar"),
        VALID_URL.replace("https://", "https://calendar:secret@"),
    ]
    for value in rejected:
        with pytest.raises(calendar_feed.CalendarUrlInvalid):
            calendar_feed.validate_feed_url(value, account)

    with pytest.raises(calendar_feed.CalendarUrlMismatch):
        calendar_feed.validate_feed_url(
            VALID_URL.replace("calendar@imta.fr", "another@imta.fr"),
            account,
        )


def test_feed_parser_expands_safe_recurrences_and_normalizes_dates() -> None:
    parsed = calendar_feed.parse_feed(
        CALENDAR_ICS,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )

    assert len(parsed.events) == 5
    assert parsed.ignored_count == 0
    first = parsed.events[0]
    assert first.title == "Réseaux avancés"
    assert first.location == "B02-134"
    assert first.starts_at == datetime(2026, 9, 1, 6, tzinfo=UTC)
    assert first.ends_at == datetime(2026, 9, 1, 8, tzinfo=UTC)
    all_day = next(event for event in parsed.events if event.title.startswith("Journée"))
    assert all_day.all_day is True
    assert all_day.starts_at.date().isoformat() == "2026-09-03"
    assert len({event.source_key for event in parsed.events}) == 5


def test_feed_parser_rejects_high_frequency_recurrences() -> None:
    hostile = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//IMTegrale tests//FR
BEGIN:VEVENT
UID:hostile@example.test
DTSTART:20260901T080000Z
DTEND:20260901T090000Z
RRULE:FREQ=HOURLY;COUNT=100
SUMMARY:Hostile
END:VEVENT
END:VCALENDAR
"""

    with pytest.raises(calendar_feed.CalendarFeedInvalid):
        calendar_feed.parse_feed(hostile, now=datetime(2026, 7, 18, tzinfo=UTC))


def test_calendar_api_encrypts_secret_and_is_private_to_primary_owner(
    client: TestClient,
    monkeypatch,
) -> None:
    session = login_calendar_owner(client, monkeypatch)
    monkeypatch.setattr(calendar_feed, "fetch_feed", lambda *_args, **_kwargs: fetched_calendar())

    connected = client.put(
        "/api/v1/calendar/subscription",
        json={"url": VALID_URL},
        headers=csrf_headers(client),
    )

    assert connected.status_code == 200, connected.text
    body = connected.json()
    assert body["configured"] is True
    assert body["event_count"] == 5
    assert body["fip_training_available"] is True
    assert body["promotion_year"] == 2028
    assert "url" not in body
    assert "check" not in connected.text

    with SessionLocal() as db:
        subscription = db.get(CalendarSubscription, session["account"]["id"])
        assert subscription is not None
        assert VALID_URL not in subscription.encrypted_url
        assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in subscription.encrypted_url
        assert cipher_for().decrypt(
            subscription.encrypted_url,
            context=f"calendar-feed:{session['account']['id']}",
        ) == VALID_NORMALIZED_URL

    events = client.get(
        "/api/v1/calendar/events",
        params={"start": "2026-08-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
    )
    assert events.status_code == 200
    assert len(events.json()) == 5
    assert set(events.json()[0]) == {"id", "title", "location", "start", "end", "all_day"}

    token = client.post(
        "/api/v1/tokens",
        json={"name": "Agenda lecture", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert token.status_code == 201
    delegated = TestClient(client.app, base_url="https://testserver")
    assert delegated.post(
        "/api/v1/auth/login/token",
        json={"token": token.json()["token"]},
    ).status_code == 200
    assert delegated.get("/api/v1/calendar/status").status_code == 403
    assert delegated.get("/api/v1/calendar/training").status_code == 403

    removed = client.delete(
        "/api/v1/calendar/subscription",
        headers=csrf_headers(client),
    )
    assert removed.status_code == 204
    with SessionLocal() as db:
        assert db.get(CalendarSubscription, session["account"]["id"]) is None
        assert db.scalar(
            select(func.count(CalendarEvent.id)).where(
                CalendarEvent.account_id == session["account"]["id"]
            )
        ) == 0


def test_calendar_connection_failures_are_throttled(
    client: TestClient,
    monkeypatch,
) -> None:
    login_calendar_owner(client, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise calendar_feed.CalendarFeedUnavailable("INPASS indisponible")

    monkeypatch.setattr(calendar_feed, "fetch_feed", unavailable)
    first = client.put(
        "/api/v1/calendar/subscription",
        json={"url": VALID_URL},
        headers=csrf_headers(client),
    )
    second = client.put(
        "/api/v1/calendar/subscription",
        json={"url": VALID_URL},
        headers=csrf_headers(client),
    )

    assert first.status_code == 502
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) > 0
    with SessionLocal() as db:
        attempts = list(db.scalars(select(CalendarFetchAttempt)))
        assert len(attempts) == 1
        assert attempts[0].outcome == "upstream"


def test_hourly_refresh_preserves_cached_events_on_304_and_errors(
    client: TestClient,
    monkeypatch,
) -> None:
    session = login_calendar_owner(client, monkeypatch)
    monkeypatch.setattr(calendar_feed, "fetch_feed", lambda *_args, **_kwargs: fetched_calendar())
    assert client.put(
        "/api/v1/calendar/subscription",
        json={"url": VALID_URL},
        headers=csrf_headers(client),
    ).status_code == 200

    monkeypatch.setattr(
        calendar_feed,
        "fetch_feed",
        lambda *_args, **_kwargs: calendar_feed.FetchResult(
            body=None,
            etag='"calendar-v1"',
            last_modified="Wed, 15 Jul 2026 08:00:00 GMT",
            not_modified=True,
            upstream_status=304,
        ),
    )
    assert calendar_feed.refresh_subscription(session["account"]["id"]) is True

    def unavailable(*_args, **_kwargs):
        raise calendar_feed.CalendarFeedUnavailable("INPASS indisponible")

    monkeypatch.setattr(calendar_feed, "fetch_feed", unavailable)
    assert calendar_feed.refresh_subscription(session["account"]["id"]) is False

    with SessionLocal() as db:
        subscription = db.get(CalendarSubscription, session["account"]["id"])
        assert subscription is not None
        assert subscription.last_status == "error"
        assert subscription.last_error_code == "CALENDAR_UPSTREAM_UNAVAILABLE"
        assert db.scalar(
            select(func.count(CalendarEvent.id)).where(
                CalendarEvent.account_id == session["account"]["id"]
            )
        ) == 5


def test_fip_training_calendar_contains_all_promotions_and_official_locations(
    client: TestClient,
    monkeypatch,
) -> None:
    login_calendar_owner(client, monkeypatch)

    response = client.get("/api/v1/calendar/training")

    assert response.status_code == 200
    calendar = response.json()
    assert calendar["default_promotion_year"] == 2028
    promotions = {item["promotion_year"]: item for item in calendar["promotions"]}
    assert set(promotions) == {2027, 2028, 2029}
    assert promotions[2029]["totals"] == {"school_weeks": 23, "company_weeks": 29}
    assert promotions[2028]["totals"] == {"school_weeks": 23, "company_weeks": 30}
    assert promotions[2027]["totals"] == {"school_weeks": 23, "company_weeks": 28}
    assert [
        period["campus"]
        for promotion in promotions.values()
        for period in promotion["periods"]
        if period["campus"]
    ] == ["Rennes", "Brest"]
    assert promotions[2029]["semesters"][0]["semester"] == "S5"
    assert promotions[2027]["semesters"][-1]["semester"] == "S10"

    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.imt_username.like("calendar%")))
        assert account is not None
        account.program = "FISE"
        db.commit()
    assert client.get("/api/v1/calendar/training").status_code == 404


def test_connect_limit_is_hourly_per_account() -> None:
    account = Account(
        imt_username="calendar@imt-atlantique.fr",
        display_name="Calendar",
    )
    with SessionLocal() as db:
        db.add(account)
        db.flush()
        now = utcnow()
        db.add_all(
            CalendarFetchAttempt(
                account_id=account.id,
                kind="connect",
                outcome="invalid",
                attempted_at=now - timedelta(minutes=offset),
            )
            for offset in (5, 15, 30)
        )
        db.commit()

        with pytest.raises(calendar_feed.CalendarFetchThrottled) as exc_info:
            calendar_feed.assert_connect_allowed(db, account.id)

    assert exc_info.value.available_at > now


def test_calendar_url_digest_is_unique_across_accounts() -> None:
    first = Account(
        imt_username="calendar@imt-atlantique.fr",
        display_name="Calendar",
    )
    second = Account(
        imt_username="second@imt-atlantique.fr",
        display_name="Second",
    )
    with SessionLocal() as db:
        db.add_all((first, second))
        db.flush()
        db.add(
            CalendarSubscription(
                account_id=first.id,
                encrypted_url="encrypted",
                url_digest="a" * 64,
                account_hint="ca***@imta.fr",
                next_refresh_at=utcnow(),
            )
        )
        db.commit()
        db.add(
            CalendarSubscription(
                account_id=second.id,
                encrypted_url="other-encrypted",
                url_digest="a" * 64,
                account_hint="se***@imta.fr",
                next_refresh_at=utcnow(),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
