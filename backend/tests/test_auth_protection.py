from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.database import SessionLocal
from app.models import AuthThrottleState, PassSystemState
from app.services import auth_protection
from app.services.auth_protection import (
    AuthProtectionRejected,
    assert_auth_allowed,
    record_auth_outcome,
)
from sqlalchemy import func, select

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def throttle(scope: str, reference: str) -> AuthThrottleState:
    with SessionLocal() as db:
        state = db.scalar(
            select(AuthThrottleState).where(
                AuthThrottleState.scope == scope,
                AuthThrottleState.reference == reference,
            )
        )
        assert state is not None
        db.expunge(state)
        return state


def clear_block(scope: str, reference: str) -> None:
    with SessionLocal() as db:
        state = db.scalar(
            select(AuthThrottleState).where(
                AuthThrottleState.scope == scope,
                AuthThrottleState.reference == reference,
            )
        )
        assert state is not None
        state.blocked_until = None
        db.commit()


def test_target_cooldown_progresses_and_success_resets(monkeypatch) -> None:
    monkeypatch.setattr(auth_protection, "utcnow", lambda: NOW)
    target = "target-reference"
    client = "client-reference"

    for index, expected_seconds in enumerate(auth_protection.TARGET_DELAYS, start=1):
        record_auth_outcome(target_ref=target, client_ref=client, outcome="invalid")
        state = throttle("target", target)
        assert state.consecutive_failures == index
        assert auth_protection.ensure_utc(state.blocked_until) == NOW + timedelta(
            seconds=expected_seconds
        )
        clear_block("target", target)

    for _ in range(3):
        record_auth_outcome(target_ref=target, client_ref=client, outcome="invalid")
        state = throttle("target", target)
        assert auth_protection.ensure_utc(state.blocked_until) == NOW + timedelta(minutes=10)
        clear_block("target", target)

    record_auth_outcome(target_ref=target, client_ref=client, outcome="success")
    state = throttle("target", target)
    assert state.consecutive_failures == 0
    assert state.blocked_until is None


def test_allowed_preflight_does_not_persist_attacker_selected_state(monkeypatch) -> None:
    monkeypatch.setattr(auth_protection, "utcnow", lambda: NOW)
    for index in range(20):
        assert_auth_allowed(
            target_ref=f"never-attempted-target-{index}",
            client_ref=f"never-attempted-client-{index}",
        )
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AuthThrottleState.id))) == 0


def test_target_throttle_state_has_a_hard_cardinality_cap(monkeypatch) -> None:
    monkeypatch.setattr(auth_protection, "utcnow", lambda: NOW)
    monkeypatch.setattr(auth_protection, "MAX_TARGET_THROTTLE_STATES", 2)
    for index in range(4):
        record_auth_outcome(
            target_ref=f"rotating-target-{index}",
            client_ref="bounded-client",
            outcome="invalid",
        )
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count(AuthThrottleState.id)).where(
                AuthThrottleState.scope == "target"
            )
        )
    assert count == 2


def test_client_limit_counts_real_authentications_but_not_outages(monkeypatch) -> None:
    monkeypatch.setattr(auth_protection, "utcnow", lambda: NOW)
    client = "shared-client"
    for index in range(5):
        record_auth_outcome(
            target_ref=f"successful-target-{index}",
            client_ref=client,
            outcome="success",
        )

    with pytest.raises(AuthProtectionRejected) as rejected:
        assert_auth_allowed(target_ref="next-target", client_ref=client)
    assert rejected.value.retry_after_seconds == 900

    outage_client = "outage-client"
    for index in range(12):
        record_auth_outcome(
            target_ref=f"outage-target-{index}",
            client_ref=outage_client,
            outcome="upstream",
        )
    assert_auth_allowed(target_ref="healthy-target", client_ref=outage_client)


def test_distributed_invalid_credentials_suspend_new_imt_auth(monkeypatch) -> None:
    monkeypatch.setattr(auth_protection, "utcnow", lambda: NOW)
    for index in range(10):
        record_auth_outcome(
            target_ref=f"target-{index % 6}",
            client_ref=f"client-{index % 3}",
            outcome="invalid",
        )

    with SessionLocal() as db:
        system = db.get(PassSystemState, 1)
        assert system is not None
        assert system.auth_block_reason == "distributed_invalid_credentials"

    with pytest.raises(AuthProtectionRejected):
        assert_auth_allowed(target_ref="unrelated-target", client_ref="unrelated-client")
