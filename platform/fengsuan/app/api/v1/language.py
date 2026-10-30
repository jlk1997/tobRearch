from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, require_membership
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import User
from fengsuan.app.models.kb import GlossaryTerm
from fengsuan.app.models.manuscript import Manuscript, ManuscriptVersion
from fengsuan.app.schemas.common import LanguageBody, OutlineBody, WriteSectionBody, WritebackBody
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.kb import assert_citations_are_substrings, query_kb
from fengsuan.app.services.language import (
    apply_glossary,
    extract_translation_body,
    protect_code_and_citations,
    restore_holders,
    restore_latin_spacing,
    split_readable_chunks,
    strict_translate_retry_messages,
    translation_looks_like_instructions,
    wrap_language_user,
)
from fengsuan.app.services.llm import chat, iter_chat_deltas, quota_warning, sse_pack
from fengsuan.app.services.prompts import load_prompt
from fengsuan.app.services.writing import (
    apply_writeback,
    describe_writeback,
    enrich_outline_with_cards,
    fallback_outline,
    is_related_work_heading,
    literature_aware_outline,
    literature_card_hints,
    outline_honesty,
    outline_to_markdown,
    parse_outline_json,
    related_work_context,
    section_card_honesty,
)

router = APIRouter(tags=["language"])


def _require_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise ClassifiedHTTPException(
            400,
            "请先粘贴待处理文本",
            "parse",
            next_step="请粘贴本页原文或摘要。空题名不能当正文翻译。",
        )
    return t


def _terms(db: Session, lab_id: str) -> list[dict]:
    rows = db.query(GlossaryTerm).filter(GlossaryTerm.lab_id == lab_id).all()
    return [
        {"src": t.src, "tgt": t.tgt, "do_not_translate": t.do_not_translate, "lang_pair": t.lang_pair}
        for t in rows
    ]


def _maybe_writeback(
    db: Session,
    user: User,
    manuscript_id: str | None,
    text: str,
    lab_id: str,
    *,
    mode: str = "",
    selection: str = "",
    heading: str = "",
    expected_updated_at: str | None = None,
    literature_item_ids: list[str] | None = None,
) -> dict | None:
    if not manuscript_id:
        return
    from fengsuan.app.core.concurrency import row_write_guard

    with row_write_guard(db, Manuscript, manuscript_id) as m:
        return _writeback_locked(
            db,
            user,
            m,
            text,
            lab_id,
            mode=mode,
            selection=selection,
            heading=heading,
            expected_updated_at=expected_updated_at,
            literature_item_ids=literature_item_ids,
        )


def _writeback_locked(
    db: Session,
    user: User,
    m,
    text: str,
    lab_id: str,
    *,
    mode: str = "",
    selection: str = "",
    heading: str = "",
    expected_updated_at: str | None = None,
    literature_item_ids: list[str] | None = None,
) -> dict | None:
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="手稿不存在")
    require_membership(db, user, m.lab_id)
    if m.lab_id != lab_id:
        raise HTTPException(403, detail="手稿不属于当前实验室")
    if m.owner_id != user.id:
        raise HTTPException(403, detail="仅作者可写回手稿")
    write_mode = (mode or "").strip()
    if not write_mode:
        return None
    if expected_updated_at:
        from fengsuan.app.core.concurrency import parse_iso, timestamps_match

        if parse_iso(expected_updated_at) is None:
            raise HTTPException(400, detail="期望时间戳无效")
        if not timestamps_match(expected_updated_at, m.updated_at):
            from fengsuan.app.core.concurrency import stale_write_detail

            raise HTTPException(
                409,
                detail=stale_write_detail("手稿已被更新，请刷新后再写入", href=f"/manuscripts/{m.id}", updated_at=m.updated_at),
            )
    chunk = text
    if is_related_work_heading(heading or "") and literature_item_ids:
        from fengsuan.app.models.literature import LiteratureItem
        from fengsuan.app.services.writing import stamp_related_work_mentions

        items = []
        for iid in literature_item_ids:
            item = db.get(LiteratureItem, iid)
            if item is None or item.deleted_at is not None or item.lab_id != lab_id:
                continue
            items.append(item)
        chunk = stamp_related_work_mentions(text, items)
    try:
        m.content_md = apply_writeback(
            m.content_md or "", chunk, mode=write_mode, selection=selection or "", heading=heading or ""
        )
    except ValueError as exc:
        code = str(exc)
        if code == "selection":
            raise HTTPException(400, detail="请提供要替换的选区") from exc
        if code == "selection_missing":
            raise HTTPException(400, detail="选区未在手稿中找到") from exc
        if code == "heading":
            raise HTTPException(400, detail="请指定要追加的标题") from exc
        raise HTTPException(400, detail="无法写回手稿") from exc
    last = (
        db.query(ManuscriptVersion)
        .filter(ManuscriptVersion.manuscript_id == m.id)
        .order_by(ManuscriptVersion.version.desc())
        .first()
    )
    ver = (last.version + 1) if last else 1
    db.add(ManuscriptVersion(manuscript_id=m.id, version=ver, content_md=m.content_md, created_by=user.id))
    m.updated_at = utcnow()
    emit_activity(
        db,
        lab_id=m.lab_id,
        actor_id=user.id,
        verb="manuscript.updated",
        object_type="manuscript",
        object_id=m.id,
        summary=m.title,
        object_owner_id=m.owner_id,
    )
    writeback = describe_writeback(mode=write_mode, heading=heading, selection=selection, version=ver)
    from fengsuan.app.services.reproduce import manuscript_success_warning
    from fengsuan.app.services.writing import core_literature_coverage, writeback_next_step

    return {
        "wrote": True,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        "manuscript_id": m.id,
        "href": f"/manuscripts/{m.id}",
        "title": m.title,
        "writeback": writeback,
        "related_work_written": is_related_work_heading(m.content_md or ""),
        "core_coverage": core_literature_coverage(db, m),
        "ungrounded_reproduce": manuscript_success_warning(db, m),
        "next_step": writeback_next_step(db, m),
    }


