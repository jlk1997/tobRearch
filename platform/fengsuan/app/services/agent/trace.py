from __future__ import annotations

import json

from sqlalchemy.orm import Session

from fengsuan.app.models.agent import AgentTrace


def record(
    db: Session,
    *,
    lab_id: str,
    user_id: str,
    session_id: str,
    feature: str,
    scope: str,
    step: int,
    kind: str,
    tool: str = "",
    args: dict | None = None,
    result_summary: str = "",
    source_ids: list[str] | None = None,
    latency_ms: int = 0,
    error: str = "",
    summary_limit: int = 2000,
) -> None:
    """写一条追踪。追踪失败绝不能拖垮回答：只用 savepoint，禁止 rollback 整单。"""
    try:
        with db.begin_nested():
            db.add(
                AgentTrace(
                    lab_id=lab_id,
                    user_id=user_id,
                    session_id=(session_id or "")[:64],
                    feature=(feature or "")[:40],
                    scope=(scope or "paper")[:20],
                    step=int(step),
                    kind=(kind or "tool")[:20],
                    tool=(tool or "")[:80],
                    args_json=json.dumps(args or {}, ensure_ascii=False)[:4000],
                    result_summary=(result_summary or "")[: max(200, min(int(summary_limit or 2000), 20000))],
                    source_ids_json=json.dumps(source_ids or [], ensure_ascii=False)[:4000],
                    latency_ms=max(0, int(latency_ms)),
                    error=(error or None),
                )
            )
            db.flush()
    except Exception:
        return


def _is_pending_summary(text: str) -> bool:
    raw = text or ""
    return "pending_confirm" in raw or "确认" in raw


def _trace_args(raw: str) -> dict:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


def last_pending_args(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    lab_id: str,
    tool: str,
) -> dict | None:
    """该工具在本会话里最近一次调用若仍是待确认，才允许回放。已写入则不能再点一次。"""
    sid = (session_id or "").strip()
    name = (tool or "").strip()
    if not sid or not name:
        return None
    row = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.session_id == sid,
            AgentTrace.user_id == user_id,
            AgentTrace.lab_id == lab_id,
            AgentTrace.kind == "tool",
            AgentTrace.tool == name,
        )
        .order_by(AgentTrace.created_at.desc())
        .first()
    )
    if row is None or row.error or not _is_pending_summary(row.result_summary):
        return None
    return _trace_args(row.args_json)


def session_pending_replays(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    lab_id: str,
) -> list[tuple[str, dict]]:
    """本会话当前仍待确认的写入工具（每个工具只看最近一次）。"""
    sid = (session_id or "").strip()
    if not sid:
        return []
    rows = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.session_id == sid,
            AgentTrace.user_id == user_id,
            AgentTrace.lab_id == lab_id,
            AgentTrace.kind == "tool",
        )
        .order_by(AgentTrace.created_at.desc())
        .limit(40)
        .all()
    )
    seen: set[str] = set()
    out: list[tuple[str, dict]] = []
    for row in rows:
        name = (row.tool or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if row.error or not _is_pending_summary(row.result_summary):
            continue
        out.append((name, _trace_args(row.args_json)))
    return out


def session_steps(db: Session, session_id: str, *, limit: int = 60) -> list[dict]:
    rows = (
        db.query(AgentTrace)
        .filter(AgentTrace.session_id == session_id)
        .order_by(AgentTrace.step.asc(), AgentTrace.created_at.asc())
        .limit(limit)
        .all()
    )
    from fengsuan.app.services.agent.registry import tool_label

    out: list[dict] = []
    for r in rows:
        try:
            args = json.loads(r.args_json or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            sids = json.loads(r.source_ids_json or "[]")
        except json.JSONDecodeError:
            sids = []
        out.append(
            {
                "step": r.step,
                "kind": r.kind,
                "tool": r.tool,
                "label": tool_label(r.tool) if r.tool else (r.kind or "作答"),
                "args": args if isinstance(args, dict) else {},
                "result_summary": r.result_summary or "",
                "source_ids": sids if isinstance(sids, list) else [],
                "latency_ms": r.latency_ms,
                "error": r.error or "",
            }
        )
    return out


def session_memory(db: Session, *, session_id: str, user_id: str, lab_id: str) -> dict:
    """上一轮认出的人、列出的论文、检索编号。新的覆盖旧的。"""
    sid = (session_id or "").strip()
    if not sid:
        return {}
    rows = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.session_id == sid,
            AgentTrace.user_id == user_id,
            AgentTrace.lab_id == lab_id,
            AgentTrace.kind == "memory",
        )
        .order_by(AgentTrace.created_at.asc())
        .limit(20)
        .all()
    )
    merged: dict = {}
    for row in rows:
        try:
            data = json.loads(row.args_json or "{}")
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            continue
        if data.get("person"):
            merged["person"] = data["person"]
        if data.get("papers"):
            merged["papers"] = data["papers"]
        if data.get("topic"):
            merged["topic"] = str(data["topic"])[:80]
        if data.get("hits"):
            merged["hits"] = data["hits"]
        if data.get("sweep_id"):
            merged["sweep_id"] = data["sweep_id"]
        if data.get("works_page"):
            try:
                merged["works_page"] = int(data.get("works_page") or 1)
            except (TypeError, ValueError):
                pass
        if data.get("citing_page"):
            try:
                merged["citing_page"] = int(data.get("citing_page") or 1)
            except (TypeError, ValueError):
                pass
    return merged
