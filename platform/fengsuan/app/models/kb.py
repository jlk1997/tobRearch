from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class KnowledgeBase(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "knowledge_bases"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), unique=True, index=True)
    embedding_model: Mapped[str] = mapped_column(String(120), default="bge-m3")
    dim: Mapped[int] = mapped_column(Integer, default=1024)


class KbDocument(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "kb_documents"
    __table_args__ = (
        Index("ix_kb_documents_status_created", "status", "created_at"),
        Index("ix_kb_documents_kind", "kb_id", "source_kind"),
    )

    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    literature_item_id: Mapped[str | None] = mapped_column(ForeignKey("literature_items.id"), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction_id: Mapped[str | None] = mapped_column(ForeignKey("research_directions.id"), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    # A-PROFILE：paper | lab_intro | meeting_minutes | manuscript | profile
    source_kind: Mapped[str] = mapped_column(String(24), default="paper")


class KbChunk(Base, UUIDPrimaryKey):
    __tablename__ = "kb_chunks"

    doc_id: Mapped[str] = mapped_column(ForeignKey("kb_documents.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[str] = mapped_column(String(500), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    # B-SCALE：检索直接按组 / 按篇过滤，不再连三张表。
    lab_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    literature_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # body | abstract | caption | table | reference | page
    kind: Mapped[str] = mapped_column(String(20), default="page")
    section: Mapped[str] = mapped_column(String(300), default="")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    # 真向量记模型名；兜底向量记 hash，之后会自动补。
    embed_model: Mapped[str] = mapped_column(String(80), default="")


class GlossaryTerm(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("lab_id", "src", "lang_pair"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    src: Mapped[str] = mapped_column(String(200))
    tgt: Mapped[str] = mapped_column(String(200), default="")
    lang_pair: Mapped[str] = mapped_column(String(20), default="en-zh")
    do_not_translate: Mapped[bool] = mapped_column(Boolean, default=False)
    domain: Mapped[str] = mapped_column(String(80), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
