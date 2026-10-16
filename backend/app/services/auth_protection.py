from __future__ import annotations

import hashlib
import math
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, utcnow
from app.models import AuthAttempt, AuthThrottleState, PassSystemState

TARGET_DELAYS = (30, 120, 600)
CLIENT_DELAYS = (900, 3_600, 21_600, 86_400)
CLIENT_WINDOW = timedelta(minutes=15)
CLIENT_ATTEMPT_LIMIT = 5
TARGET_FAILURE_DECAY = timedelta(hours=24)
THROTTLE_RETENTION = timedelta(days=30)
MAX_TARGET_THROTTLE_STATES = 20_000
MAX_CLIENT_THROTTLE_STATES = 10_000
_AUTH_LOCK = threading.RLock()


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AuthProtectionRejected(RuntimeError):
    def __init__(self, available_at: datetime, *, code: str = "IMT_AUTH_COOLDOWN") -> None:
        super().__init__("Authentification IMT temporairement limitée")
        self.available_at = ensure_utc(available_at)
        self.code = code

    @property
    def retry_after_seconds(self) -> int:
        return max(1, math.ceil((self.available_at - utcnow()).total_seconds()))

    def detail(self) -> dict:
        now = utcnow()
        return {
            "code": self.code,
            "message": "Trop de tentatives. Réessaie lorsque le compteur sera terminé.",
            "retry_after_seconds": max(
                1,
                math.ceil((self.available_at - now).total_seconds()),
            ),
            "available_at": self.available_at.isoformat(),
            "server_time": now.isoformat(),
        }


def _lock_scope(db: Session, *, scope: str, reference: str) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    digest = hashlib.blake2b(f"{scope}\0{reference}".encode(), digest_size=8).digest()
    lock_id = int.from_bytes(digest, "big", signed=True)
    db.execute(select(func.pg_advisory_xact_lock(lock_id)))


def _scope_state(
    db: Session,
    *,
    scope: str,
    reference: str,
) -> AuthThrottleState:
    # A row lock cannot protect a row that does not exist yet. The transaction-level
    # advisory lock serializes the first insert across API processes as well.
    _lock_scope(db, scope=scope, reference=reference)
    statement = select(AuthThrottleState).where(
        AuthThrottleState.scope == scope,
        AuthThrottleState.reference == reference,
    )
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        statement = statement.with_for_update()
    state = db.scalar(statement)
    if state is None:
        state = AuthThrottleState(scope=scope, reference=reference)
        db.add(state)
        db.flush()
    return state


def _existing_scope_state(
    db: Session,
    *,
    scope: str,
    reference: str,
) -> AuthThrottleState | None:
    return db.scalar(
        select(AuthThrottleState).where(
            AuthThrottleState.scope == scope,
            AuthThrottleState.reference == reference,
        )
    )


def _prune_throttle_states(db: Session, now: datetime, *, scope: str) -> int:
    maximum = (
        MAX_TARGET_THROTTLE_STATES
        if scope in {"target", "target_client"}
        else MAX_CLIENT_THROTTLE_STATES
    )
    cutoff = now - THROTTLE_RETENTION
    db.execute(
        delete(AuthThrottleState).where(
            AuthThrottleState.scope == scope,
            or_(
                AuthThrottleState.blocked_until.is_(None),
                AuthThrottleState.blocked_until <= now,
            ),
            or_(
                AuthThrottleState.last_failure_at.is_(None),
                AuthThrottleState.last_failure_at < cutoff,
            ),
            or_(
                AuthThrottleState.last_success_at.is_(None),
                AuthThrottleState.last_success_at < cutoff,
            ),
            or_(
                AuthThrottleState.last_escalated_at.is_(None),
                AuthThrottleState.last_escalated_at < cutoff,
            ),
        )
    )
    db.flush()
    count = db.scalar(
        select(func.count(AuthThrottleState.id)).where(AuthThrottleState.scope == scope)
    ) or 0
    overflow = count - maximum + 1
    if overflow <= 0:
        return count
    stale_ids = (
        select(AuthThrottleState.id)
        .where(
            AuthThrottleState.scope == scope,
            or_(
                AuthThrottleState.blocked_until.is_(None),
                AuthThrottleState.blocked_until <= now,
            )
        )
        .order_by(
            func.coalesce(
                AuthThrottleState.last_escalated_at,
                AuthThrottleState.last_failure_at,
                AuthThrottleState.last_success_at,
            ).asc(),
            AuthThrottleState.id.asc(),
        )
        .limit(overflow)
    )
    db.execute(delete(AuthThrottleState).where(AuthThrottleState.id.in_(stale_ids)))
    db.flush()
    return db.scalar(
        select(func.count(AuthThrottleState.id)).where(AuthThrottleState.scope == scope)
    ) or 0


def _target_client_reference(target_ref: str, client_ref: str) -> str:
    return f"{target_ref[:31]}:{client_ref[:32]}"


def _target_client_prefix(target_ref: str) -> str:
    return f"{target_ref[:31]}:"


