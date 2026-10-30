from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class LiteratureItem(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "literature_items"
    __table_args__ = (Index("ix_literature_items_lab_deleted", "lab_id", "deleted_at"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    authors_json: Mapped[str] = mapped_column(Text, default="[]")
    abstract: Mapped[str] = mapped_column(Text, default="")
    doi: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cnki_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pubmed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(300), nullable=True)
    venue_type: Mapped[str] = mapped_column(String(40), default="other")
    language: Mapped[str] = mapped_column(String(16), default="en")
    publish_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_channel: Mapped[str] = mapped_column(String(40), default="search")
    external_ids_json: Mapped[str] = mapped_column(Text, default="{}")
    # B-SCALE：-1 还没算；0 没读；1 读了摘要；2 读了全文要点；3 精读（分节笔记）
    read_level: Mapped[int] = mapped_column(Integer, default=-1, index=True)
    # 预读排队分，越大越先读（整数，百分制）
    read_score: Mapped[int] = mapped_column(Integer, default=0)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    openalex_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # 篇级向量：题目 + 摘要 + 方法卡一句话，用来找主题相近的篇
    paper_embedding_json: Mapped[str] = mapped_column(Text, default="")
    # 书目补全状态：""未补 / ok / miss / offline
    enrich_state: Mapped[str] = mapped_column(String(20), default="")


class Author(Base, UUIDPrimaryKey, TimestampMixin):
    """组内作者。同名不同人分成多条；只凭名字不合并。"""

    __tablename__ = "authors"
    __table_args__ = (Index("ix_authors_lab_name", "lab_id", "name_key"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), default="")
    orcid: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    s2_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    affiliations_json: Mapped[str] = mapped_column(Text, default="[]")
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    # 人工合并后指向保留的那条；查询时跟过去
    merged_into: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 人工拆过、合过、认领过的，自动归并不再动它
    verified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    paper_count: Mapped[int] = mapped_column(Integer, default=0)


class Authorship(Base, UUIDPrimaryKey):
    """一篇论文的第几位作者是谁，以及凭什么认定。"""

    __tablename__ = "authorships"
    __table_args__ = (
        UniqueConstraint("item_id", "position"),
        Index("ix_authorships_author", "author_id"),
    )

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("authors.id"), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    raw_name: Mapped[str] = mapped_column(String(300), default="")
    affiliation: Mapped[str] = mapped_column(String(500), default="")
    orcid: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # orcid | openalex | s2 | coauthor | affiliation | name_only | manual
    evidence: Mapped[str] = mapped_column(String(40), default="")


class LiteratureReference(Base, UUIDPrimaryKey):
    """这篇引了谁。组里有被引那篇时填 cited_item_id。"""

    __tablename__ = "literature_references"
    __table_args__ = (
        Index("ix_litref_citing", "citing_item_id", "position"),
        Index("ix_litref_cited", "cited_item_id"),
        Index("ix_litref_key", "lab_id", "ref_key"),
    )

    lab_id: Mapped[str] = mapped_column(String(36), index=True)
    citing_item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"))
    cited_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    doi: Mapped[str | None] = mapped_column(String(128), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_author: Mapped[str] = mapped_column(String(200), default="")
    # doi:xxx 或 t:标题归一化；同一篇被两处引用时对得上
    ref_key: Mapped[str] = mapped_column(String(200), default="")
    # grobid | openalex | crossref | s2
    source: Mapped[str] = mapped_column(String(20), default="")


class LiteratureDirectionLink(Base):
    __tablename__ = "literature_direction_links"
    __table_args__ = (UniqueConstraint("item_id", "direction_id"),)

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), primary_key=True)
    direction_id: Mapped[str] = mapped_column(ForeignKey("research_directions.id"), primary_key=True)
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)


class LiteratureFile(Base, UUIDPrimaryKey):
    __tablename__ = "literature_files"
    __table_args__ = (Index("ix_literature_files_sha256", "content_sha256"),)

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(1000))
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(80), default="application/pdf")
    size: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # B-SCALE：GROBID 结构解析。"" 还没做 / ok / miss（解析不出，已退回按页切）
    structure_state: Mapped[str] = mapped_column(String(12), default="", index=True)


class LiteratureFilePage(Base, UUIDPrimaryKey):
    """页文本缓存。可安全清空重建，不是业务事实来源。"""

    __tablename__ = "literature_file_pages"
    __table_args__ = (
        UniqueConstraint("file_id", "page_no"),
        Index("ix_literature_file_pages_item", "item_id", "page_no"),
    )

    file_id: Mapped[str] = mapped_column(ForeignKey("literature_files.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")


class LiteraturePageTranslation(Base, UUIDPrimaryKey, TimestampMixin):
    """页译文缓存。`text_hash` 变了即视为失效。"""

    __tablename__ = "literature_page_translations"
    __table_args__ = (UniqueConstraint("item_id", "page_no", "lang"),)

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    lang: Mapped[str] = mapped_column(String(16), default="zh")
    text_hash: Mapped[str] = mapped_column(String(64), default="")
    text: Mapped[str] = mapped_column(Text, default="")


class LiteratureReading(Base, UUIDPrimaryKey):
    __tablename__ = "literature_readings"
    __table_args__ = (UniqueConstraint("item_id", "user_id"),)

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="want_read")
    progress_pct: Mapped[float] = mapped_column(Float, default=0)
    last_page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LiteratureNote(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "literature_notes"

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    content_md: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(20), default="lab")
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anchor_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class LiteratureAnalysis(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "literature_analyses"
    __table_args__ = (Index("ix_literature_analyses_item_type_by", "item_id", "analysis_type", "created_by"),)

    item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), index=True)
    analysis_type: Mapped[str] = mapped_column(String(40), default="method_card")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    model: Mapped[str] = mapped_column(String(120), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class BrowserCapture(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "browser_captures"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    page_url: Mapped[str] = mapped_column(String(1000))
    site_adapter: Mapped[str] = mapped_column(String(80), default="generic")
    raw_meta_json: Mapped[str] = mapped_column(Text, default="{}")
