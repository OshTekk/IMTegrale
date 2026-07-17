from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.database import SessionLocal
from app.models import Account
from app.services import sync as sync_service
from app.services.cohort_pulse import emit_cohort_pulse
from app.services.imt import ImtPassClient, PassEntry
from app.services.sync_schedule import (
    account_zone,
    auto_sync_is_due,
    automatic_lateness_ratio,
    effective_auto_sync_interval,
    ensure_utc,
    next_auto_sync_at,
    update_adaptive_cadence,
)
from fastapi.testclient import TestClient

from tests.conftest import csrf_headers


def account_for_schedule(**values) -> Account:
    defaults = {
        "imt_username": "schedule@imt-atlantique.fr",
        "display_name": "Schedule",
        "encrypted_imt_password": "encrypted",
        "timezone": "Europe/Paris",
        "auto_sync_enabled": True,
        "auto_sync_interval_hours": 2,
        "auto_sync_adaptive": True,
        "auto_sync_current_interval_hours": 2,
        "auto_sync_no_change_streak": 0,
        "auto_sync_consented_at": datetime(2026, 7, 1, tzinfo=UTC),
        "is_disabled": False,
    }
    defaults.update(values)
    return Account(**defaults)


def test_auto_sync_requires_consent_interval_and_business_window() -> None:
    thursday_10_local = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    account = account_for_schedule(last_sync_at=thursday_10_local - timedelta(hours=2))

    assert auto_sync_is_due(account, thursday_10_local) is True
    account.auto_sync_enabled = False
    assert auto_sync_is_due(account, thursday_10_local) is False
    account.auto_sync_enabled = True
    account.auto_sync_consented_at = None
    assert auto_sync_is_due(account, thursday_10_local) is False
    account.auto_sync_consented_at = thursday_10_local - timedelta(days=1)
    assert auto_sync_is_due(account, datetime(2026, 7, 16, 19, 0, tzinfo=UTC)) is False
    assert auto_sync_is_due(account, datetime(2026, 7, 18, 10, 0, tzinfo=UTC)) is False


def test_next_auto_sync_moves_to_next_business_day() -> None:
    friday_after_hours = datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    account = account_for_schedule(last_sync_at=friday_after_hours - timedelta(hours=4))

    next_run = next_auto_sync_at(account, friday_after_hours)

    assert next_run == datetime(2026, 7, 20, 6, 0, tzinfo=UTC)


def test_invalid_stored_timezone_is_contained_and_api_rejects_new_invalid_value(
    client: TestClient,
    monkeypatch,
) -> None:
    account = account_for_schedule(timezone="bad\x00zone")
    assert account_zone(account).key == "Europe/Paris"
    assert auto_sync_is_due(account, datetime(2026, 7, 16, 8, 0, tzinfo=UTC)) is True

    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    assert client.post(
        "/api/v1/auth/login/imt",
        json={"username": "timezone@imt-atlantique.fr", "password": "correct-password"},
    ).status_code == 200
    rejected = client.patch(
        "/api/v1/settings/account",
        json={"timezone": "bad\u0000zone"},
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 422


def test_adaptive_cadence_slows_after_three_unchanged_automatic_runs() -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    account = account_for_schedule(last_sync_at=now)

    for _ in range(3):
        update_adaptive_cadence(account, changed=False, actor="automatic", now=now)
    assert effective_auto_sync_interval(account) == 4
    assert account.auto_sync_no_change_streak == 0

    for _ in range(3):
        update_adaptive_cadence(account, changed=False, actor="automatic", now=now)
    assert effective_auto_sync_interval(account) == 6

    update_adaptive_cadence(account, changed=True, actor="automatic", now=now)
    assert effective_auto_sync_interval(account) == 2
    assert account.auto_sync_no_change_streak == 0


def test_fixed_cadence_and_manual_no_change_do_not_adapt() -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    adaptive = account_for_schedule(last_sync_at=now)
    update_adaptive_cadence(adaptive, changed=False, actor="owner", now=now)
    assert adaptive.auto_sync_no_change_streak == 0
    assert effective_auto_sync_interval(adaptive) == 2

    fixed = account_for_schedule(
        last_sync_at=now,
        auto_sync_interval_hours=4,
        auto_sync_adaptive=False,
        auto_sync_current_interval_hours=24,
    )
    update_adaptive_cadence(fixed, changed=False, actor="automatic", now=now)
    assert effective_auto_sync_interval(fixed) == 4
    assert fixed.auto_sync_current_interval_hours == 4


def test_lateness_ratio_keeps_elapsed_delay_inside_business_window() -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    account = account_for_schedule(last_sync_at=now - timedelta(hours=6))

    assert automatic_lateness_ratio(account, now) == 2.0


def test_cohort_pulse_resets_only_same_promotion_adaptive_opt_ins() -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        source = account_for_schedule(
            imt_username="source@imt-atlantique.fr",
            program="FIP",
            promotion_year=2028,
        )
        target = account_for_schedule(
            imt_username="target@imt-atlantique.fr",
            program="FIP",
            promotion_year=2028,
            auto_sync_current_interval_hours=24,
            auto_sync_no_change_streak=2,
            auto_sync_next_at=now + timedelta(hours=20),
        )
        other_promotion = account_for_schedule(
            imt_username="other@imt-atlantique.fr",
            program="FIP",
            promotion_year=2029,
            auto_sync_current_interval_hours=24,
        )
        fixed = account_for_schedule(
            imt_username="fixed@imt-atlantique.fr",
            program="FIP",
            promotion_year=2028,
            auto_sync_adaptive=False,
            auto_sync_current_interval_hours=24,
        )
        db.add_all([source, target, other_promotion, fixed])
        db.flush()
        target_id = target.id
        other_id = other_promotion.id
        fixed_id = fixed.id

        assert emit_cohort_pulse(db, source, now=now) == 1
        db.commit()

    with SessionLocal() as db:
        target = db.get(Account, target_id)
        other_promotion = db.get(Account, other_id)
        fixed = db.get(Account, fixed_id)
        assert target is not None and other_promotion is not None and fixed is not None
        assert target.auto_sync_current_interval_hours == 2
        assert target.auto_sync_no_change_streak == 0
        target_next = ensure_utc(target.auto_sync_next_at)
        assert now <= target_next <= now + timedelta(hours=2)
        assert other_promotion.auto_sync_current_interval_hours == 24
        assert fixed.auto_sync_current_interval_hours == 24


def fake_notes(_self: ImtPassClient, _username: str, _password: str) -> list[PassEntry]:
    return [PassEntry("SIT130", "Examen", 15, 1, False)]


def test_auto_sync_setting_is_disabled_by_default_and_requires_owner_csrf(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    assert client.post(
        "/api/v1/auth/login/imt",
        json={"username": "owner@imt-atlantique.fr", "password": "correct-password"},
    ).status_code == 200

    initial = client.get("/api/v1/settings").json()["sync"]
    rejected = client.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": True, "interval_hours": 2},
    )
    invalid = client.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": True, "interval_hours": 1},
        headers=csrf_headers(client),
    )
    enabled = client.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": True, "interval_hours": 4},
        headers=csrf_headers(client),
    )
    disabled = client.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": False, "interval_hours": 4},
        headers=csrf_headers(client),
    )

    assert initial["enabled"] is False
    assert initial["interval_hours"] == 2
    assert rejected.status_code == 403
    assert invalid.status_code == 422
    assert enabled.json()["sync"]["enabled"] is True
    assert enabled.json()["sync"]["consented_at"] is not None
    assert disabled.json()["sync"]["enabled"] is False
    assert disabled.json()["sync"]["consented_at"] is None