def assert_auth_allowed(*, target_ref: str, client_ref: str) -> None:
    now = utcnow()
    with _AUTH_LOCK, SessionLocal() as db:
        target_client = _existing_scope_state(
            db,
            scope="target_client",
            reference=_target_client_reference(target_ref, client_ref),
        )
        client = _existing_scope_state(db, scope="client", reference=client_ref)
        system = db.get(PassSystemState, 1)
        blocked_until = [
            value
            for value in (
                ensure_utc(target_client.blocked_until) if target_client is not None else None,
                ensure_utc(client.blocked_until) if client is not None else None,
                ensure_utc(system.auth_blocked_until) if system is not None else None,
            )
            if value is not None and value > now
        ]
        if blocked_until:
            raise AuthProtectionRejected(max(blocked_until))

        recent_attempts = db.scalar(
            select(func.count(AuthAttempt.id)).where(
                AuthAttempt.client_ref == client_ref,
                AuthAttempt.attempted_at >= now - CLIENT_WINDOW,
                AuthAttempt.outcome.in_(["success", "invalid"]),
            )
        )
        if (recent_attempts or 0) >= CLIENT_ATTEMPT_LIMIT:
            state_count = _prune_throttle_states(db, now, scope="client")
            if (
                client is None
                and state_count >= MAX_CLIENT_THROTTLE_STATES
            ):
                raise AuthProtectionRejected(now + timedelta(seconds=CLIENT_DELAYS[0]))
            client = _scope_state(db, scope="client", reference=client_ref)
            recent_escalation = ensure_utc(client.last_escalated_at)
            if recent_escalation is None or recent_escalation < now - timedelta(days=7):
                client.escalation_level = 0
            else:
                client.escalation_level = min(
                    client.escalation_level + 1,
                    len(CLIENT_DELAYS) - 1,
                )
            client.last_escalated_at = now
            client.blocked_until = now + timedelta(
                seconds=CLIENT_DELAYS[client.escalation_level]
            )
            db.commit()
            raise AuthProtectionRejected(ensure_utc(client.blocked_until))


def record_auth_outcome(*, target_ref: str, client_ref: str, outcome: str) -> None:
    if outcome not in {"success", "invalid", "upstream"}:
        raise ValueError("Résultat d'authentification inconnu")
    now = utcnow()
    with _AUTH_LOCK, SessionLocal() as db:
        db.add(
            AuthAttempt(
                target_ref=target_ref,
                client_ref=client_ref,
                outcome=outcome,
                attempted_at=now,
            )
        )
        references = (
            ("target", target_ref),
            ("target_client", _target_client_reference(target_ref, client_ref)),
        )
        for scope, reference in references:
            state = _existing_scope_state(db, scope=scope, reference=reference)
            if outcome == "invalid" and state is None:
                state_count = _prune_throttle_states(db, now, scope=scope)
                if state_count < MAX_TARGET_THROTTLE_STATES:
                    state = _scope_state(db, scope=scope, reference=reference)
            if outcome == "success" and state is not None:
                state.consecutive_failures = 0
                state.blocked_until = None
                state.last_success_at = now
            elif outcome == "invalid" and state is not None:
                last_failure = ensure_utc(state.last_failure_at)
                if last_failure is None or last_failure < now - TARGET_FAILURE_DECAY:
                    state.consecutive_failures = 0
                state.consecutive_failures += 1
                delay_index = min(state.consecutive_failures - 1, len(TARGET_DELAYS) - 1)
                state.blocked_until = now + timedelta(seconds=TARGET_DELAYS[delay_index])
                state.last_failure_at = now

        db.flush()
        if outcome == "upstream":
            window_start = now - timedelta(minutes=15)
            total = db.scalar(
                select(func.count(AuthAttempt.id)).where(
                    AuthAttempt.outcome == "upstream",
                    AuthAttempt.attempted_at >= window_start,
                )
            )
            targets = db.scalar(
                select(func.count(func.distinct(AuthAttempt.target_ref))).where(
                    AuthAttempt.outcome == "upstream",
                    AuthAttempt.attempted_at >= window_start,
                )
            )
            clients = db.scalar(
                select(func.count(func.distinct(AuthAttempt.client_ref))).where(
                    AuthAttempt.outcome == "upstream",
                    AuthAttempt.attempted_at >= window_start,
                )
            )
            if (total or 0) >= 10 and (targets or 0) >= 6 and (clients or 0) >= 3:
                system = db.get(PassSystemState, 1)
                if system is None:
                    system = PassSystemState(id=1)
                    db.add(system)
                system.auth_blocked_until = now + timedelta(minutes=30)
                system.auth_block_reason = "distributed_upstream_failures"
        db.commit()


def clear_target_cooldown(db: Session, target_ref: str) -> None:
    state = db.scalar(
        select(AuthThrottleState).where(
            AuthThrottleState.scope == "target",
            AuthThrottleState.reference == target_ref,
        )
    )
    if state is not None:
        state.consecutive_failures = 0
        state.blocked_until = None
        state.last_failure_at = None
    db.execute(
        delete(AuthThrottleState).where(
            AuthThrottleState.scope == "target_client",
            AuthThrottleState.reference.like(f"{_target_client_prefix(target_ref)}%"),
        )
    )


def auth_throttle_view(db: Session, target_ref: str) -> dict:
    now = utcnow()
    state = db.scalar(
        select(AuthThrottleState).where(
            AuthThrottleState.scope == "target",
            AuthThrottleState.reference == target_ref,
        )
    )
    combined_blocked_values = db.scalars(
        select(AuthThrottleState.blocked_until).where(
            AuthThrottleState.scope == "target_client",
            AuthThrottleState.reference.like(f"{_target_client_prefix(target_ref)}%"),
            AuthThrottleState.blocked_until.is_not(None),
        )
    ).all()
    blocked_values = [ensure_utc(value) for value in combined_blocked_values]
    blocked_until = max((value for value in blocked_values if value is not None), default=None)
    return {
        "consecutive_failures": state.consecutive_failures if state is not None else 0,
        "blocked_until": blocked_until,
        "blocked": bool(blocked_until and blocked_until > now),
        "retry_after_seconds": (
            max(0, math.ceil((blocked_until - now).total_seconds()))
            if blocked_until
            else 0
        ),
    }
