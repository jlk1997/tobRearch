from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgentTrace(Base, UUIDPrimaryKey, TimestampMixin):
    """一次 Agent 会话的逐步审计记录。

    只做审计与复算（「这个答案是怎么来的」），不是影子业务表：
    业务事实仍在 `literature_analyses` / `kb_*` / `literature_notes` 里。
    """

    __tablename__ = "agent_traces"
    __table_args__ = (
        Index("ix_agent_traces_lab_feature", "lab_id", "feature", "created_at"),
        Index("ix_agent_traces_session_step", "session_id", "step"),
    )

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    feature: Mapped[str] = mapped_column(String(40), default="")
    scope: Mapped[str] = mapped_column(String(20), default="paper")
    step: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20), default="tool")
    tool: Mapped[str] = mapped_column(String(80), default="")
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
