"""Agent 审计只读入口。不是新聊天页。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, is_admin, require_membership
from fengsuan.app.db.session import get_db
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.identity import User
from fengsuan.app.services.agent import trace

router = APIRouter(tags=["agent"])


@router.get("/agent/traces/{trace_id}")
def get_agent_trace(
    trace_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """按 session_id 或单步 id 取工具追踪。导师、管理员或本人可见。"""
    tid = (trace_id or "").strip()
    if not tid:
        raise HTTPException(404, detail="没有这次追踪")
    row = db.get(AgentTrace, tid)
    session_id = (row.session_id if row else tid)[:64]
    first = (
        db.query(AgentTrace)
        .filter(AgentTrace.session_id == session_id)
        .order_by(AgentTrace.step.asc(), AgentTrace.created_at.asc())
        .first()
    )
    if first is None:
        raise HTTPException(404, detail="没有这次追踪")
    membership = require_membership(db, user, first.lab_id)
    if first.user_id != user.id and membership.role != "teacher" and not is_admin(db, user):
        raise HTTPException(403, detail="仅导师或本人可看这次追踪")
    steps = trace.session_steps(db, session_id)
    return {
        "trace_id": session_id,
        "lab_id": first.lab_id,
        "feature": first.feature,
        "scope": first.scope,
        "user_id": first.user_id,
        "steps": steps,
    }