def _note_write_out(note, item, *, created: bool = False) -> dict:
    from fengsuan.app.services.literature import literature_open_href

    page = note.page_no
    if created:
        summary = f"新建笔记（第 {page} 页）" if page else "新建笔记"
        mode = "create_note"
    else:
        summary = f"替换了第 {page} 页笔记" if page else "替换了笔记"
        mode = "replace_note"
    return {
        "wrote_note": True,
        "note_id": note.id,
        "literature_item_id": item.id,
        "page_no": page,
        "href": literature_open_href(item.id, page, note_id=note.id, panel="notes"),
        "note_updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "writeback": {
            "mode": mode,
            "heading": "",
            "selection_preview": "",
            "page_no": page,
            "version": None,
            "summary": summary,
        },
    }


def _maybe_write_note(
    db: Session,
    user: User,
    note_id: str | None,
    literature_item_id: str | None,
    text: str,
    lab_id: str,
    *,
    write_note: bool = False,
    expected_updated_at: str | None = None,
    page_no: int | None = None,
) -> dict | None:
    from fengsuan.app.models.literature import LiteratureItem, LiteratureNote

    if note_id:
        from fengsuan.app.core.concurrency import row_write_guard

        with row_write_guard(db, LiteratureNote, note_id) as note:
            if note is None or note.deleted_at is not None:
                raise HTTPException(404, detail="笔记不存在")
            item = db.get(LiteratureItem, note.item_id)
            if item is None or item.deleted_at is not None:
                raise HTTPException(404, detail="文献不存在")
            require_membership(db, user, item.lab_id)
            if item.lab_id != lab_id:
                raise HTTPException(403, detail="笔记不属于当前实验室")
            if note.user_id != user.id:
                raise HTTPException(403, detail="仅作者可写回笔记")
            if expected_updated_at:
                from fengsuan.app.core.concurrency import parse_iso, timestamps_match

                if parse_iso(expected_updated_at) is None:
                    raise HTTPException(400, detail="期望时间戳无效")
                if not timestamps_match(expected_updated_at, note.updated_at):
                    from fengsuan.app.core.concurrency import stale_write_detail
                    from fengsuan.app.services.literature import literature_open_href

                    raise HTTPException(
                        409,
                        detail=stale_write_detail(
                            "笔记已被更新，请刷新后再写入",
                            href=literature_open_href(note.item_id, note.page_no, note_id=note.id),
                            updated_at=note.updated_at,
                        ),
                    )
            note.content_md = text
            if page_no is not None:
                note.page_no = page_no
            note.updated_at = utcnow()
            emit_activity(
                db,
                lab_id=item.lab_id,
                actor_id=user.id,
                verb="note.updated",
                object_type="literature",
                object_id=item.id,
                summary="语言工作台更新笔记",
                payload={"page_no": note.page_no, "note_id": note.id},
            )
            return _note_write_out(note, item, created=False)
    if not write_note or not literature_item_id:
        return None
    item = db.get(LiteratureItem, literature_item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    if item.lab_id != lab_id:
        raise HTTPException(403, detail="文献不属于当前实验室")
    note = LiteratureNote(item_id=item.id, user_id=user.id, content_md=text, page_no=page_no)
    db.add(note)
    db.flush()
    emit_activity(
        db,
        lab_id=item.lab_id,
        actor_id=user.id,
        verb="note.created",
        object_type="literature",
        object_id=item.id,
        summary="语言工作台写回笔记",
        payload={"page_no": note.page_no, "note_id": note.id},
    )
    return _note_write_out(note, item, created=True)


def _apply_language_outputs(db: Session, user: User, body, lab_id: str, result: str) -> dict:
    writing_ms = bool(body.manuscript_id and (body.write_mode or "").strip())
    writing_note = bool(body.note_id or (body.write_note and body.literature_item_id))
    if writing_ms and writing_note:
        raise HTTPException(400, detail="请只选择手稿或笔记其中一种写回")
    out: dict = {}
    wrote = _maybe_writeback(
        db,
        user,
        body.manuscript_id,
        result,
        lab_id,
        mode=body.write_mode,
        selection=body.selection,
        heading=body.heading,
        expected_updated_at=body.expected_updated_at if writing_ms else None,
        literature_item_ids=getattr(body, "literature_item_ids", None) or None,
    )
    if wrote:
        out.update(wrote)
    note = _maybe_write_note(
        db,
        user,
        body.note_id,
        body.literature_item_id,
        result,
        lab_id,
        write_note=body.write_note,
        expected_updated_at=body.expected_updated_at if writing_note and body.note_id else None,
        page_no=body.page_no,
    )
    if note:
        out.update(note)
    return out


def _language_reproduce_flag(db: Session, body, result: str, lab_id: str | None = None) -> str:
    from fengsuan.app.services.reproduce import language_output_warning

    mid = getattr(body, "manuscript_id", None)
    return language_output_warning(db, result or "", manuscript_id=mid, lab_id=lab_id) or language_output_warning(
        db, getattr(body, "text", "") or "", manuscript_id=mid, lab_id=lab_id
    )


def _emit_language_done(db: Session, lab_id: str, user: User, mode: str, wrote: dict) -> None:
    if wrote.get("wrote") or wrote.get("wrote_note"):
        return
    emit_activity(db, lab_id=lab_id, actor_id=user.id, verb="language.done", object_type="language", summary=mode)


def _unused_research_for_language(db: Session, lab_id: str, user_id: str, body, extra: str = "") -> dict | None:
    """选手稿看作者正文；生成草稿不消未点名（生成不等于写回）。"""
    from fengsuan.app.services.writing import writing_unused_research_note

    mid = getattr(body, "manuscript_id", None)
    parts: list[str] = []
    if mid:
        host = db.get(Manuscript, mid)
        if host is not None and host.deleted_at is None:
            parts.append(host.content_md or "")
    if extra:
        parts.append(extra)
    return writing_unused_research_note(
        db, lab_id, user_id, manuscript_id=mid, body="\n".join(parts)
    )


def _direction_scope_for_language(db: Session, lab_id: str, body, *, require_live: bool = False) -> tuple[list[str], bool]:
    """父方向叠子课题词。无效 direction_id 可 404；手稿已挂方向作回退。"""
    from fengsuan.app.services.directions import direction_scope_keywords

    did = getattr(body, "direction_id", None)
    if did:
        from fengsuan.app.models.lab import ResearchDirection

        d = db.get(ResearchDirection, did)
        if d is None or d.lab_id != lab_id or d.deleted_at is not None:
            if require_live:
                raise HTTPException(404, detail="方向不存在")
            return [], False
        return direction_scope_keywords(db, lab_id, did)
    mid = getattr(body, "manuscript_id", None)
    if mid:
        from fengsuan.app.models.manuscript import ManuscriptDirectionLink

        hung = [
            row.direction_id
            for row in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.manuscript_id == mid).all()
        ]
        if hung:
            return direction_scope_keywords(db, lab_id, direction_ids=hung)
    return [], False


