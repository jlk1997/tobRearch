from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey, utcnow


class ResearchRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "research_runs"
    __table_args__ = (
        Index("ix_research_runs_status_created", "status", "created_at"),
        Index("ix_research_runs_lab_status_created", "lab_id", "status", "created_at"),
    )

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    dataset_path: Mapped[str] = mapped_column(String(1000), default="")
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    domain: Mapped[str] = mapped_column(String(40), default="general")
    language: Mapped[str] = mapped_column(String(16), default="chs")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    save_path: Mapped[str] = mapped_column(String(1000), default="")
    interrupt_after: Mapped[str] = mapped_column(String(80), default="")
    resume_from_subgraph: Mapped[str] = mapped_column(String(80), default="")
    token_usage_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ReproduceRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "reproduce_runs"
    __table_args__ = (Index("ix_reproduce_runs_status_created", "status", "created_at"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    literature_item_id: Mapped[str | None] = mapped_column(ForeignKey("literature_items.id"), nullable=True)
    dataset_path: Mapped[str] = mapped_column(String(1000), default="")
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    claimed_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    actual_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    method_card_json: Mapped[str] = mapped_column(Text, default="{}")
    gap_report_md: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_stage: Mapped[str] = mapped_column(String(40), default="ingest")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    save_path: Mapped[str] = mapped_column(String(1000), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    needs_gpu: Mapped[bool] = mapped_column(default=False)


class ReproduceAttempt(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "reproduce_attempts"

    run_id: Mapped[str] = mapped_column(ForeignKey("reproduce_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="implement")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    log_summary: Mapped[str] = mapped_column(Text, default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")


class UsageLedger(Base, UUIDPrimaryKey):
    __tablename__ = "usage_ledger"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    feature: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate: Mapped[str] = mapped_column(String(40), default="0")
    credential_scope: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
