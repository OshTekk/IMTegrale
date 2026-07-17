from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "auto_sync_interval_hours IN (2, 4, 6, 8, 12, 24)",
            name="ck_accounts_auto_sync_interval",
        ),
        CheckConstraint(
            "auto_sync_current_interval_hours IN (2, 4, 6, 8, 12, 24)",
            name="ck_accounts_auto_sync_current_interval",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    imt_username: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    encrypted_imt_password: Mapped[str] = mapped_column(Text)
    encrypted_telegram_token: Mapped[str | None] = mapped_column(Text)
    encrypted_telegram_chat_id: Mapped[str | None] = mapped_column(Text)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_last_test_status: Mapped[str | None] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Paris")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credentials_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str] = mapped_column(String(32), default="never")
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=2)
    auto_sync_adaptive: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_sync_current_interval_hours: Mapped[int] = mapped_column(Integer, default=2)
    auto_sync_no_change_streak: Mapped[int] = mapped_column(Integer, default=0)
    auto_sync_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_sync_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_active_request_id: Mapped[str | None] = mapped_column(String(36))
    sync_active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(String(240))
    detected_campus: Mapped[str] = mapped_column(String(16), default="unknown")
    detected_campus_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_refresh_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_first_name: Mapped[str | None] = mapped_column(String(120))
    official_last_name: Mapped[str | None] = mapped_column(String(120))
    official_identity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campus: Mapped[str] = mapped_column(String(16), default="unknown")
    campus_source: Mapped[str] = mapped_column(String(24), default="unknown")
    campus_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cohort: Mapped[str] = mapped_column(String(16), default="unknown")
    cohort_source: Mapped[str] = mapped_column(String(24), default="unknown")
    cohort_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_program: Mapped[str] = mapped_column(String(32), default="unknown")
    detected_promotion_year: Mapped[int | None] = mapped_column(Integer)
    program: Mapped[str] = mapped_column(String(32), default="unknown")
    promotion_year: Mapped[int | None] = mapped_column(Integer)
    academic_source: Mapped[str] = mapped_column(String(24), default="unknown")
    academic_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    last_note_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    notes: Mapped[list[Note]] = relationship(back_populates="account", cascade="all, delete-orphan")
    ue_settings: Mapped[list[UeSetting]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    leaderboard_profile: Mapped[LeaderboardProfile | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        UniqueConstraint("account_id", "source", "source_key", name="uq_notes_account_source_key"),
        Index("ix_notes_account_ue", "account_id", "ue_code"),
        Index("ix_notes_account_archived", "account_id", "archived"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(96))
    ue_code: Mapped[str] = mapped_column(String(32))
    raw_label: Mapped[str] = mapped_column(String(240))
    raw_score: Mapped[float] = mapped_column(Float)
    raw_coefficient: Mapped[float] = mapped_column(Float, default=1)
    raw_is_resit: Mapped[bool] = mapped_column(Boolean, default=False)
    label_override: Mapped[str | None] = mapped_column(String(240))
    score_override: Mapped[float | None] = mapped_column(Float)
    coefficient_override: Mapped[float | None] = mapped_column(Float)
    is_resit_override: Mapped[bool | None] = mapped_column(Boolean)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_by_user: Mapped[bool] = mapped_column(Boolean, default=False)

    account: Mapped[Account] = relationship(back_populates="notes")

    @property
    def label(self) -> str:
        return self.label_override if self.label_override is not None else self.raw_label

    @property
    def score(self) -> float:
        return self.score_override if self.score_override is not None else self.raw_score

    @property
    def coefficient(self) -> float:
        return self.coefficient_override if self.coefficient_override is not None else self.raw_coefficient

    @property
    def is_resit(self) -> bool:
        return self.is_resit_override if self.is_resit_override is not None else self.raw_is_resit

    @property
    def has_override(self) -> bool:
        return any(
            value is not None
            for value in (
                self.label_override,
                self.score_override,
                self.coefficient_override,
                self.is_resit_override,
            )
        )


class UeSetting(Base):
    __tablename__ = "ue_settings"
    __table_args__ = (UniqueConstraint("account_id", "code", name="uq_ue_settings_account_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    credits_ects: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(200), default="")
    year: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="ue_settings")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_account_id_id", "account_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncRequest(Base):
    __tablename__ = "sync_requests"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "idempotency_digest",
            name="uq_sync_requests_account_idempotency",
        ),
        Index("ix_sync_requests_account_status", "account_id", "status"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')",
            name="ck_sync_requests_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    idempotency_digest: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON)


class PassSystemState(Base):
    __tablename__ = "pass_system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_operation_id: Mapped[str | None] = mapped_column(String(36))
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quiet_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    circuit_state: Mapped[str] = mapped_column(String(16), default="closed")
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    circuit_reason: Mapped[str | None] = mapped_column(String(64))
    circuit_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    probe_operation_id: Mapped[str | None] = mapped_column(String(36))
    auth_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_block_reason: Mapped[str | None] = mapped_column(String(64))
    last_auto_account_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PassOperation(Base):
    __tablename__ = "pass_operations"
    __table_args__ = (
        Index("ix_pass_operations_target_started", "target_ref", "started_at"),
        Index("ix_pass_operations_kind_started", "kind", "started_at"),
        Index("ix_pass_operations_status_started", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    target_ref: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="running")
    quota_bypassed: Mapped[bool] = mapped_column(Boolean, default=False)
    bypass_reason: Mapped[str | None] = mapped_column(String(240))
    is_probe: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    session_reused: Mapped[bool] = mapped_column(Boolean, default=False)
    full_sso_performed: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    error_class: Mapped[str | None] = mapped_column(String(64))
    upstream_status: Mapped[int | None] = mapped_column(Integer)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)


class PassDenial(Base):
    __tablename__ = "pass_denials"
    __table_args__ = (Index("ix_pass_denials_reason_created", "reason", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    target_ref: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"
    __table_args__ = (
        Index("ix_auth_attempts_client_attempted", "client_ref", "attempted_at"),
        Index("ix_auth_attempts_target_attempted", "target_ref", "attempted_at"),
        Index("ix_auth_attempts_outcome_attempted", "outcome", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_ref: Mapped[str] = mapped_column(String(64))
    client_ref: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(24))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthThrottleState(Base):
    __tablename__ = "auth_throttle_states"
    __table_args__ = (
        UniqueConstraint("scope", "reference", name="uq_auth_throttle_scope_reference"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str] = mapped_column(String(64))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyCredential(Base):
    __tablename__ = "passkey_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[str] = mapped_column(String(1024), unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[list] = mapped_column(JSON, default=list)
    name: Mapped[str] = mapped_column(String(80))
    device_type: Mapped[str | None] = mapped_column(String(32))
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebAuthnChallenge(Base):
    __tablename__ = "webauthn_challenges"
    __table_args__ = (Index("ix_webauthn_challenges_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("web_sessions.id", ondelete="CASCADE"), index=True
    )
    challenge: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CohortPulse(Base):
    __tablename__ = "cohort_pulses"
    __table_args__ = (
        UniqueConstraint("program", "promotion_year", name="uq_cohort_pulse_segment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    program: Mapped[str] = mapped_column(String(32))
    promotion_year: Mapped[int] = mapped_column(Integer)
    last_emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    affected_accounts: Mapped[int] = mapped_column(Integer, default=0)


class ShareToken(Base):
    __tablename__ = "share_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    digest: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebSession(Base):
    __tablename__ = "web_sessions"
    __table_args__ = (Index("ix_web_sessions_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    share_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("share_tokens.id", ondelete="SET NULL"), index=True
    )
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_digest: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    auth_method: Mapped[str] = mapped_column(String(16))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LeaderboardProfile(Base):
    __tablename__ = "leaderboard_profiles"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    pseudonym: Mapped[str | None] = mapped_column(String(24))
    pseudonym_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    is_participating: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ranking_visible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejoin_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(32))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(String(24), default="standard")
    score_ects_snapshot: Mapped[dict | None] = mapped_column(JSON)
    score_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score_verified_by_admin_id: Mapped[str | None] = mapped_column(String(36))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="leaderboard_profile")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (Index("ix_admin_sessions_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    admin_user_id: Mapped[str] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_digest: Mapped[str] = mapped_column(String(64))
    identity_digest: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    __table_args__ = (Index("ix_admin_audit_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), index=True
    )
    target_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
