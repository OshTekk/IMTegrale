from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("imt_username", name="accounts_imt_username_key"),
        Index("ix_accounts_imt_username", "imt_username", unique=True),
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
    imt_username: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(120))
    encrypted_telegram_token: Mapped[str | None] = mapped_column(Text)
    encrypted_telegram_chat_id: Mapped[str | None] = mapped_column(Text)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_last_test_status: Mapped[str | None] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Paris")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str] = mapped_column(String(32), default="never")
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=2)
    auto_sync_adaptive: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_sync_current_interval_hours: Mapped[int] = mapped_column(Integer, default=2)
    auto_sync_no_change_streak: Mapped[int] = mapped_column(Integer, default=0)
    auto_sync_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_sync_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_sync_paused_reason: Mapped[str | None] = mapped_column(String(32))
    auto_sync_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    ue_metadata_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ue_metadata_refresh_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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
    sync_setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    notes: Mapped[list[Note]] = relationship(back_populates="account", cascade="all, delete-orphan")
    ue_settings: Mapped[list[UeSetting]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    simulation_scenarios: Mapped[list[SimulationScenario]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    calendar_subscription: Mapped[CalendarSubscription | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )
    leaderboard_profile: Mapped[LeaderboardProfile | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )
    pass_service_sessions: Mapped[list[PassServiceSession]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
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
    earned_credits_ects: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(200), default="")
    year: Mapped[str] = mapped_column(String(16), default="")
    semester: Mapped[str | None] = mapped_column(String(16))
    source_semester: Mapped[str | None] = mapped_column(String(32))
    official_code: Mapped[str | None] = mapped_column(String(80))
    official_grade: Mapped[str | None] = mapped_column(String(4))
    metadata_source: Mapped[str] = mapped_column(String(24), default="manual")
    metadata_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="ue_settings")


class SimulationScenario(Base):
    __tablename__ = "simulation_scenarios"
    __table_args__ = (
        Index("ix_simulation_scenarios_account_updated", "account_id", "updated_at"),
        Index(
            "ix_simulation_scenarios_account_kind_updated",
            "account_id",
            "kind",
            "updated_at",
        ),
        CheckConstraint(
            "created_from IN ('blank', 'academic')",
            name="ck_simulation_scenarios_created_from",
        ),
        CheckConstraint("version >= 1", name="ck_simulation_scenarios_version"),
        CheckConstraint(
            "kind IN ('gpa', 'notes')",
            name="ck_simulation_scenarios_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16), default="gpa")
    created_from: Mapped[str] = mapped_column(String(16), default="blank")
    formula_version: Mapped[str] = mapped_column(String(32), default="gpa-ects-v1")
    source_revision: Mapped[str | None] = mapped_column(String(64))
    source_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    account: Mapped[Account] = relationship(back_populates="simulation_scenarios")
    entries: Mapped[list[SimulationEntry]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="SimulationEntry.position, SimulationEntry.created_at, SimulationEntry.id",
    )
    score_ues: Mapped[list[ScoreSimulationUe]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScoreSimulationUe.position, ScoreSimulationUe.created_at, ScoreSimulationUe.id",
    )


class SimulationEntry(Base):
    __tablename__ = "simulation_entries"
    __table_args__ = (
        UniqueConstraint("scenario_id", "lineage_key", name="uq_simulation_entries_lineage"),
        Index("ix_simulation_entries_scenario_position", "scenario_id", "position"),
        CheckConstraint(
            "origin IN ('imported', 'simulated')",
            name="ck_simulation_entries_origin",
        ),
        CheckConstraint(
            "source_status IN ('current', 'conflict', 'unavailable')",
            name="ck_simulation_entries_source_status",
        ),
        CheckConstraint(
            "grade IS NULL OR grade IN ('A', 'B', 'C', 'D', 'E', 'FX', 'F')",
            name="ck_simulation_entries_grade",
        ),
        CheckConstraint(
            "base_grade IS NULL OR base_grade IN ('A', 'B', 'C', 'D', 'E', 'FX', 'F')",
            name="ck_simulation_entries_base_grade",
        ),
        CheckConstraint(
            "credits_ects IS NULL OR (credits_ects > 0 AND credits_ects <= 60)",
            name="ck_simulation_entries_credits",
        ),
        CheckConstraint(
            "base_credits_ects IS NULL OR (base_credits_ects > 0 AND base_credits_ects <= 60)",
            name="ck_simulation_entries_base_credits",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_scenarios.id", ondelete="CASCADE"), index=True
    )
    lineage_key: Mapped[str] = mapped_column(String(80))
    source_ue_code: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(16), default="simulated")
    source_status: Mapped[str] = mapped_column(String(16), default="current")
    semester: Mapped[str | None] = mapped_column(String(16))
    ue_code: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200), default="")
    credits_ects: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    grade: Mapped[str | None] = mapped_column(String(4))
    base_semester: Mapped[str | None] = mapped_column(String(16))
    base_ue_code: Mapped[str | None] = mapped_column(String(32))
    base_title: Mapped[str | None] = mapped_column(String(200))
    base_credits_ects: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    base_grade: Mapped[str | None] = mapped_column(String(4))
    base_grade_source: Mapped[str | None] = mapped_column(String(24))
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    scenario: Mapped[SimulationScenario] = relationship(back_populates="entries")


class ScoreSimulationUe(Base):
    __tablename__ = "score_simulation_ues"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id",
            "lineage_key",
            name="uq_score_simulation_ues_lineage",
        ),
        Index("ix_score_simulation_ues_scenario_position", "scenario_id", "position"),
        CheckConstraint(
            "origin IN ('imported', 'simulated')",
            name="ck_score_simulation_ues_origin",
        ),
        CheckConstraint(
            "source_status IN ('current', 'conflict', 'unavailable')",
            name="ck_score_simulation_ues_source_status",
        ),
        CheckConstraint(
            "credits_ects IS NULL OR (credits_ects > 0 AND credits_ects <= 60)",
            name="ck_score_simulation_ues_credits",
        ),
        CheckConstraint(
            "base_credits_ects IS NULL OR (base_credits_ects > 0 AND base_credits_ects <= 60)",
            name="ck_score_simulation_ues_base_credits",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_scenarios.id", ondelete="CASCADE"), index=True
    )
    lineage_key: Mapped[str] = mapped_column(String(80))
    source_ue_code: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(16), default="simulated")
    source_status: Mapped[str] = mapped_column(String(16), default="current")
    semester: Mapped[str | None] = mapped_column(String(16))
    ue_code: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200), default="")
    credits_ects: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    base_semester: Mapped[str | None] = mapped_column(String(16))
    base_ue_code: Mapped[str | None] = mapped_column(String(32))
    base_title: Mapped[str | None] = mapped_column(String(200))
    base_credits_ects: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    scenario: Mapped[SimulationScenario] = relationship(back_populates="score_ues")
    assessments: Mapped[list[ScoreSimulationAssessment]] = relationship(
        back_populates="ue",
        cascade="all, delete-orphan",
        order_by=(
            "ScoreSimulationAssessment.position, "
            "ScoreSimulationAssessment.created_at, ScoreSimulationAssessment.id"
        ),
    )


