"""把现有 AI 功能接到运行时：先用工具取证，再走原来的生成路径。

方法卡 / 对比 / 大纲 / 章节 / KB / 组会仍产出原来的结构，
差别是每一步可追溯，引用能指回 `source_id`。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fengsuan.app.services.agent.budget import budget_for
from fengsuan.app.services.agent.loop import _run_tool
from fengsuan.app.services.agent.registry import ToolContext
from fengsuan.app.services.agent import trace


def run_seed_tools(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    feature: str,
    scope: str,
    seeds: list[tuple[str, dict]],
    item_id: str = "",
    manuscript_id: str = "",
    session_id: str = "",
    direction_id: str = "",
    question: str = "",
) -> dict:
    """只跑种子工具，不另开一轮作答。失败不拖垮原功能。"""
    ctx = ToolContext(
        db=db,
        user_id=user_id,
        lab_id=lab_id,
        scope=scope,
        item_id=item_id,
        manuscript_id=manuscript_id,
        direction_id=(direction_id or "")[:64],
    )
    if question:
        ctx.extra["question"] = (question or "")[:800]
    budget = budget_for(scope)
    sid = (session_id or f"{feature}:{item_id or lab_id}")[:64]
    steps: list[dict] = []
    sources: list[dict] = []
    for i, (name, args) in enumerate(seeds, start=1):
        result, ms = _run_tool(ctx, name, args or {}, budget)
        ids = [str(s.get("id") or "") for s in result.sources if s.get("id")]
        sources.extend(result.sources)
        steps.append(
            {
                "step": i,
                "tool": name,
                "args": args or {},
                "result_summary": result.summary(),
                "source_ids": ids,
                "error": result.error,
            }
        )
        trace.record(
            db,
            lab_id=lab_id,
            user_id=user_id,
            session_id=sid,
            feature=feature,
            scope=scope,
            step=i,
            kind="tool",
            tool=name,
            args=args or {},
            result_summary=result.summary(400),
            source_ids=ids,
            latency_ms=ms,
            error=result.error,
        )
    try:
        from fengsuan.app.services.observability import inc

        inc("agent_seeds")
        inc("agent_steps", len(steps))
    except Exception:
        pass
    return {"session_id": sid, "steps": steps, "sources": sources}


def evidence_block(pack: dict, *, limit: int = 4000) -> str:
    lines = []
    for src in pack.get("sources") or []:
        label = src.get("id") or ""
        excerpt = (src.get("excerpt") or "")[:800]
        if excerpt:
            lines.append(f"[{label}] {src.get('title') or ''}\n{excerpt}")
        if sum(len(x) for x in lines) >= limit:
            break
    return "\n\n".join(lines)[:limit]


def try_seed_tools(*args, **kwargs) -> dict:
    """种子失败返回空包，不拖垮原功能。"""
    try:
        return run_seed_tools(*args, **kwargs)
    except Exception:
        return {}


def attach_evidence(messages: list[dict], pack: dict | None, *, limit: int = 4000) -> list[dict]:
    """把工具片段接到现有 chat 消息上。不另开一轮，不改生成器结构。"""
    block = evidence_block(pack or {}, limit=limit)
    if not block or not messages:
        return messages
    note = "\n\n已用工具取到的可追溯片段（结论必须能指回方括号里的 source_id；没有出处就不要写成事实）：\n" + block
    out = [dict(m) for m in messages]
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            out[i]["content"] = (out[i].get("content") or "") + note
            return out
    out[0]["content"] = (out[0].get("content") or "") + note
    return out


def followups_from_result(payload: dict) -> list:
    """从现有下一步抽出可点花片。不另编下一问。"""
    out: list[dict] = []
    nxt = payload.get("next_step") or {}
    if isinstance(nxt, dict) and (nxt.get("label") or nxt.get("href")):
        label = (nxt.get("label") or nxt.get("text") or "下一步").strip()
        if label:
            out.append({"label": label[:40], "href": nxt.get("href") or ""})
    empty = payload.get("empty_next") or {}
    if isinstance(empty, dict):
        for s in empty.get("steps") or []:
            if not isinstance(s, dict):
                continue
            label = (s.get("label") or "").strip()
            if label:
                out.append({"label": label[:40], "href": s.get("href") or ""})
    seen: set[tuple[str, str]] = set()
    uniq: list[dict] = []
    for row in out:
        key = (row.get("label") or "", row.get("href") or "")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
    return uniq[:3]


def stamp_followups(payload: dict) -> dict:
    """下一步齐了再盖花片。已有 followups 不改。"""
    if payload.get("followups"):
        return payload
    payload["followups"] = followups_from_result(payload)
    return payload


def stamp_trace(payload: dict, pack: dict | None) -> dict:
    """把种子会话写进现有返回体，方便 GET /agent/traces/{id}，不另开字段结构。"""
    sid = ((pack or {}).get("session_id") or "").strip()
    if sid:
        payload["session_id"] = sid
        payload["trace_id"] = sid
    steps = list((pack or {}).get("steps") or [])
    if steps and not payload.get("steps"):
        from fengsuan.app.services.agent.registry import tool_label

        payload["steps"] = [
            {
                "step": s.get("step"),
                "tool": s.get("tool"),
                "label": s.get("label") or tool_label(s.get("tool") or ""),
                "summary": s.get("result_summary") or s.get("summary") or "",
                "result_summary": s.get("result_summary") or s.get("summary") or "",
                "error": s.get("error") or "",
            }
            for s in steps
        ]
    used = payload.get("steps") or steps
    if any((s.get("error") or "") for s in used):
        payload.setdefault("degraded", "tool_failures")
        payload.setdefault("degraded_to", "chat")
        tools: list[str] = []
        seen: set[str] = set()
        for s in used:
            name = str(s.get("tool") or "")
            if name and name not in seen:
                seen.add(name)
                tools.append(name)
        if tools:
            payload.setdefault("available_tools", tools)
    stamp_followups(payload)
    return payload
