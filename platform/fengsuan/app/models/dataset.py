from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Dataset(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "datasets"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    source_note: Mapped[str] = mapped_column(Text, default="")
    license: Mapped[str] = mapped_column(String(120), default="")
    local_path: Mapped[str] = mapped_column(String(1000), default="")
    file_path: Mapped[str] = mapped_column(String(1000), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class DatasetLiteratureLink(Base):
    __tablename__ = "dataset_literature_links"
    __table_args__ = (UniqueConstraint("dataset_id", "literature_item_id"),)

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    literature_item_id: Mapped[str] = mapped_column(ForeignKey("literature_items.id"), primary_key=True)