def _language_payload(db: Session, user: User, lab_id: str, body, result: str, mode: str, *, source: str | None = None) -> dict:
    note = _apply_language_outputs(db, user, body, lab_id, result)
    _emit_language_done(db, lab_id, user, mode, note)
    db.commit()
    out = {
        "text": result,
        "mode": mode,
        "reasoning_stripped": True,
        "quota_warning": quota_warning(db, lab_id),
        **note,
        "ungrounded_reproduce": note.get("ungrounded_reproduce") or _language_reproduce_flag(db, body, result, lab_id),
    }
    if source is not None:
        out["source"] = source
    if mode == "expand":
        unused = _unused_research_for_language(
            db, lab_id, user.id, body, extra=getattr(body, "text", None) or ""
        )
        if unused:
            out["unused_research"] = unused
            notes = [n for n in (out.get("honesty_notes") or []) if isinstance(n, dict)]
            if not any(n.get("kind") == "unused_research" for n in notes):
                notes.append(unused)
            out["honesty_notes"] = notes
        keywords, includes_children = _direction_scope_for_language(db, lab_id, body)
        out["keywords"] = keywords
        out["includes_children"] = includes_children
    return out


def _language_reply(db: Session, user: User, lab_id: str, body, messages: list[dict], mode: str, *, source: str | None = None, post=None, seed=None):
    return _language_reply_many(db, user, lab_id, body, [(messages, post)], mode, source=source, seed=seed)


def _complete_language_job(db, user, lab_id, messages, post, mode: str, *, target: str = "zh"):
    result = chat(db, user_id=user.id, lab_id=lab_id, messages=messages, feature="language")
    if not post:
        return result
    try:
        return post(result)
    except ClassifiedHTTPException:
        if mode != "translate":
            raise
        again = chat(
            db,
            user_id=user.id,
            lab_id=lab_id,
            messages=strict_translate_retry_messages(messages, target),
            feature="language",
        )
        return post(again)


def _remember_language(db, user, lab_id: str, seed, mode: str, text: str) -> None:
    from fengsuan.app.services.insight import remember_own_text

    remember_own_text(
        db,
        lab_id=lab_id,
        user_id=user.id,
        session_id=((seed or {}).get("session_id") or ""),
        feature="language",
        question=mode or "译润",
        text=text or "",
        kind=mode or "text",
    )
    if (text or "").strip() and ((seed or {}).get("session_id") or "").strip():
        db.commit()