class ScoreSimulationAssessment(Base):
    __tablename__ = "score_simulation_assessments"
    __table_args__ = (
        UniqueConstraint(
            "ue_id",
            "lineage_key",
            name="uq_score_simulation_assessments_lineage",
        ),
        Index("ix_score_simulation_assessments_ue_position", "ue_id", "position"),
        CheckConstraint(
            "origin IN ('imported', 'simulated')",
            name="ck_score_simulation_assessments_origin",
        ),
        CheckConstraint(
            "source_status IN ('current', 'conflict', 'unavailable')",
            name="ck_score_simulation_assessments_source_status",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 20)",
            name="ck_score_simulation_assessments_score",
        ),
        CheckConstraint(
            "base_score IS NULL OR (base_score >= 0 AND base_score <= 20)",
            name="ck_score_simulation_assessments_base_score",
        ),
        CheckConstraint(
            "coefficient > 0 AND coefficient <= 100",
            name="ck_score_simulation_assessments_coefficient",
        ),
        CheckConstraint(
            "base_coefficient IS NULL OR (base_coefficient > 0 AND base_coefficient <= 100)",
            name="ck_score_simulation_assessments_base_coefficient",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ue_id: Mapped[str] = mapped_column(
        ForeignKey("score_simulation_ues.id", ondelete="CASCADE"), index=True
    )
    lineage_key: Mapped[str] = mapped_column(String(120))
    source_note_key: Mapped[str | None] = mapped_column(String(96))
    origin: Mapped[str] = mapped_column(String(16), default="simulated")
    source_status: Mapped[str] = mapped_column(String(16), default="current")
    label: Mapped[str] = mapped_column(String(240), default="")
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    coefficient: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("1"))
    is_resit: Mapped[bool] = mapped_column(Boolean, default=False)
    base_label: Mapped[str | None] = mapped_column(String(240))
    base_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    base_coefficient: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    base_is_resit: Mapped[bool | None] = mapped_column(Boolean)
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    ue: Mapped[ScoreSimulationUe] = relationship(back_populates="assessments")


