from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Manuscript(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "manuscripts"
    __table_args__ = (Index("ix_manuscripts_lab_deleted", "lab_id", "deleted_at"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(40), default="other")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    venue: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_md: Mapped[str] = mapped_column(Text, default="")
    content_latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    overleaf_git_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    overleaf_last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ManuscriptDirectionLink(Base):
    __tablename__ = "manuscript_direction_links"
    __table_args__ = (UniqueConstraint("manuscript_id", "direction_id"),)

    manuscript_id: Mapped[str] = mapped_column(ForeignKey("manuscripts.id"), primary_key=True)
    direction_id: Mapped[str] = mapped_column(ForeignKey("research_directions.id"), primary_key=True)


class ManuscriptVersion(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "manuscript_versions"

    manuscript_id: Mapped[str] = mapped_column(ForeignKey("manuscripts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_md: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class ManuscriptComment(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "manuscript_comments"

    manuscript_id: Mapped[str] = mapped_column(ForeignKey("manuscripts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    anchor_json: Mapped[str] = mapped_column(Text, default="{}")
    body: Mapped[str] = mapped_column(Text, default="")


class ManuscriptCitation(Base, UUIDPrimaryKey):
    __tablename__ = "manuscript_citations"

    manuscript_id: Mapped[str] = mapped_column(ForeignKey("manuscripts.id"), index=True)
    literature_item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"))
    citation_key: Mapped[str] = mapped_column(String(80), default="")
