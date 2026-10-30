"""本组助手的说话方式和要记住的事。写在已有设置和知识库里，不另开一套库。"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.identity import User
from fengsuan.app.models.kb import KbChunk, KbDocument, KnowledgeBase
from fengsuan.app.models.lab import Lab, LabMembership

_VOICE_CAP = 400
_INTRO_CAP = 4000
_INTRO_NAME = "组里要记住的.txt"


def _settings(lab: Lab | None) -> dict:
    if lab is None:
        return {}
    try:
        data = json.loads(lab.settings_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def agent_voice(lab: Lab | None) -> str:
    agent = _settings(lab).get("agent")
    if not isinstance(agent, dict):
        return ""
    return str(agent.get("voice") or "").strip()[:_VOICE_CAP]


def save_agent_voice(lab: Lab, voice: str) -> None:
    cur = _settings(lab)
    agent = cur.get("agent") if isinstance(cur.get("agent"), dict) else {}
    agent["voice"] = str(voice or "").strip()[:_VOICE_CAP]
    cur["agent"] = agent
    lab.settings_json = json.dumps(cur, ensure_ascii=False)


def _intro_doc(db: Session, lab_id: str) -> KbDocument | None:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
    if kb is None:
        return None
    return (
        db.query(KbDocument)
        .filter(
            KbDocument.kb_id == kb.id,
            KbDocument.source_kind == "lab_intro",
            KbDocument.deleted_at.is_(None),
        )
        .order_by(KbDocument.created_at.asc())
        .first()
    )


def lab_intro_text(db: Session, lab_id: str) -> tuple[str, str]:
    """返回正文和第一块的 id，供回答引用。"""
    doc = _intro_doc(db, lab_id)
    if doc is None:
        return "", ""
    from fengsuan.app.services.kb import read_document_pages

    pages = read_document_pages(doc.file_path)
    text = "\n".join(pages).strip()[:_INTRO_CAP]
    chunk = db.query(KbChunk).filter(KbChunk.doc_id == doc.id).order_by(KbChunk.seq.asc()).first()
    if not text and chunk is not None:
        text = (chunk.content or "").strip()[:_INTRO_CAP]
    return text, (chunk.id if chunk is not None else "")


def save_lab_intro(db: Session, lab_id: str, user_id: str, text: str) -> str:
    """同一篇 lab_intro 更新后再切块。空文本就收起这篇。"""
    from fengsuan.app.services.kb import get_or_create_kb, index_document, save_upload

    body = str(text or "").strip()[:_INTRO_CAP]
    kb = get_or_create_kb(db, lab_id)
    docs = (
        db.query(KbDocument)
        .filter(KbDocument.kb_id == kb.id, KbDocument.source_kind == "lab_intro", KbDocument.deleted_at.is_(None))
        .order_by(KbDocument.created_at.asc())
        .all()
    )
    doc = docs[0] if docs else None
    for extra in docs[1:]:
        extra.deleted_at = utcnow()
    if not body:
        if doc is not None:
            doc.deleted_at = utcnow()
        return ""
    path = save_upload(lab_id, _INTRO_NAME, body.encode("utf-8"))
    if doc is None:
        doc = KbDocument(
            kb_id=kb.id,
            original_filename=_INTRO_NAME,
            file_path=str(path),
            status="pending",
            uploaded_by=user_id,
            source_kind="lab_intro",
        )
        db.add(doc)
        db.flush()
    else:
        doc.file_path = str(path)
        doc.original_filename = _INTRO_NAME
        doc.status = "pending"
        doc.deleted_at = None
        doc.content_hash = ""
    index_document(db, doc, [body])
    return body


def marked_sections(text: str) -> dict[str, str]:
    """从同一篇组内说明里拆出称呼、约定、前提。没有标记就返回空。"""
    raw = text or ""
    found: dict[str, str] = {}
    for key in ("称呼", "约定", "前提"):
        matched = re.search(rf"【{key}】\s*(.*?)(?=【(?:称呼|约定|前提)】|$)", raw, re.S)
        if not matched:
            continue
        body = re.sub(r"\s+", " ", matched.group(1)).strip()
        if body:
            found[key] = body
    return found


def teacher_rows(db: Session, lab_id: str) -> list[dict]:
    rows = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id, LabMembership.role == "teacher")
        .all()
    )
    return [{"user_id": user.id, "display_name": user.display_name or user.email} for _mem, user in rows]


def standing_for_ask(db: Session, lab_id: str, scope: str) -> tuple[str, dict | None, str]:
    """说话方式每次都带。组里写的只进实验室问答，不铺进单篇辅导。"""
    lab = db.get(Lab, lab_id)
    lines: list[str] = []
    voice = agent_voice(lab)
    if voice:
        lines.append(f"本组说话方式（只管语气，不能改名册上的角色，也不能改组里写的称呼）：{voice}")
    src = None
    if scope in {"lab", "onboarding"}:
        text, chunk_id = lab_intro_text(db, lab_id)
        if text:
            sections = marked_sections(text)
            if sections:
                blob = "\n".join(f"{key}：{sections[key][:500]}" for key in ("称呼", "约定", "前提") if sections.get(key))
            else:
                blob = text[:1200]
            lines.append(f"组里写过这些。和名册冲突时两句都说，不要折中：{blob}")
            src = {
                "id": f"kb:{chunk_id}" if chunk_id else "kb:lab_intro",
                "kind": "lab_intro",
                "title": "组里要记住的",
                "excerpt": blob[:1200],
                "intro_address": sections.get("称呼", "")[:800],
                "intro_custom": sections.get("约定", "")[:800],
                "intro_premise": sections.get("前提", "")[:800],
                "agent_voice": voice[:400],
                "href": f"/labs/{lab_id}/kb?tab=docs",
            }
    return "\n".join(lines), src, voice