class CalendarSubscription(Base):
    __tablename__ = "calendar_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "last_status IN ('pending', 'success', 'error')",
            name="ck_calendar_subscriptions_status",
        ),
        Index("ix_calendar_subscriptions_next_refresh", "next_refresh_at"),
    )

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    encrypted_url: Mapped[str] = mapped_column(Text)
    url_digest: Mapped[str] = mapped_column(String(64), unique=True)
    account_hint: Mapped[str] = mapped_column(String(96))
    content_digest: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(256))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_refresh_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    account: Mapped[Account] = relationship(back_populates="calendar_subscription")
    events: Mapped[list[CalendarEvent]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
        order_by="CalendarEvent.starts_at, CalendarEvent.ends_at, CalendarEvent.id",
    )


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("account_id", "source_key", name="uq_calendar_events_source_key"),
        Index("ix_calendar_events_account_start", "account_id", "starts_at"),
        Index("ix_calendar_events_account_end", "account_id", "ends_at"),
        CheckConstraint("ends_at > starts_at", name="ck_calendar_events_dates"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("calendar_subscriptions.account_id", ondelete="CASCADE"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(300))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subscription: Mapped[CalendarSubscription] = relationship(back_populates="events")


class CalendarFetchAttempt(Base):
    __tablename__ = "calendar_fetch_attempts"
    __table_args__ = (
        Index(
            "ix_calendar_fetch_attempts_account_kind_attempted",
            "account_id",
            "kind",
            "attempted_at",
        ),
        Index("ix_calendar_fetch_attempts_attempted", "attempted_at"),
        CheckConstraint(
            "kind IN ('connect', 'automatic')",
            name="ck_calendar_fetch_attempts_kind",
        ),
        CheckConstraint(
            "outcome IN ('success', 'not_modified', 'invalid', 'upstream')",
            name="ck_calendar_fetch_attempts_outcome",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(24))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class PassServiceSession(Base):
    __tablename__ = "pass_service_sessions"
    __table_args__ = (
        Index(
            "uq_pass_service_sessions_active_account",
            "account_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
        Index(
            "ix_pass_service_sessions_account_state",
            "account_id",
            "state",
        ),
        Index("ix_pass_service_sessions_established_at", "established_at"),
        Index("ix_pass_service_sessions_ended_at", "ended_at"),
        CheckConstraint(
            "state IN ('active', 'expired', 'revoked', 'invalid')",
            name="ck_pass_service_sessions_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    encrypted_cookie_jar: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="active")
    established_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pass_last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hub_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hub_last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(32))
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    account: Mapped[Account] = relationship(back_populates="pass_service_sessions")


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
    __table_args__ = (
        UniqueConstraint("prefix", name="share_tokens_prefix_key"),
        Index("ix_share_tokens_prefix", "prefix", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))
    digest: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebSession(Base):
    __tablename__ = "web_sessions"
    __table_args__ = (
        UniqueConstraint("digest", name="web_sessions_digest_key"),
        Index("ix_web_sessions_digest", "digest", unique=True),
        Index("ix_web_sessions_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    share_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("share_tokens.id", ondelete="SET NULL"), index=True
    )
    digest: Mapped[str] = mapped_column(String(64))
    csrf_digest: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    auth_method: Mapped[str] = mapped_column(String(16))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LeaderboardProfile(Base):
    __tablename__ = "leaderboard_profiles"
    __table_args__ = (
        UniqueConstraint(
            "pseudonym_key",
            name="uq_leaderboard_profiles_pseudonym_key",
        ),
        Index("ix_leaderboard_profiles_pseudonym_key", "pseudonym_key"),
    )

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    pseudonym: Mapped[str | None] = mapped_column(String(24))
    pseudonym_key: Mapped[str | None] = mapped_column(String(64))
    is_participating: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ranking_visible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejoin_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(32))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(String(24), default="standard")
    score_ects_basis: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    score_basis_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_recommended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="leaderboard_profile")


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_admin_users_username"),
        Index("ix_admin_users_username", "username", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        UniqueConstraint("digest", name="uq_admin_sessions_digest"),
        Index("ix_admin_sessions_digest", "digest", unique=True),
        Index("ix_admin_sessions_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    admin_user_id: Mapped[str] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    digest: Mapped[str] = mapped_column(String(64))
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
