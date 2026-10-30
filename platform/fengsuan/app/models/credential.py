from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class LLMCredential(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "llm_credentials"

    owner_type: Mapped[str] = mapped_column(String(20))  # user | lab | platform
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="openai")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    model_chat: Mapped[str] = mapped_column(String(200), default="")
    model_vision: Mapped[str] = mapped_column(String(200), default="")
    model_embed: Mapped[str] = mapped_column(String(200), default="")
    model_rerank: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20), default="cloud")  # cloud | onprem
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class GPUProfile(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "gpu_profiles"

    name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20), default="off")
    docker_gpus: Mapped[str] = mapped_column(String(80), default="")
    external_endpoint: Mapped[str] = mapped_column(String(500), default="")
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
