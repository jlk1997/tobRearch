from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fengsuan.app.core.concurrency import worker_enabled
from fengsuan.app.core.deps import get_current_user, require_membership, require_owner_or_teacher
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.safepath import clamp_page, read_upload_limited, safe_filename
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import User
from fengsuan.app.models.kb import GlossaryTerm, KbDocument, KnowledgeBase
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.schemas.common import GlossaryCreate, KbFeedbackBody, KbQueryBody
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.kb import assert_citations_are_substrings, get_or_create_kb, index_document, infer_source_kind, list_glossary, query_kb, read_document_pages, save_upload, summarize_lab_kb
from fengsuan.app.services.llm import chat, quota_warning
from fengsuan.app.services.prompts import load_prompt

router = APIRouter(tags=["kb"])


@router.get("/labs/{lab_id}/kb")
def kb_meta(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    kb = get_or_create_kb(db, lab_id)
    db.commit()
    docs = db.query(KbDocument).filter(KbDocument.kb_id == kb.id, KbDocument.deleted_at.is_(None)).count()
    from fengsuan.app.services.insight import kb_ask_sessions

    return {
        "id": kb.id,
        "lab_id": lab_id,
        "document_count": docs,
        "ask_sessions": kb_ask_sessions(db, lab_id, user.id, limit=20),
    }


@router.get("/labs/{lab_id}/kb/documents")
def list_docs(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
    page_size: int = 20,
    direction_id: str | None = None,
) -> dict:
    require_membership(db, user, lab_id)
    kb = get_or_create_kb(db, lab_id)
    db.commit()
    q = db.query(KbDocument).filter(KbDocument.kb_id == kb.id, KbDocument.deleted_at.is_(None)).order_by(KbDocument.created_at.desc())
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope
        from fengsuan.app.services.kb import kb_doc_ids_for_query

        allowed = set(kb_doc_ids_for_query(db, kb, direction_id))
        scope = resolve_direction_scope(db, lab_id, direction_id) or [direction_id]
        pending = [
            d.id
            for d in db.query(KbDocument)
            .filter(KbDocument.kb_id == kb.id, KbDocument.deleted_at.is_(None), KbDocument.direction_id.in_(scope))
            .all()
            if d.status != "indexed"
        ]
        keep = allowed | set(pending)
        if keep:
            q = q.filter(KbDocument.id.in_(list(keep)))
        else:
            q = q.filter(KbDocument.id.is_(None))
    total = q.count()
    p, s = clamp_page(page, page_size, max_size=100)
    rows = q.offset((p - 1) * s).limit(s).all()
    lit_ids = [d.literature_item_id for d in rows if d.literature_item_id]
    titles = {}
    if lit_ids:
        titles = {
            x.id: x.title
            for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    items = [
        {
            "id": d.id,
            "filename": d.original_filename,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "parse_error": d.parse_error,
            "literature_item_id": d.literature_item_id,
            "literature_title": titles.get(d.literature_item_id) if d.literature_item_id else None,
            "direction_id": d.direction_id,
        }
        for d in rows
    ]
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_items_open_href(db, user.id, items, id_key="literature_item_id")
    stamp_reproduce_href(lab_id, items, id_key="literature_item_id")
    stamp_latest_reproduce(db, items, id_key="literature_item_id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, items, id_key="literature_item_id")
    from fengsuan.app.services.llm import embed_texts

    _vecs, embed_fallback = embed_texts(db, user_id=user.id, lab_id=lab_id, texts=["probe"], dim=kb.dim or 8)
    out = {
        "items": items,
        "total": total,
        "page": p,
        "page_size": s,
        "embed_fallback": embed_fallback or "hash",
    }
    if total == 0:
        from fengsuan.app.services.insight import kb_ask_empty_next

        out["empty_kind"] = "library" if not direction_id else "filter"
        out["empty_next"] = kb_ask_empty_next(lab_id, "no_indexed", direction_id=direction_id)
    return out


@router.post("/labs/{lab_id}/kb/documents")
def upload_doc(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    direction_id: str | None = Form(default=None),
    source_kind: str | None = Form(default=None),
) -> dict:
    require_membership(db, user, lab_id)
    kb = get_or_create_kb(db, lab_id)
    try:
        content = read_upload_limited(file)
    except ValueError:
        raise HTTPException(400, detail="文件过大，单文件不超过 32MB")
    fname = safe_filename(file.filename or "doc.txt", "doc.txt")
    dest = save_upload(lab_id, fname, content)
    did = (direction_id or "").strip() or None
    if did:
        from fengsuan.app.services.directions import live_direction

        if live_direction(db, lab_id, did) is None:
            raise HTTPException(404, detail="方向不存在")
    doc = KbDocument(
        kb_id=kb.id,
        original_filename=fname,
        file_path=str(dest),
        status="pending",
        uploaded_by=user.id,
        direction_id=did,
        source_kind=infer_source_kind(fname, source_kind),
    )
    db.add(doc)
    db.flush()
    pages: list[str]
    name = fname.lower()
    if name.endswith(".pdf"):
        from fengsuan.app.services.pdfparse import extract_pdf_bytes

        pages, status, err = extract_pdf_bytes(content)
        if status == "unusable":
            doc.status = "failed"
            doc.parse_error = err or "unusable"
            db.commit()
            raise ClassifiedHTTPException(400, "无法提取正文，请换可复制 PDF。", "parse")
    else:
        pages = [content.decode("utf-8", errors="ignore")]
    index_document(db, doc, pages)
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="kb.indexed",
        object_type="kb_document",
        object_id=doc.id,
        summary=doc.original_filename,
    )
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "status": doc.status, "chunk_count": doc.chunk_count}


@router.delete("/kb/documents/{doc_id}")
def delete_doc(
    doc_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    doc = db.get(KbDocument, doc_id)
    if doc is None:
        raise HTTPException(404, detail="文档不存在")
    from fengsuan.app.models.kb import KnowledgeBase

    kb = db.get(KnowledgeBase, doc.kb_id)
    if kb is None:
        raise HTTPException(404, detail="知识库不存在")
    membership = require_membership(db, user, kb.lab_id)
    if doc.uploaded_by != user.id and membership.role != "teacher":
        raise HTTPException(403, detail="仅上传者或导师可删除")
    doc.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/kb/documents/{doc_id}/reindex")
def reindex_doc(
    doc_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    doc = db.get(KbDocument, doc_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(404, detail="文档不存在")
    from fengsuan.app.models.kb import KnowledgeBase

    kb = db.get(KnowledgeBase, doc.kb_id)
    if kb is None:
        raise HTTPException(404, detail="知识库不存在")
    require_membership(db, user, kb.lab_id)
    if not (doc.file_path or "").strip():
        doc.status = "failed"
        doc.parse_error = "missing_file"
        db.commit()
        raise ClassifiedHTTPException(400, "原文件缺失，请重新上传可复制的文字版。", "parse")
    from pathlib import Path

    if not Path(doc.file_path).exists():
        doc.status = "failed"
        doc.parse_error = "missing_file"
        db.commit()
        raise ClassifiedHTTPException(400, "原文件缺失，请重新上传可复制的文字版。", "parse")
    doc.status = "pending"
    doc.parse_error = None
    db.flush()
    if worker_enabled():
        db.commit()
        db.refresh(doc)
        return {"id": doc.id, "status": doc.status, "chunk_count": doc.chunk_count}
    pages = read_document_pages(doc.file_path)
    try:
        index_document(db, doc, pages)
    except Exception:
        doc.status = "failed"
        doc.parse_error = "index_failed"
    if doc.status == "indexed":
        emit_activity(
            db,
            lab_id=kb.lab_id,
            actor_id=user.id,
            verb="kb.indexed",
            object_type="kb_document",
            object_id=doc.id,
            summary=doc.original_filename,
        )
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "status": doc.status, "chunk_count": doc.chunk_count, "parse_error": doc.parse_error}


def _seed_from_context(body: KbQueryBody) -> list[tuple[str, dict]]:
    ctx = getattr(body, "context", None)
    if ctx is None:
        return []
    kind = (ctx.kind or "").strip()
    cid = (ctx.id or "").strip()
    if not cid:
        return []
    if kind == "direction":
        return [("get_direction_brief", {"direction_id": cid})]
    if kind == "paper":
        return [("get_paper_digest", {"item_id": cid})]
    if kind == "member":
        return [("get_member_work", {"user_id": cid})]
    return []


def _agent_kb_query(db: Session, user: User, lab_id: str, body: KbQueryBody) -> dict:
    """`scope=lab` / `scope=onboarding` 走 Agent 运行时：可多轮追问，引用可点。

    仍是知识库问答这一个入口，不新开产品面。返回体在原有键上只做增量。
    """
    from fengsuan.app.services.insight import kb_session_history
    from fengsuan.app.services.onboarding import onboarding_system_extra

    scope = body.scope
    extra = onboarding_system_extra(db, lab_id) if scope == "onboarding" else ""
    explicit = (body.direction_id or "").strip()
    defaulted = ""
    if not explicit and scope == "onboarding":
        from fengsuan.app.services.directions import member_primary_direction_id

        defaulted = member_primary_direction_id(db, lab_id, user.id)
    if explicit and scope != "onboarding":
        extra = (extra + "\n" if extra else "") + "用户选了研究方向，只引用该方向（含小课题）已入库的材料。"
    direction_id = explicit or defaulted
    seed = _seed_from_context(body)
    if seed and seed[0][0] == "get_direction_brief":
        explicit = explicit or str((seed[0][1] or {}).get("direction_id") or "")
        direction_id = explicit or defaulted
    stored = []
    if not list(body.history or []):
        stored = kb_session_history(db, lab_id, user.id, body.session_id or "")
    out = _consume_lab_ask(
        db,
        user,
        lab_id,
        body,
        scope=scope,
        extra=extra,
        explicit=explicit,
        defaulted=defaulted,
        direction_id=direction_id,
        seed=seed,
        stored=stored,
    )
    return out


def _consume_lab_ask(db, user, lab_id, body, **prep) -> dict:
    payload = {}
    for ev in _iter_lab_ask(db, user, lab_id, body, **prep):
        if "payload" in ev:
            payload = ev["payload"]
    return payload


def _iter_lab_ask(db, user, lab_id, body, *, scope, extra, explicit, defaulted, direction_id, seed, stored):
    """先吐工具和文字，最后一条带完整 payload。"""
    from fengsuan.app.services.agent.loop import run_agent
    from fengsuan.app.services.insight import ask_history_for_agent, kb_ask_empty_next

    out = {}
    for ev in run_agent(
        db,
        user_id=user.id,
        lab_id=lab_id,
        scope=scope,
        question=body.q,
        feature="kb_query",
        session_id=body.session_id or "",
        history=ask_history_for_agent(body.history, stored),
        system_extra=extra,
        confirm=bool(getattr(body, "confirm", False)),
        direction_id=explicit,
        default_direction_id=defaulted,
        confirm_tools=getattr(body, "confirm_tools", None),
        seed_tools=seed or None,
    ):
        if ev.get("done"):
            out = {k: v for k, v in ev.items() if k != "done"}
        else:
            yield ev
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="ask.lab" if scope in {"lab", "onboarding"} else "kb.query",
        object_type="kb",
        summary=body.q[:80],
        payload={"scope": scope, "session_id": out.get("session_id") or body.session_id or ""},
    )
    db.commit()
    hits = [
        {
            "chunk_id": s.get("id"),
            "content": s.get("excerpt") or "",
            "citation": s.get("excerpt") or "",
            "page_no": s.get("page"),
            "heading_path": s.get("title") or "",
            "filename": s.get("title") or "",
            "literature_item_id": s.get("item_id") or None,
            "open_href": s.get("href") or "",
            "source_id": s.get("id"),
            "label": s.get("label"),
            "kind": s.get("kind"),
            "doi": s.get("doi") or "",
            "arxiv_id": s.get("arxiv_id") or "",
            "year": s.get("year"),
            "authors": s.get("authors") or [],
            "score": None,
            "retrieval_mode": "agent",
            "relation": s.get("relation") or "",
            "relations": s.get("relations") or [],
            "relation_label": s.get("relation_label") or "",
            "author_id": s.get("author_id") or "",
            "url": s.get("url") or "",
            "site": s.get("site") or "",
            "published": s.get("published") or "",
            "provider": s.get("provider") or "",
            "identity": s.get("identity") or None,
            "oa": s.get("oa") or None,
            "sweep_id": s.get("sweep_id") or "",
            "ordinal": s.get("ordinal") or None,
            "why": s.get("why") or "",
            "section": s.get("section") or "",
            "role": s.get("role") or "",
            "member_title": s.get("member_title") or "",
            "member_dirs": s.get("member_dirs") or "",
            "my_reading": s.get("my_reading") or "",
            "kind_label": s.get("kind_label") or "",
        }
        for s in out.get("sources") or []
    ]
    payload = {
        "hits": hits,
        "answer": out.get("answer") or "",
        "quota_warning": quota_warning(db, lab_id),
        "empty_reason": "" if hits else "no_hits",
        "embed_fallback": "",
        "ungrounded": bool(out.get("ungrounded")),
        "ungrounded_spans": list(out.get("ungrounded") or [])[:6],
        "ungrounded_reproduce": "",
        "reproduce_gap_md": "",
        "retrieval_mode": "agent",
        "direction_id": direction_id,
        "scope": scope,
        "agent": True,
        "session_id": out.get("session_id") or "",
        "citations": out.get("citations") or [],
        "sources": out.get("sources") or [],
        "steps": [
            {
                "step": s.get("step"),
                "tool": s.get("tool"),
                "label": s.get("label") or s.get("tool"),
                "summary": s.get("result_summary") or "",
                "result_summary": s.get("result_summary") or "",
                "error": s.get("error") or "",
                "empty": bool(s.get("empty")),
                "empty_next": s.get("empty_next") or None,
            }
            for s in out.get("steps") or []
        ],
        "grounded_ratio": out.get("grounded_ratio"),
        "degraded": out.get("degraded") or "",
        "degraded_to": out.get("degraded_to") or ("chat" if out.get("degraded") else ""),
        "pending_confirm": out.get("pending_confirm") or [],
        "followups": out.get("followups") or [],
        "trace_id": out.get("session_id") or "",
        "available_tools": list(out.get("available_tools") or []),
        "question_type": out.get("question_type") or "",
        "ask_back": out.get("ask_back") or None,
        "relation_groups": out.get("relation_groups") or [],
    }
    if out.get("empty_next"):
        payload["empty_next"] = out["empty_next"]
    elif not hits:
        payload["empty_next"] = kb_ask_empty_next(lab_id, "no_hits", direction_id=direction_id)
    yield {"payload": payload}


@router.post("/labs/{lab_id}/ask")
def lab_ask(
    lab_id: str,
    body: KbQueryBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """实验室级 Agent 会话。stream=true 时边查边出字。"""
    from fastapi.responses import StreamingResponse

    from fengsuan.app.services.llm import sse_pack

    require_membership(db, user, lab_id)
    if body.scope == "kb":
        body = body.model_copy(update={"scope": "lab"})
    if not body.stream:
        return _agent_kb_query(db, user, lab_id, body)

    def gen():
        try:
            scope = body.scope
            from fengsuan.app.services.insight import kb_session_history
            from fengsuan.app.services.onboarding import onboarding_system_extra

            extra = onboarding_system_extra(db, lab_id) if scope == "onboarding" else ""
            explicit = (body.direction_id or "").strip()
            defaulted = ""
            if not explicit and scope == "onboarding":
                from fengsuan.app.services.directions import member_primary_direction_id

                defaulted = member_primary_direction_id(db, lab_id, user.id)
            if explicit and scope != "onboarding":
                extra = (extra + "\n" if extra else "") + "用户选了研究方向，只引用该方向（含小课题）已入库的材料。"
            direction_id = explicit or defaulted
            seed = _seed_from_context(body)
            if seed and seed[0][0] == "get_direction_brief":
                explicit = explicit or str((seed[0][1] or {}).get("direction_id") or "")
                direction_id = explicit or defaulted
            stored = []
            if not list(body.history or []):
                stored = kb_session_history(db, lab_id, user.id, body.session_id or "")
            for ev in _iter_lab_ask(
                db,
                user,
                lab_id,
                body,
                scope=scope,
                extra=extra,
                explicit=explicit,
                defaulted=defaulted,
                direction_id=direction_id,
                seed=seed,
                stored=stored,
            ):
                if "payload" in ev:
                    text = ev["payload"].get("answer") or ""
                    yield sse_pack({**ev["payload"], "done": True, "text": text, "replace": True})
                elif ev.get("delta"):
                    yield sse_pack({"delta": ev["delta"]})
                elif ev.get("tool"):
                    yield sse_pack(
                        {
                            "tool": ev.get("tool"),
                            "label": ev.get("label") or ev.get("tool"),
                            "step": ev.get("step"),
                            "summary": ev.get("result_summary") or "",
                            "result_summary": ev.get("result_summary") or "",
                        }
                    )
        except Exception as exc:
            db.rollback()
            yield sse_pack({"error": str(getattr(exc, "detail", "") or "没问出来"), "error_class": "llm"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/labs/{lab_id}/kb/query")
def kb_query(
    lab_id: str,
    body: KbQueryBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    if body.scope in {"lab", "onboarding"}:
        return _agent_kb_query(db, user, lab_id, body)
    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=lab_id,
        feature="kb_query",
        scope="lab",
        seeds=[
            ("search_lab_kb", {"query": (body.q or "")[:80]}),
            ("get_lab_kb_docs", {}),
        ],
        session_id=body.session_id or "",
        direction_id=body.direction_id or "",
    )
    hits, embed_fallback = query_kb(
        db, lab_id, body.q, body.top_k, user_id=user.id, direction_id=body.direction_id
    )
    assert_citations_are_substrings(hits)
    empty_reason = ""
    if not hits:
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
        indexed = 0
        if kb:
            from fengsuan.app.services.kb import kb_doc_ids_for_query

            if body.direction_id:
                indexed = len(kb_doc_ids_for_query(db, kb, body.direction_id))
            else:
                indexed = (
                    db.query(KbDocument)
                    .filter(KbDocument.kb_id == kb.id, KbDocument.status == "indexed", KbDocument.deleted_at.is_(None))
                    .count()
                )
        empty_reason = "no_indexed" if not indexed else "no_hits"
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="kb.query",
        object_type="kb",
        summary=body.q[:80],
        payload={"direction_id": body.direction_id} if body.direction_id else None,
    )
    db.commit()
    from fengsuan.app.services.writing import user_unused_research_note

    unused = user_unused_research_note(db, lab_id, user.id)
    context = "\n\n".join(f"[p.{h['page_no']}] {h['citation']}" for h in hits)
    answer = ""
    ungrounded = False
    from fengsuan.app.services.insight import ask_history_for_agent, kb_session_history

    stored = []
    if not list(body.history or []):
        stored = kb_session_history(db, lab_id, user.id, body.session_id or (pack or {}).get("session_id") or "")
    history_msgs = []
    for turn in ask_history_for_agent(body.history, stored):
        prev_q = str(turn.get("question") or "").strip()
        prev_a = str(turn.get("answer") or "").strip()
        if prev_q:
            history_msgs.append({"role": "user", "content": prev_q[:2000]})
        if prev_a:
            history_msgs.append({"role": "assistant", "content": prev_a[:4000]})
    if hits:
        ask_user = f"问题：{body.q}\n\n{context}"
        if unused:
            ask_user = ask_user + "\n" + unused["text"]
        answer = chat(
            db,
            user_id=user.id,
            lab_id=lab_id,
            messages=attach_evidence(
                [
                    {
                        "role": "system",
                        "content": load_prompt("kb_query"),
                    },
                    *history_msgs,
                    {"role": "user", "content": ask_user},
                ],
                pack,
            ),
            feature="kb_query",
        )
        from fengsuan.app.services.analysis import KB_GROUND_NOTE, ground_against_corpus

        corpus = "\n".join((h.get("content") or h.get("citation") or "") for h in hits)
        grounded = ground_against_corpus(answer, corpus, note=KB_GROUND_NOTE)
        from fengsuan.app.services.kb import _kb_spoken

        answer = _kb_spoken(grounded["text"], hits, body.direction_id)
        ungrounded = grounded["ungrounded"] or answer == "这些论文里还没有足够的正文"
    else:
        from fengsuan.app.services.kb import _kb_spoken

        answer = _kb_spoken("", [], body.direction_id)
    from fengsuan.app.services.insight import kb_ask_empty_next
    from fengsuan.app.services.reproduce import compose_reproduce_gap_md, language_output_warning

    spans = []
    if hits:
        spans = list(grounded.get("spans") or [])
    out = {
        "hits": hits,
        "answer": answer,
        "quota_warning": quota_warning(db, lab_id),
        "empty_reason": empty_reason,
        "embed_fallback": embed_fallback,
        "ungrounded": ungrounded,
        "ungrounded_spans": spans,
        "ungrounded_reproduce": language_output_warning(db, answer, lab_id=lab_id) if answer else "",
        "reproduce_gap_md": compose_reproduce_gap_md(db, [h.get("literature_item_id") for h in hits]),
        "retrieval_mode": (hits[0].get("retrieval_mode") if hits else ("simple" if embed_fallback == "hash" else "vector")),
        "direction_id": body.direction_id,
        "session_id": (pack or {}).get("session_id") or "",
        "trace_id": (pack or {}).get("session_id") or "",
        "degraded": "",
        "degraded_to": "",
        "pending_confirm": [],
        "citations": [
            {
                "label": f"S{i + 1}",
                "title": h.get("filename") or h.get("heading_path") or "",
                "page": h.get("page_no"),
                "kind": "page" if h.get("page_no") else "literature",
                "literature_item_id": h.get("literature_item_id"),
                "item_id": h.get("literature_item_id"),
                "href": h.get("href") or h.get("open_href") or "",
                "excerpt": (h.get("citation") or h.get("content") or "")[:240],
            }
            for i, h in enumerate(hits[:6])
        ],
        "followups": (
            (["这段依据在哪一页？"] if ungrounded else [])
            + (
                [f"第{hits[0].get('page_no')}页还讲了什么？"]
                if hits and hits[0].get("page_no")
                else []
            )
            + (
                [f"{str(hits[0].get('filename') or hits[0].get('heading_path') or '').strip()[:20]} 还写了什么？"]
                if hits and str(hits[0].get("filename") or hits[0].get("heading_path") or "").strip()
                else []
            )
        )[:3],
        "steps": list((pack or {}).get("steps") or []),
        "available_tools": list((pack or {}).get("available_tools") or []),
    }
    if unused:
        out["unused_research"] = unused
        out["honesty_notes"] = [unused]
    if empty_reason:
        out["empty_next"] = kb_ask_empty_next(lab_id, empty_reason, direction_id=body.direction_id)
    sid = ((pack or {}).get("session_id") or body.session_id or "").strip()
    if sid and (body.q or "").strip():
        from fengsuan.app.services.agent import trace

        trace.record(
            db,
            lab_id=lab_id,
            user_id=user.id,
            session_id=sid,
            feature="kb_query",
            scope="kb",
            step=len((pack or {}).get("steps") or []) + 1,
            kind="answer",
            args={"question": (body.q or "")[:500]},
            result_summary=(answer or "")[:2000],
            source_ids=[str(h.get("literature_item_id") or "") for h in hits[:6] if h.get("literature_item_id")],
        )
        db.commit()
    return out


def _require_kb(db: Session, user: User, kb_id: str):
    from fengsuan.app.models.kb import KnowledgeBase

    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(404, detail="\u77e5\u8bc6\u5e93\u4e0d\u5b58\u5728")
    require_membership(db, user, kb.lab_id)
    return kb


@router.post("/labs/{lab_id}/kb/summarize")
def kb_summarize(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> dict:
    require_membership(db, user, lab_id)
    try:
        result = summarize_lab_kb(db, lab_id, user.id, direction_id=direction_id)
    except ValueError:
        raise ClassifiedHTTPException(400, "引用必须来自原文片段", "parse") from None
    emit_activity(db, lab_id=lab_id, actor_id=user.id, verb="kb.summarize", object_type="kb", summary="内容综述")
    db.commit()
    return result


@router.post("/labs/{lab_id}/kb/feedback")
def kb_feedback(
    lab_id: str,
    body: KbFeedbackBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.models.kb import KbChunk

    chunk = db.get(KbChunk, body.chunk_id)
    if chunk is None:
        raise HTTPException(404, detail="片段不存在")
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="kb.feedback",
        object_type="kb_chunk",
        object_id=chunk.id,
        summary="有用" if body.useful else "无用",
        payload={"useful": body.useful, "q": (body.q or "")[:200]},
    )
    db.commit()
    return {"ok": True, "chunk_id": chunk.id, "useful": body.useful}


@router.get("/kb/{kb_id}/documents")
def list_docs_by_kb(
    kb_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
    page_size: int = 20,
    direction_id: str | None = None,
) -> dict:
    kb = _require_kb(db, user, kb_id)
    return list_docs(kb.lab_id, user, db, page=page, page_size=page_size, direction_id=direction_id)


@router.post("/kb/{kb_id}/documents")
def upload_doc_by_kb(
    kb_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    direction_id: str | None = Form(default=None),
) -> dict:
    kb = _require_kb(db, user, kb_id)
    return upload_doc(kb.lab_id, user, db, file, direction_id=direction_id)


@router.post("/kb/{kb_id}/query")
def kb_query_by_id(
    kb_id: str,
    body: KbQueryBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    kb = _require_kb(db, user, kb_id)
    return kb_query(kb.lab_id, body, user, db)


@router.post("/kb/{kb_id}/summarize")
def kb_summarize_by_id(
    kb_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    kb = _require_kb(db, user, kb_id)
    result = summarize_lab_kb(db, kb.lab_id, user.id)
    return result if isinstance(result, dict) else {"summary": result}


@router.get("/labs/{lab_id}/glossary")
def get_glossary(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> list[dict]:
    """术语是全实验室。查询里的 direction_id 不切表，禁止加列冒充。有记录时仍返回数组。"""
    require_membership(db, user, lab_id)
    _ = direction_id
    return list_glossary(db, lab_id)


@router.post("/labs/{lab_id}/glossary")
def add_glossary(
    lab_id: str,
    body: GlossaryCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    row = GlossaryTerm(
        lab_id=lab_id,
        src=body.src,
        tgt=body.tgt,
        lang_pair=body.lang_pair,
        do_not_translate=body.do_not_translate,
        domain=body.domain,
        created_by=user.id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, detail="该术语已存在")
    db.refresh(row)
    return {"id": row.id}


@router.delete("/glossary/{term_id}")
def delete_glossary(
    term_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    row = db.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(404, detail="术语不存在")
    membership = require_membership(db, user, row.lab_id)
    require_owner_or_teacher(membership, user, row.created_by, "仅创建者或导师可删除术语")
    db.delete(row)
    db.commit()
    return {"ok": True}


class _StandingBody(BaseModel):
    voice: str | None = None
    intro: str | None = None


@router.put("/labs/{lab_id}/assistant/standing")
def save_assistant_standing(
    lab_id: str,
    body: _StandingBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """导师改说话方式和组里要记住的一篇。学生不能改。"""
    from fengsuan.app.core.deps import require_teacher
    from fengsuan.app.models.lab import Lab
    from fengsuan.app.services.agent_standing import agent_voice, lab_intro_text, save_agent_voice, save_lab_intro

    require_teacher(db, user, lab_id)
    lab = db.get(Lab, lab_id)
    if lab is None or lab.deleted_at is not None:
        raise HTTPException(404, detail="实验室不存在")
    if body.voice is not None:
        save_agent_voice(lab, body.voice)
    if body.intro is not None:
        intro = save_lab_intro(db, lab_id, user.id, body.intro)
    else:
        intro, _chunk = lab_intro_text(db, lab_id)
    db.commit()
    return {"voice": agent_voice(lab), "intro": intro}


class AskSessionTitle(BaseModel):
    title: str


@router.patch("/labs/{lab_id}/ask/sessions/{session_id}")
def rename_ask_session(
    lab_id: str,
    session_id: str,
    body: AskSessionTitle,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import rename_ask_session as rename

    try:
        saved = rename(db, lab_id, user.id, session_id, body.title)
    except ValueError:
        raise HTTPException(400, detail="请写一个对话名称")
    if not saved:
        raise HTTPException(404, detail="这段对话不存在")
    db.commit()
    return saved


@router.delete("/labs/{lab_id}/ask/sessions/{session_id}")
def delete_ask_session(
    lab_id: str,
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import delete_ask_session as remove

    if not remove(db, lab_id, user.id, session_id):
        raise HTTPException(404, detail="这段对话不存在")
    db.commit()
    return {"ok": True}


@router.get("/labs/{lab_id}/assistant/home")
def assistant_home(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.lab_brief import assistant_home as build_home

    payload = build_home(db, lab_id, user.id)
    db.commit()
    return payload