def test_worker_selects_only_due_consenting_accounts(monkeypatch) -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        due = account_for_schedule(
            imt_username="due@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=2),
        )
        disabled = account_for_schedule(
            imt_username="disabled@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=4),
            is_disabled=True,
        )
        not_consented = account_for_schedule(
            imt_username="off@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=4),
            auto_sync_enabled=False,
            auto_sync_consented_at=None,
        )
        too_recent = account_for_schedule(
            imt_username="recent@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=1),
        )
        db.add_all([due, disabled, not_consented, too_recent])
        db.commit()
        due_id = due.id

    monkeypatch.setattr(sync_service, "utcnow", lambda: now)
    called: list[tuple[str, str]] = []

    def fake_sync(account_id: str, *, notify: bool = True, actor: str = "system") -> dict:
        called.append((account_id, actor))
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sync_service, "sync_account", fake_sync)

    results = sync_service.sync_due_accounts()

    assert called == [(due_id, "automatic")]
    assert results == [
        {"account_id": due_id, "ok": True, "total": 0, "inserted": 0, "updated": 0}
    ]


def test_worker_rotates_after_a_terminal_failure(monkeypatch) -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        first = account_for_schedule(
            imt_username="first-failure@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=4),
        )
        second = account_for_schedule(
            imt_username="second-after-failure@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=4),
        )
        db.add_all([first, second])
        db.flush()
        first_id = first.id
        second_id = second.id
        db.commit()

    monkeypatch.setattr(sync_service, "utcnow", lambda: now)
    called: list[str] = []

    def failing_sync(account_id: str, *, notify: bool = True, actor: str = "system") -> dict:
        called.append(account_id)
        raise RuntimeError("upstream failed")

    monkeypatch.setattr(sync_service, "sync_account", failing_sync)
    sync_service.sync_due_accounts()
    sync_service.sync_due_accounts()

    assert called == [first_id, second_id]


def test_automatic_sync_rechecks_consent_after_worker_selection(monkeypatch) -> None:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        account = account_for_schedule(
            imt_username="revoked@imt-atlantique.fr",
            last_sync_at=now - timedelta(hours=3),
        )
        db.add(account)
        db.flush()
        account_id = account.id
        account.auto_sync_enabled = False
        account.auto_sync_consented_at = None
        db.commit()

    monkeypatch.setattr(sync_service, "utcnow", lambda: now)
    with pytest.raises(sync_service.AutomaticSyncNotAllowed):
        sync_service.sync_account(account_id, actor="automatic")