def _language_reply_many(db, user, lab_id, body, jobs: list, mode: str, *, source: str | None = None, seed=None):
    from fengsuan.app.services.agent.features import stamp_trace

    target = getattr(body, "target", None) or "zh"
    if not getattr(body, "stream", False):
        parts: list[str] = []
        for messages, post in jobs:
            parts.append(_complete_language_job(db, user, lab_id, messages, post, mode, target=target))
        pack = _language_payload(db, user, lab_id, body, "\n\n".join(parts), mode, source=source)
        pack["chunks"] = len(jobs)
        _remember_language(db, user, lab_id, seed, mode, pack.get("text") or "")
        return stamp_trace(pack, seed)

    def gen():
        parts: list[str] = []
        last_raw = ""
        try:
            total = len(jobs)
            for i, (messages, post) in enumerate(jobs):
                yield sse_pack({"chunk": i + 1, "chunks": total})
                result = yield from _stream_language_job(
                    db,
                    user,
                    lab_id,
                    messages,
                    post,
                    mode,
                    target=target,
                    parts=parts,
                    chunk=i + 1,
                    chunks=total,
                )
                last_raw = result
                parts.append(result)
                yield sse_pack({"text": "\n\n".join(parts), "replace": True, "chunk": i + 1, "chunks": total})
            payload = _language_payload(db, user, lab_id, body, "\n\n".join(parts), mode, source=source)
            payload["chunks"] = len(jobs)
            _remember_language(db, user, lab_id, seed, mode, payload.get("text") or "")
            yield sse_pack({**stamp_trace(payload, seed), "done": True})
        except ClassifiedHTTPException as exc:
            db.rollback()
            salvage = extract_translation_body("\n\n".join(parts) or last_raw, target) if mode == "translate" else ""
            pack = {"error": exc.detail, "error_class": exc.error_class}
            if exc.next_step:
                pack["next_step"] = exc.next_step
            if salvage:
                pack["partial"] = salvage
            yield sse_pack(pack)
        except Exception:
            db.rollback()
            yield sse_pack({"error": "译润未完成", "error_class": "llm"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_language_job(
    db,
    user,
    lab_id,
    messages,
    post,
    mode: str,
    *,
    target: str,
    parts: list[str],
    chunk: int,
    chunks: int,
):
    def _pump(msgs, *, retrying: bool = False):
        pieces: list[str] = []
        last_shown = ""
        if retrying:
            yield sse_pack({"chunk": chunk, "chunks": chunks, "retrying": True})
        for delta in iter_chat_deltas(db, user_id=user.id, lab_id=lab_id, messages=msgs, feature="language"):
            pieces.append(delta)
            raw_so_far = "".join(pieces)
            if mode == "translate":
                preview = extract_translation_body(raw_so_far, target)
                shown = "\n\n".join([*parts, preview] if preview else parts)
                if shown and shown != last_shown:
                    last_shown = shown
                    yield sse_pack({"text": shown, "replace": True, "chunk": chunk, "chunks": chunks})
            else:
                yield sse_pack({"delta": delta, "chunk": chunk, "chunks": chunks})
        return "".join(pieces)

    raw = yield from _pump(messages)
    if not post:
        return raw
    try:
        return post(raw)
    except ClassifiedHTTPException:
        if mode != "translate":
            raise
        raw2 = yield from _pump(strict_translate_retry_messages(messages, target), retrying=True)
        return post(raw2)


@router.post("/labs/{lab_id}/language/polish")
def polish(
    lab_id: str,
    body: LanguageBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    require_membership(db, user, lab_id)
    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=lab_id,
        feature="language",
        scope="writing",
        seeds=[
            ("search_lab_kb", {"query": (body.text or "polish")[:80]}),
            ("get_lab_glossary", {}),
        ],
        manuscript_id=body.manuscript_id or "",
        direction_id=body.direction_id or "",
    )
    protected, holders = protect_code_and_citations(_require_text(body.text))
    messages = [
        {
            "role": "system",
            "content": load_prompt(
                "language_polish",
                polish_level=body.polish_level,
                venue_style_notes=body.venue_style_notes or "无",
            ),
        },
        {"role": "user", "content": wrap_language_user(protected, getattr(body, "context", "") or "", mode="polish")},
    ]
    def _after_polish(result: str) -> str:
        restored = restore_holders(apply_glossary(result, _terms(db, lab_id)), holders)
        from fengsuan.app.services.agent.loop import narration_to_zh

        restored = narration_to_zh(body.text or "改措辞", restored)
        if translation_looks_like_instructions(restored, "en"):
            raise ClassifiedHTTPException(
                400,
                "模型没有给出改好的正文",
                "llm",
                next_step="请再点「改措辞」。不要把说明书当结果。",
            )
        return restored

    return _language_reply(
        db,
        user,
        lab_id,
        body,
        attach_evidence(messages, pack),
        "polish",
        post=_after_polish,
        seed=pack,
    )


@router.post("/labs/{lab_id}/language/translate")
def translate(
    lab_id: str,
    body: LanguageBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    require_membership(db, user, lab_id)
    from fengsuan.app.services.agent.features import try_seed_tools

    seeds: list[tuple[str, dict]] = []
    if getattr(body, "literature_item_id", None):
        seeds.append(("get_paper_page_text", {"page_no": getattr(body, "page_no", None) or 1}))
        seeds.append(("get_page_translation", {"page_no": getattr(body, "page_no", None) or 1}))
        seeds.append(("get_paper_biblio", {}))
    else:
        seeds.append(("search_lab_kb", {"query": (body.text or "translate")[:80]}))
    seeds.append(("get_lab_glossary", {}))
    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=lab_id,
        feature="language",
        scope="writing",
        seeds=seeds,
        item_id=getattr(body, "literature_item_id", None) or "",
        manuscript_id=getattr(body, "manuscript_id", None) or "",
        direction_id=getattr(body, "direction_id", None) or "",
    )
    terms = _terms(db, lab_id)
    raw = restore_latin_spacing(_require_text(body.text))
    given_ctx = restore_latin_spacing((getattr(body, "context", None) or "").strip())
    parts = [raw] if given_ctx or len(raw) <= 2200 else split_readable_chunks(raw)
    jobs = []
    prev_tail = given_ctx
    for i, part in enumerate(parts):
        protected, holders = protect_code_and_citations(part)
        for j, term in enumerate(t for t in terms if t.get("do_not_translate")):
            token = f"⟦LOCK{j}⟧"
            holders.append((token, term["src"]))
            protected = protected.replace(term["src"], token)
        ctx = given_ctx if i == 0 and given_ctx else (prev_tail if i else "")
        messages = [
            {
                "role": "system",
                "content": load_prompt("language_translate", target=body.target, terms=terms),
            },
            {"role": "user", "content": wrap_language_user(protected, ctx)},
        ]

        def _after(result: str, holders=holders) -> str:
            restored = restore_holders(apply_glossary(result, terms), holders)
            cleaned = extract_translation_body(restored, body.target)
            if not cleaned or translation_looks_like_instructions(cleaned, body.target):
                raise ClassifiedHTTPException(
                    400,
                    "模型没有给出译文",
                    "llm",
                    next_step="请再点「译这一页」。不要把说明书当译文。",
                )
            return cleaned

        # 页译不要把英文原文/书目再贴进 user：那会诱导模型复述规范并夹带英文。
        jobs.append((messages, _after))
        prev_tail = part[-220:]
    return _language_reply_many(
        db,
        user,
        lab_id,
        body,
        jobs,
        "translate",
        source=body.text,
        seed=pack,
    )


@router.post("/labs/{lab_id}/language/expand")
def expand(
    lab_id: str,
    body: LanguageBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    require_membership(db, user, lab_id)
    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    seeds = [
        ("search_lab_kb", {"query": (body.text or body.expand_goal or "expand")[:80]}),
        ("get_lab_glossary", {}),
    ]
    for iid in (body.literature_item_ids or [])[:3]:
        seeds.append(("get_method_card", {"item_id": iid}))
    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=lab_id,
        feature="language",
        scope="writing",
        seeds=seeds,
        manuscript_id=body.manuscript_id or "",
        direction_id=body.direction_id or "",
    )
    messages = [
        {"role": "system", "content": load_prompt("language_expand", expand_goal=body.expand_goal)},
        {"role": "user", "content": _require_text(body.text)},
    ]
    if body.expand_goal == "related_work":
        patch: dict = {}
        if not (body.heading or "").strip() or body.heading == "语言工作台":
            patch["heading"] = "相关工作"
        if (body.write_mode or "").strip() == "append":
            patch["write_mode"] = "append_heading"
            patch.setdefault("heading", "相关工作")
        if patch:
            body = body.model_copy(update=patch)
        unused = _unused_research_for_language(db, lab_id, user.id, body, extra=body.text or "")
        if unused:
            messages[0]["content"] = (messages[0].get("content") or "") + "\n" + unused["text"]
        keywords, includes_children = _direction_scope_for_language(db, lab_id, body, require_live=True)
        if keywords:
            label = "这个大方向（含小课题）关键词" if includes_children else "本方向关键词"
            messages[0]["content"] = (messages[0].get("content") or "") + f"\n{label}：{'、'.join(keywords)}"
    return _language_reply(db, user, lab_id, body, attach_evidence(messages, pack), "expand", seed=pack)


@router.get("/labs/{lab_id}/writing/latest")
def writing_latest(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import latest_own_text

    return latest_own_text(db, lab_id, user.id, "writing", kind="outline")


@router.get("/labs/{lab_id}/language/latest")
def language_latest(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import latest_own_text

    return latest_own_text(db, lab_id, user.id, "language")


@router.get("/labs/{lab_id}/writing/section/latest")
def writing_section_latest(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import latest_own_text

    return latest_own_text(db, lab_id, user.id, "writing", kind="section")


@router.post("/labs/{lab_id}/writing/outline")
def outline(
    lab_id: str,
    body: OutlineBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.models.literature import LiteratureItem

    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    seeds = [("search_lab_literature", {"query": (body.title or "outline")[:80]})]
    for iid in (body.literature_item_ids or [])[:3]:
        seeds.append(("get_method_card", {"item_id": iid}))
    if body.literature_item_ids:
        seeds.append(("get_related_work", {"item_ids": list(body.literature_item_ids)[:8]}))
    if getattr(body, "manuscript_id", None):
        seeds.append(("get_core_coverage", {}))
        seeds.append(("get_manuscript_versions", {}))
        seeds.append(("get_unused_research", {}))
    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=lab_id,
        feature="writing",
        scope="writing",
        seeds=seeds,
        manuscript_id=getattr(body, "manuscript_id", None) or "",
        direction_id=getattr(body, "direction_id", None) or "",
    )
    paper_titles: list[str] = []
    for iid in body.literature_item_ids or []:
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None or item.lab_id != lab_id:
            raise HTTPException(404, detail="文献不属于当前实验室")
        paper_titles.append(item.title)
    keywords: list[str] = []
    includes_children = False
    if body.direction_id:
        keywords, includes_children = _direction_scope_for_language(db, lab_id, body, require_live=True)
    card_hints = literature_card_hints(db, lab_id, body.literature_item_ids or [])
    fallback = (
        literature_aware_outline(body.kind, body.title or "未命名", paper_titles, keywords, card_hints=card_hints)
        if paper_titles or keywords or card_hints
        else fallback_outline(body.kind, body.title or "未命名")
    )
    extra = chat(
        db,
        user_id=user.id,
        lab_id=lab_id,
        messages=attach_evidence(
            [
                {
                    "role": "system",
                    "content": load_prompt("writing_outline"),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "kind": body.kind,
                            "title": body.title,
                            "papers": paper_titles,
                            "keywords": keywords,
                            "method_cards": card_hints,
                            "skeleton": fallback,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            pack,
        ),
        feature="writing",
        json_mode=True,
    )
    tree = parse_outline_json(extra, fallback)
    blob = json.dumps(tree, ensure_ascii=False)
    if paper_titles:
        if not any(t[:24] in blob for t in paper_titles) and "文献" not in blob:
            tree = fallback
    else:
        tree = fallback
    tree = enrich_outline_with_cards(tree, card_hints)
    md = outline_to_markdown(tree)
    used_card = any(h.get("has_card") for h in card_hints)
    tree["used_method_card"] = used_card
    used_repro = any(h.get("has_reproduce") for h in card_hints)
    tree["used_reproduce"] = used_repro
    unused = _unused_research_for_language(db, lab_id, user.id, body)
    honesty = outline_honesty(
        paper_titles=paper_titles,
        used_card=used_card,
        used_repro=used_repro,
        unused_research=unused,
        missing_cards=[h for h in card_hints if not h.get("has_card")],
    )
    note = honesty["notes"]
    wrote = None
    if body.manuscript_id and (body.write_mode or "").strip():
        wrote = _maybe_writeback(
            db,
            user,
            body.manuscript_id,
            md,
            lab_id,
            mode=body.write_mode,
            selection=body.selection,
            heading=body.heading,
            expected_updated_at=body.expected_updated_at,
        )
    if not body.manuscript_id:
        emit_activity(
            db,
            lab_id=lab_id,
            actor_id=user.id,
            verb="writing.outline",
            object_type="writing",
            object_id="",
            summary=body.title or "outline",
        )
    from fengsuan.app.services.insight import remember_own_text

    title = body.title or "大纲"
    try:
        full = json.dumps(
            {"markdown": md, "outline": tree, "title": title, "truncated": False},
            ensure_ascii=False,
        )
    except TypeError:
        full = ""
    if not full or len(full) > 12000:
        full = json.dumps({"markdown": (md or "")[:8000], "title": title, "truncated": True}, ensure_ascii=False)
    remember_own_text(
        db,
        lab_id=lab_id,
        user_id=user.id,
        session_id=((pack or {}).get("session_id") or ""),
        feature="writing",
        question=title,
        text=full,
        kind="outline",
    )
    db.commit()
    out = {
        "outline": tree,
        "notes": note,
        "markdown": md,
        "used_literature": bool(tree.get("used_literature")),
        "used_method_card": bool(tree.get("used_method_card")),
        "used_reproduce": used_repro,
        "honesty_notes": honesty["honesty_notes"],
        "quota_warning": quota_warning(db, lab_id),
        "keywords": keywords,
        "includes_children": includes_children,
    }
    if wrote:
        out.update(wrote)
    from fengsuan.app.services.reproduce import language_output_warning

    out["ungrounded_reproduce"] = (wrote or {}).get("ungrounded_reproduce") or language_output_warning(
        db, md, manuscript_id=getattr(body, "manuscript_id", None), lab_id=lab_id
    )
    if not out.get("next_step"):
        from fengsuan.app.services.writing import writing_draft_next_step

        out["next_step"] = writing_draft_next_step(lab_id, body.manuscript_id, stage="outline")
    from fengsuan.app.services.agent.features import stamp_trace

    return stamp_trace(out, pack)


@router.post("/labs/{lab_id}/writing/section")
def write_section(
    lab_id: str,
    body: WriteSectionBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    seeds = [("search_lab_kb", {"query": (body.heading or body.notes or "section")[:80]})]
    for iid in (body.literature_item_ids or [])[:3]:
        seeds.append(("get_method_card", {"item_id": iid}))
    if body.literature_item_ids:
        seeds.append(("get_related_work", {"item_ids": list(body.literature_item_ids)[:8]}))
    if getattr(body, "manuscript_id", None):
        seeds.append(("get_core_coverage", {}))
        seeds.append(("get_manuscript_versions", {}))
    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=lab_id,
        feature="writing",
        scope="writing",
        seeds=seeds,
        manuscript_id=getattr(body, "manuscript_id", None) or "",
        direction_id=getattr(body, "direction_id", None) or "",
    )
    keywords, includes_children = _direction_scope_for_language(db, lab_id, body, require_live=bool(body.direction_id))
    q = body.heading + " " + body.notes
    if keywords:
        q = f"{q} {' '.join(keywords)}"
    hits, _fallback = query_kb(db, lab_id, q, top_k=6, user_id=user.id, direction_id=body.direction_id)
    lit_refs: list[dict] = []
    extra_bits: list[str] = []
    if body.literature_item_ids:
        from fengsuan.app.models.literature import LiteratureItem
        from fengsuan.app.services.reproduce import evidence_brief, latest_reproduce_evidence

        for iid in body.literature_item_ids:
            item = db.get(LiteratureItem, iid)
            if item is None or item.lab_id != lab_id or item.deleted_at is not None:
                raise HTTPException(404, detail="文献不属于当前实验室")
            extra_bits.append(f"{item.title} {item.abstract or ''}")
            ev = latest_reproduce_evidence(db, item.id)
            brief = evidence_brief(ev)
            extra_bits.append(brief)
            if ev and ev.get("measured"):
                extra_bits.append(json.dumps(ev.get("actual_metrics") or {}, ensure_ascii=False))
            lit_refs.append({"id": item.id, "title": item.title, "reproduce": ev, "reproduce_brief": brief})
        if extra_bits:
            more, _ = query_kb(
                db, lab_id, " ".join(extra_bits)[:500], top_k=4, user_id=user.id, direction_id=body.direction_id
            )
            seen = {h["chunk_id"] for h in hits}
            for h in more:
                if h["chunk_id"] not in seen:
                    hits.append(h)
    related_ctx = {"hints": [], "used_method_card": False, "used_reproduce": False, "no_fulltext_titles": []}
    if body.literature_item_ids:
        related_ctx = related_work_context(db, lab_id, body.literature_item_ids)
        extra_bits.append(json.dumps(related_ctx.get("hints") or [], ensure_ascii=False))
        if related_ctx.get("no_fulltext_titles"):
            extra_bits.append("无全文仅摘要：" + "；".join(related_ctx["no_fulltext_titles"][:8]))
    unused_research = _unused_research_for_language(db, lab_id, user.id, body)
    if unused_research:
        extra_bits.append(unused_research["text"])
    if keywords:
        extra_bits.append(("这个大方向（含小课题）关键词：" if includes_children else "本方向关键词：") + "、".join(keywords))
    try:
        assert_citations_are_substrings(hits)
    except ValueError:
        raise ClassifiedHTTPException(400, "引用必须来自原文片段", "parse") from None
    cites = "\n".join(f"[p.{h['page_no']}] {h['citation']}" for h in hits)
    rewrite_bits = ""
    prior = (body.prior_draft or "").strip()
    if body.rewrite or prior:
        rewrite_bits = "请改写上一稿，不要另起一章。保持同一章节标题与论据来源。\n"
        if prior:
            rewrite_bits += f"上一稿：\n{prior[:8000]}\n"
    text = chat(
        db,
        user_id=user.id,
        lab_id=lab_id,
        messages=attach_evidence(
            [
                {"role": "system", "content": load_prompt("writing_section")},
                {
                    "role": "user",
                    "content": f"章节：{body.heading}\n备注：{body.notes}\n{rewrite_bits}已选文献：{json.dumps(lit_refs, ensure_ascii=False)}\n方法卡与复现：{json.dumps(related_ctx, ensure_ascii=False)}\n{cites}",
                },
            ],
            pack,
        ),
        feature="writing",
    )
    from fengsuan.app.services.analysis import KB_GROUND_NOTE, ground_against_corpus

    corpus = "\n".join(
        [(h.get("content") or h.get("citation") or "") for h in hits]
        + [f"{r.get('title') or ''} {r.get('id') or ''}" for r in lit_refs]
        + extra_bits
    )
    grounded = ground_against_corpus(text, corpus, note=KB_GROUND_NOTE)
    text = grounded["text"]
    from fengsuan.app.services.agent.loop import narration_to_zh

    text = narration_to_zh(body.heading or "写这一节", text)
    if is_related_work_heading(body.heading) and lit_refs:
        from fengsuan.app.services.writing import stamp_related_work_mentions

        text = stamp_related_work_mentions(text, lit_refs)
    added_cites = 0
    if body.manuscript_id and lit_refs:
        from fengsuan.app.models.manuscript import ManuscriptCitation

        host = db.get(Manuscript, body.manuscript_id)
        if host is None or host.deleted_at is not None or host.lab_id != lab_id:
            raise HTTPException(404, detail="手稿不存在")
        require_membership(db, user, host.lab_id)
        if host.owner_id != user.id:
            raise HTTPException(403, detail="仅作者可改手稿引用")
        existing = {
            r.literature_item_id
            for r in db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == body.manuscript_id).all()
        }
        for ref in lit_refs:
            iid = ref.get("id")
            if not iid or iid in existing:
                continue
            key = "".join(ch for ch in (ref.get("title") or "ref") if ch.isalnum())[:24].lower() or "ref"
            db.add(ManuscriptCitation(manuscript_id=body.manuscript_id, literature_item_id=iid, citation_key=key))
            existing.add(iid)
            added_cites += 1
    wrote = None
    if body.manuscript_id and (body.write_mode or "").strip():
        wrote = _maybe_writeback(
            db,
            user,
            body.manuscript_id,
            text,
            lab_id,
            mode=body.write_mode,
            selection=body.selection,
            heading=body.heading,
            expected_updated_at=body.expected_updated_at,
            literature_item_ids=body.literature_item_ids,
        )
    if not wrote:
        emit_activity(
            db,
            lab_id=lab_id,
            actor_id=user.id,
            verb="writing.section",
            object_type="writing",
            object_id="",
            summary=body.heading,
        )
    from fengsuan.app.services.insight import remember_result_json

    remember_result_json(
        db,
        lab_id=lab_id,
        user_id=user.id,
        session_id=((pack or {}).get("session_id") or ""),
        feature="writing",
        question=(body.heading or "章节")[:180],
        kind="section",
        payload={"markdown": text or "", "heading": body.heading or "", "title": body.heading or "章节"},
    )
    db.commit()
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_open_href, stamp_reproduce_href

    stamp_items_open_href(db, user.id, lit_refs, id_key="id")
    stamp_open_href(hits, id_key="literature_item_id")
    stamp_reproduce_href(lab_id, lit_refs, id_key="id")
    stamp_reproduce_href(lab_id, hits, id_key="literature_item_id")
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, lit_refs, id_key="id")
    stamp_latest_reproduce(db, hits, id_key="literature_item_id")
    hint_by = {h.get("id"): h for h in related_ctx.get("hints") or [] if isinstance(h, dict) and h.get("id")}
    for ref in lit_refs:
        h = hint_by.get(ref.get("id")) or {}
        ref["has_method_card"] = bool(h.get("has_card"))
    card_note = section_card_honesty(related_ctx)
    ms = (body.manuscript_id or "").strip()
    if ms:
        for row in (*lit_refs, *hits):
            href = row.get("href")
            if isinstance(href, str) and href.startswith("/") and "ms=" not in href:
                row["href"] = f"{href}{'&' if '?' in href else '?'}ms={ms}"
    out = {
        "text": text,
        "citations": hits,
        "literature": lit_refs,
        "citations_added": added_cites,
        "ungrounded": bool(grounded.get("ungrounded")),
        "ungrounded_spans": list(grounded.get("spans") or []),
        "quota_warning": quota_warning(db, lab_id),
        "related_work": is_related_work_heading(body.heading),
        "used_method_card": bool(related_ctx.get("used_method_card")),
        "used_reproduce": bool(related_ctx.get("used_reproduce")),
        "no_fulltext_titles": related_ctx.get("no_fulltext_titles") or [],
        "unused_research": unused_research,
        "honesty_notes": ([unused_research] if unused_research else []) + ([card_note] if card_note else []),
        "keywords": keywords,
        "includes_children": includes_children,
    }
    if wrote:
        out.update(wrote)
        unused_research = _unused_research_for_language(db, lab_id, user.id, body)
        out["unused_research"] = unused_research
        out["honesty_notes"] = ([unused_research] if unused_research else []) + ([card_note] if card_note else [])
    from fengsuan.app.services.reproduce import language_output_warning

    out["ungrounded_reproduce"] = (wrote or {}).get("ungrounded_reproduce") or language_output_warning(
        db, text, manuscript_id=body.manuscript_id, lab_id=lab_id
    )
    if body.manuscript_id:
        host = db.get(Manuscript, body.manuscript_id)
        if host is not None and host.deleted_at is None:
            from fengsuan.app.services.writing import core_literature_coverage

            out["core_coverage"] = core_literature_coverage(db, host)
    if not out.get("next_step"):
        from fengsuan.app.services.writing import writing_draft_next_step

        out["next_step"] = writing_draft_next_step(lab_id, body.manuscript_id)
    from fengsuan.app.services.agent.features import stamp_trace

    return stamp_trace(out, pack)


@router.post("/labs/{lab_id}/writing/writeback")
def writing_writeback(
    lab_id: str,
    body: WritebackBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    wrote = _maybe_writeback(
        db,
        user,
        body.manuscript_id,
        body.text,
        lab_id,
        mode=body.write_mode,
        selection=body.selection,
        heading=body.heading,
        expected_updated_at=body.expected_updated_at,
        literature_item_ids=body.literature_item_ids,
    )
    if not wrote:
        raise HTTPException(400, detail="未写入手稿")
    db.commit()
    return wrote


def _lab_id_or_400(body) -> str:
    lab_id = getattr(body, "lab_id", None)
    if not lab_id:
        raise HTTPException(400, detail="\u7f3a\u5c11 lab_id")
    return lab_id


@router.post("/language/polish")
def polish_alias(
    body: LanguageBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return polish(_lab_id_or_400(body), body, user, db)


@router.post("/language/translate")
def translate_alias(
    body: LanguageBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return translate(_lab_id_or_400(body), body, user, db)


@router.post("/language/expand")
def expand_alias(
    body: LanguageBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return expand(_lab_id_or_400(body), body, user, db)


@router.post("/writing/outline")
def outline_alias(
    body: OutlineBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return outline(_lab_id_or_400(body), body, user, db)


@router.post("/writing/section")
def write_section_alias(
    body: WriteSectionBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return write_section(_lab_id_or_400(body), body, user, db)
