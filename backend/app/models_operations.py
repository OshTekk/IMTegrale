from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, utcnow
from app.model_helpers import new_id


class DurableJob(Base):
    __tablename__ = "durable_jobs"
    __table_args__ = (
        UniqueConstraint("kind", "idempotency_key", name="uq_durable_jobs_kind_idempotency"),
        CheckConstraint("kind IN ('sync', 'calendar')", name="ck_durable_jobs_kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'dead_letter')",
            name="ck_durable_jobs_status",
        ),
        Index(
            "ix_durable_jobs_claim",
            "kind",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
        Index("ix_durable_jobs_expired_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    correlation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    worker_id: Mapped[str | None] = mapped_column(String(96))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("kind", "idempotency_key", name="uq_notification_outbox_kind_idempotency"),
        CheckConstraint("kind IN ('telegram_new_notes')", name="ck_notification_outbox_kind"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'delivered', 'dead_letter')",
            name="ck_notification_outbox_status",
        ),
        Index("ix_notification_outbox_claim", "status", "available_at", "created_at"),
        Index("ix_notification_outbox_expired_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    correlation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    worker_id: Mapped[str | None] = mapped_column(String(96))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RuntimeHeartbeat(Base):
    __tablename__ = "runtime_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "component IN ('scheduler', 'sync', 'calendar', 'outbox')",
            name="ck_runtime_heartbeats_component",
        ),
        CheckConstraint(
            "state IN ('starting', 'ok', 'error', 'stopping')",
            name="ck_runtime_heartbeats_state",
        ),
        Index("ix_runtime_heartbeats_seen_at", "seen_at"),
    )

    component: Mapped[str] = mapped_column(String(24), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(96))
    state: Mapped[str] = mapped_column(String(16), default="starting")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
