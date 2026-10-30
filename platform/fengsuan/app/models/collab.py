from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey, utcnow


class Meeting(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "meetings"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    notes_raw: Mapped[str] = mapped_column(Text, default="")


class MeetingMinutes(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "meeting_minutes"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    content_md: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")


class ActionItem(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "action_items"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("lab_tasks.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    source_line: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LabFile(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "lab_files"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str] = mapped_column(String(1000))
    mime: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")
