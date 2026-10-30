from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.deps import get_current_user, require_membership, require_owner_or_teacher, require_teacher
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.safepath import read_upload_limited, safe_filename, write_bytes_under
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import User
from fengsuan.app.models.literature import (
    BrowserCapture,
    LiteratureDirectionLink,
    LiteratureFile,
    LiteratureItem,
    LiteratureNote,
    LiteratureReading,
)
from fengsuan.app.schemas.common import (
    ImportBibtexBody,
    ImportCaptureBody,
    ImportCsvBody,
    ImportDoiBody,
    LiteratureManual,
    LiteratureSearchBody,
    MarkReadBody,
    PinCoreBody,
    NoteCreate,
    NotePatch,
    AnalyzeBody,
    CompareBody,
    PageTranslationBody,
    ReadingProgressBody,
    TutorBody,
)
from fengsuan.app.services.activity import emit_activity, emit_audit
from fengsuan.app.services.literature import (
    build_literature_detail,
    fetch_crossref,
    backfill_file_hashes,
    collapse_same_hash_items,
    find_by_content_hash,
    find_duplicate,
    item_to_bibtex,
    note_dto,
    paper_dto,
    parse_bibtex,
    reading_fields,
    recommend_for_lab,
    search_sources,
)
from fengsuan.app.services.pagecache import store_pages, store_structure
from fengsuan.app.services.web.common import SourceFailures
from fengsuan.app.services.pdfparse import extract_pdf_full, filename_title, guess_literature_title, looks_like_running_header

router = APIRouter(tags=["literature"])

READ_LEVEL_FILTER = {
    "none": (-1, 0),
    "abstract": (1,),
    "full": (2, 3),
    "deep": (3,),
}


def _note_dict(db: Session, n: LiteratureNote) -> dict:
    return note_dto(db, n)


def _attach_pdf(
    db: Session,
    item: LiteratureItem,
    lab_id: str,
    content: bytes,
    filename: str,
    mime: str = "application/pdf",
    sha256: str | None = None,
    index_now: bool = True,
) -> LiteratureFile:
    dest_dir = Path(get_settings().file_root) / "files" / lab_id / "literature" / item.id
    dest = write_bytes_under(dest_dir, filename or "paper.pdf", content, default="paper.pdf")
    pages, parse_status, parse_error, structure = extract_pdf_full(content)
    page_count = len(pages) if pages else None
    digest = (sha256 or hashlib.sha256(content).hexdigest()).lower()
    guessed = guess_literature_title(pages, filename, item.title or "")
    placeholder = (item.title or "").strip().lower() in {"", "untitled", "uploaded pdf"}
    from_name = item.title == filename_title(filename)
    if guessed and (placeholder or from_name or looks_like_running_header(item.title or "")):
        item.title = guessed.replace("\x00", "")[:200]
    row = LiteratureFile(
        item_id=item.id,
        file_path=str(dest),
        filename=safe_filename(filename or "paper.pdf", "paper.pdf"),
        mime=mime or "application/pdf",
        size=len(content),
        page_count=page_count,
        parse_status=parse_status,
        parse_error=parse_error,
        content_sha256=digest,
    )
    db.add(row)
    # 上传时已经解析过一遍，顺手落库，学生第一次翻页就不必再解析。
    if parse_status != "unusable" and any((p or "").strip() for p in pages):
        db.flush()
        store_pages(db, row, pages)
        store_structure(row, structure)
    if not index_now:
        return row
    try:
        from fengsuan.app.services.kb import index_literature_if_readable

        index_literature_if_readable(db, item, item.added_by or "")
    except Exception:
        pass
    return row


def _create_item(db: Session, lab_id: str, user_id: str, data: dict, channel: str, mark_read: bool) -> tuple[LiteratureItem, bool]:
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        return _create_item_locked(db, lab_id, user_id, data, channel, mark_read)


def _create_item_locked(db: Session, lab_id: str, user_id: str, data: dict, channel: str, mark_read: bool) -> tuple[LiteratureItem, bool]:
    dup = find_duplicate(
        db,
        lab_id,
        doi=data.get("doi"),
        title=data.get("title") or "",
        year=data.get("publish_year"),
        arxiv_id=data.get("arxiv_id"),
        pubmed_id=data.get("pubmed_id"),
        cnki_id=data.get("cnki_id"),
        title_match=channel not in {"upload", "manuscript"},
    )
    if dup:
        if data.get("doi") and not dup.doi:
            dup.doi = data.get("doi")
        if data.get("arxiv_id") and not dup.arxiv_id:
            dup.arxiv_id = data.get("arxiv_id")
        if data.get("pubmed_id") and not dup.pubmed_id:
            dup.pubmed_id = data.get("pubmed_id")
        if data.get("cnki_id") and not dup.cnki_id:
            dup.cnki_id = data.get("cnki_id")
        if data.get("abstract") and not (dup.abstract or "").strip():
            dup.abstract = data.get("abstract")
        if data.get("url") and not dup.url:
            dup.url = data.get("url")
        if data.get("venue") and not dup.venue:
            dup.venue = data.get("venue")
        if data.get("publish_year") and not dup.publish_year:
            dup.publish_year = data.get("publish_year")
        authors = data.get("authors") or []
        if authors and (not (dup.authors_json or "").strip() or dup.authors_json in ("[]", "null")):
            dup.authors_json = json.dumps(authors, ensure_ascii=False)
        item = dup
        already = True
    else:
        item = LiteratureItem(
            lab_id=lab_id,
            title=data.get("title") or "untitled",
            authors_json=json.dumps(data.get("authors") or [], ensure_ascii=False),
            abstract=data.get("abstract") or "",
            doi=data.get("doi"),
            arxiv_id=data.get("arxiv_id"),
            pubmed_id=data.get("pubmed_id"),
            cnki_id=data.get("cnki_id"),
            url=data.get("url"),
            venue=data.get("venue"),
            venue_type=data.get("venue_type") or "other",
            language=data.get("language") or "en",
            publish_year=data.get("publish_year"),
            added_by=user_id,
            source_channel=channel,
        )
        db.add(item)
        db.flush()
        already = False
        emit_activity(
            db,
            lab_id=lab_id,
            actor_id=user_id,
            verb="literature.added",
            object_type="literature",
            object_id=item.id,
            summary=item.title,
        )
    if data.get("openalex_id") and not item.openalex_id:
        item.openalex_id = str(data.get("openalex_id"))[:40]
    if data.get("citation_count") is not None and item.cited_by_count is None:
        try:
            item.cited_by_count = int(data.get("citation_count"))
        except (TypeError, ValueError):
            pass
    details = [d for d in (data.get("author_details") or []) if isinstance(d, dict) and d.get("name")]
    try:
        from fengsuan.app.services.biblio import link_item_as_cited, sync_authorships

        with db.begin_nested():
            sync_authorships(db, item, details or None, evidence_source="search" if details else "")
            if not already:
                link_item_as_cited(db, item)
    except Exception:
        pass
    if mark_read:
        row, prev = _upsert_reading(db, item.id, user_id, status="reading")
        _emit_reading_transition(db, item, user_id, prev, row.status)
    return item, already


def _link_dirs(db: Session, item: LiteratureItem, direction_ids: list[str] | None) -> None:
    for did in direction_ids or []:
        exists = (
            db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.item_id == item.id, LiteratureDirectionLink.direction_id == did)
            .first()
        )
        if exists is None:
            db.add(LiteratureDirectionLink(item_id=item.id, direction_id=did))


def _explicit_dir_ids(*parts) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part is None:
            continue
        vals = [part] if isinstance(part, str) else list(part)
        for raw in vals:
            did = str(raw or "").strip()
            if did and did not in seen:
                seen.add(did)
                out.append(did)
    return out


def _hang_import_dirs(db: Session, lab_id: str, user_id: str, item: LiteratureItem, dto: dict, explicit=None) -> None:
    from fengsuan.app.services.literature import _live_lab_directions, resolve_import_direction_ids, stamp_direction_hook

    ids, via = resolve_import_direction_ids(
        db,
        lab_id,
        user_id,
        _explicit_dir_ids(explicit),
        title=item.title or "",
        abstract=item.abstract or "",
    )
    _link_dirs(db, item, ids)
    db.flush()
    from fengsuan.app.services.kb import index_literature_if_readable

    index_literature_if_readable(db, item, user_id)
    linked = [
        r.direction_id
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    ]
    dto["direction_ids"] = linked
    dto["linked_via"] = via
    names = {d.id: d.name for d in _live_lab_directions(db, lab_id)}
    dto["auto_placed"] = [
        {"id": did, "name": names.get(did) or ""}
        for did, how in (via or {}).items()
        if how in {"match", "primary"}
    ]
    stamp_direction_hook(db, lab_id, dto, title=item.title or "", abstract=item.abstract or "", linked=linked)


def _upsert_reading(
    db: Session,
    item_id: str,
    user_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    last_page_no: int | None = None,
) -> tuple[LiteratureReading, str | None]:
    def _load() -> LiteratureReading | None:
        return (
            db.query(LiteratureReading)
            .filter(LiteratureReading.item_id == item_id, LiteratureReading.user_id == user_id)
            .first()
        )

    row = _load()
    prev = row.status if row is not None else None
    if row is None:
        try:
            with db.begin_nested():
                row = LiteratureReading(item_id=item_id, user_id=user_id)
                db.add(row)
                row.status = status or "reading"
                if progress is not None:
                    row.progress_pct = max(0.0, min(100.0, float(progress)))
                if last_page_no is not None:
                    row.last_page_no = last_page_no
                row.last_opened_at = utcnow()
                db.flush()
                return row, prev
        except IntegrityError:
            row = _load()
            if row is None:
                raise
            prev = row.status
    if status is not None:
        row.status = status
    if progress is not None:
        row.progress_pct = max(0.0, min(100.0, float(progress)))
    if last_page_no is not None:
        row.last_page_no = last_page_no
    row.last_opened_at = utcnow()
    return row, prev


def _emit_reading_transition(db: Session, item: LiteratureItem, user_id: str, prev: str | None, status: str) -> None:
    if status == "finished" and prev != "finished":
        verb = "reading.finished"
    elif status == "reading" and prev != "reading":
        verb = "reading.started"
    else:
        return
    emit_activity(
        db,
        lab_id=item.lab_id,
        actor_id=user_id,
        verb=verb,
        object_type="literature",
        object_id=item.id,
        summary=item.title,
    )


@router.post("/labs/{lab_id}/literature/search")
def search_literature(
    lab_id: str,
    body: LiteratureSearchBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.services.literature import cached_search_sources, rewrite_query, search_empty_next, search_local, stamp_search_library

    mem = require_membership(db, user, lab_id)
    if not (body.q or "").strip():
        return {
            "items": [],
            "rewritten_q": "",
            "sources_used": [],
            "includes_children": False,
            "cached": False,
            "keyword_hints": [],
            "local_count": 0,
            "external_count": 0,
        }
    includes_children = False
    if body.direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, body.direction_id)
        includes_children = len(scope) > 1
    q = rewrite_query(
        db,
        lab_id,
        body.q,
        direction_id=body.direction_id,
        user_id=user.id,
        role=mem.role,
    )
    wanted = list(body.sources or ["arxiv"])
    if body.language and str(body.language).lower().startswith("zh") and "tavily" not in wanted:
        wanted.append("tavily")
    # D-HONEST：这一轮把源的失败记下来，空结果要能区分「没有」和「没查成」。
    with SourceFailures.scoped() as failures:
        ext, cached = cached_search_sources(
            q, [s for s in wanted if s != "lab"], body.max_per_source, body.language
        )
    failed = failures.as_dict()
    hits = list(ext)
    if "lab" in wanted:
        hits = search_local(db, lab_id, body.q) + hits
    if body.year:
        hits = [h for h in hits if not h.get("year") or h.get("year") == body.year]
    stamp_search_library(db, lab_id, hits)
    from openlens_ai.utils.paper_dedupe import merge_versions

    before_merge = len(hits)
    hits = merge_versions(hits)
    version_merged = max(0, before_merge - len(hits))
    local_count = 0
    if body.direction_id and (body.q or "").strip():
        from fengsuan.app.models.lab import ResearchDirection
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope
        from fengsuan.app.services.literature import score_against_corpus, tokenize

        scope = resolve_direction_scope(db, lab_id, body.direction_id) or [body.direction_id]
        local_count = (
            db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.direction_id.in_(scope))
            .count()
        )
        dirs = (
            db.query(ResearchDirection)
            .filter(ResearchDirection.id.in_(scope), ResearchDirection.deleted_at.is_(None))
            .all()
        )
        parts: list[str] = []
        for d in dirs:
            parts.append(d.name or "")
            try:
                kws = json.loads(d.keywords_json or "[]")
            except Exception:
                kws = []
            if isinstance(kws, list):
                parts.extend(str(k) for k in kws if k)
        corpus = " ".join(parts)
        tokens = tokenize(corpus)
        if tokens:
            for hit in hits:
                _score, matched = score_against_corpus(hit.get("title") or "", hit.get("abstract") or "", corpus, tokens)
                if matched:
                    hit["why"] = "、".join(matched[:4])
    external_count = sum(1 for hit in hits if not hit.get("already_in_library") and hit.get("source") != "lab")
    out = {
        "items": hits,
        "rewritten_q": q,
        "local_count": local_count,
        "external_count": external_count,
        "sources_used": wanted,
        "includes_children": includes_children,
        "cached": cached,
        "keyword_hints": [],
        "failed_sources": failed,
        "version_merged": version_merged,
        "version_note": f"预印本和正式版并掉了 {version_merged} 条，留下正式版。" if version_merged else "",
    }
    if (mem.role or "") == "teacher":
        from fengsuan.app.services.literature import classify_unlinked_papers, leftover_keyword_hints

        _hangable, leftover_rows = classify_unlinked_papers(db, lab_id)
        out["keyword_hints"] = leftover_keyword_hints(db, lab_id, leftover_rows, limit=8)
    if not hits:
        out["empty_next"] = search_empty_next(
            lab_id,
            body.q,
            sources=wanted,
            direction_id=body.direction_id,
            includes_children=includes_children,
            failed_sources=failed,
        )
    return out


@router.post("/labs/{lab_id}/literature")
def create_literature(
    lab_id: str,
    body: LiteratureManual,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    item, already = _create_item(db, lab_id, user.id, body.model_dump(), "search", body.mark_read)
    dto = paper_dto(item)
    dto["already_in_library"] = already
    if body.fetch_oa_pdf and body.oa_pdf_url:
        from fengsuan.app.models.literature import LiteratureFile

        has_file = db.query(LiteratureFile.id).filter(LiteratureFile.item_id == item.id).first() is not None
        if not has_file:
            try:
                from fengsuan.app.services.paper_import import _download

                blob = _download(body.oa_pdf_url)
                _attach_pdf(db, item, lab_id, blob, "oa.pdf")
                dto["pdf"] = "attached"
            except Exception:
                import json

                try:
                    ids = json.loads(item.external_ids_json or "{}")
                except json.JSONDecodeError:
                    ids = {}
                if not isinstance(ids, dict):
                    ids = {}
                ids["oa_pdf"] = body.oa_pdf_url[:500]
                item.external_ids_json = json.dumps(ids, ensure_ascii=False)
                dto["pdf"] = "queued"
    _hang_import_dirs(db, lab_id, user.id, item, dto, body.direction_ids)
    db.commit()
    db.refresh(item)
    return dto


UPLOAD_BATCH = 30
ZIP_MAX_BYTES = 200 * 1024 * 1024
ZIP_MAX_PDFS = 400


class _BytesUpload:
    def __init__(self, name: str, data: bytes):
        self.filename = name
        self.content_type = "application/pdf"
        self.file = io.BytesIO(data)


class ImportCheckBody(BaseModel):
    refs: list[dict] = Field(default_factory=list)
    direction_id: str | None = None


@router.post("/labs/{lab_id}/literature/import-check")
def import_check(
    lab_id: str,
    body: ImportCheckBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """预览要收的论文，不写入。"""
    require_membership(db, user, lab_id)
    from fengsuan.app.services.paper_import import preview_many

    rows = preview_many(db, lab_id, body.refs or [], direction_id=body.direction_id or "")
    return {"items": rows}


@router.post("/labs/{lab_id}/literature/upload-pdfs")
def upload_pdfs(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    files: list[UploadFile] = File(...),
    mark_read: bool = True,
    direction_id: str | None = Query(default=None),
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        return _upload_pdfs_locked(db, lab_id, user, files, mark_read, direction_id)


@router.post("/labs/{lab_id}/literature/upload-zip")
def upload_zip(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    mark_read: bool = True,
    direction_id: str | None = Query(default=None),
) -> dict:
    """一个压缩包里的 PDF 一次放进组里。非 PDF 跳过，单份仍不超过 32MB，最多 400 份。"""
    require_membership(db, user, lab_id)
    raw = file.file.read(ZIP_MAX_BYTES + 1)
    if len(raw) > ZIP_MAX_BYTES:
        raise ClassifiedHTTPException(400, "压缩包太大，请分成几个再传。", "validation")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ClassifiedHTTPException(400, "这不是一个能打开的压缩包。", "validation") from exc
    uploads: list[_BytesUpload] = []
    skipped = 0
    overflow = False
    for info in zf.infolist():
        if info.is_dir():
            continue
        base = Path(info.filename).name
        if not base.lower().endswith(".pdf"):
            continue
        if info.file_size > 32 * 1024 * 1024:
            skipped += 1
            continue
        if len(uploads) >= ZIP_MAX_PDFS:
            overflow = True
            break
        uploads.append(_BytesUpload(safe_filename(base, "paper.pdf"), zf.read(info)))
    if not uploads and not skipped:
        raise ClassifiedHTTPException(400, "压缩包里没有 PDF。", "validation")
    from fengsuan.app.core.concurrency import lab_quota_guard

    items: list[dict] = []
    with lab_quota_guard(db, lab_id):
        for start in range(0, len(uploads), UPLOAD_BATCH):
            part = _upload_pdfs_locked(db, lab_id, user, uploads[start : start + UPLOAD_BATCH], mark_read, direction_id)
            items.extend(part.get("items") or [])
    imported = [x for x in items if x.get("id")]
    out = {"count": len(imported), "items": items, "pdfs": len(uploads)}
    notes = []
    if skipped:
        notes.append(f"{skipped} 份超过 32MB，没有放进去")
    if overflow:
        notes.append(f"一次最多 {ZIP_MAX_PDFS} 份，剩下的请再传一个包")
    if notes:
        out["next_step"] = "。".join(notes) + "。"
    return out


def _upload_pdfs_locked(
    db: Session, lab_id: str, user, files: list[UploadFile], mark_read: bool, direction_id: str | None = None
) -> dict:
    backfill_file_hashes(db, lab_id)
    collapse_same_hash_items(db, lab_id)
    created: list[dict] = []
    incoming = list(files or [])
    truncated = len(incoming) > UPLOAD_BATCH
    skipped = 0
    index_now = len(incoming) <= 3
    for file in incoming[:UPLOAD_BATCH]:
        name = safe_filename(file.filename or "paper.pdf", "paper.pdf")
        try:
            content = read_upload_limited(file)
        except ValueError:
            skipped += 1
            created.append(
                {
                    "title": name,
                    "already_in_library": False,
                    "parse_status": "failed",
                    "error": "文件过大，单文件不超过 32MB。",
                }
            )
            continue
        try:
            with db.begin_nested():
                digest = hashlib.sha256(content).hexdigest()
                existing = find_by_content_hash(db, lab_id, digest)
                if existing is not None:
                    if mark_read:
                        row, prev = _upsert_reading(db, existing.id, user.id, status="reading")
                        _emit_reading_transition(db, existing, user.id, prev, row.status)
                    dto = paper_dto(existing, filename=name)
                    dto["already_in_library"] = True
                    dto["duplicate_reason"] = "hash"
                    _hang_import_dirs(db, lab_id, user.id, existing, dto, [direction_id] if direction_id else [])
                    created.append(dto)
                    continue
                title = filename_title(name) or "Uploaded PDF"
                item, already = _create_item_locked(db, lab_id, user.id, {"title": title, "authors": []}, "upload", mark_read)
                attached = _attach_pdf(
                    db,
                    item,
                    lab_id,
                    content,
                    name,
                    file.content_type or "application/pdf",
                    sha256=digest,
                    index_now=index_now,
                )
                dto = paper_dto(item, filename=name)
                dto["parse_status"] = attached.parse_status
                dto["already_in_library"] = already
                _hang_import_dirs(db, lab_id, user.id, item, dto, [direction_id] if direction_id else [])
                created.append(dto)
        except Exception:
            skipped += 1
            created.append(
                {
                    "title": name,
                    "already_in_library": False,
                    "parse_status": "failed",
                    "error": "这篇 PDF 抽出来的文字无法入库，已跳过。其余不受影响。",
                }
            )
    collapse_same_hash_items(db, lab_id)
    db.commit()
    imported = [x for x in created if x.get("id")]
    if imported:
        from fengsuan.app.services.analysis import stamp_list_card_state

        stamp_list_card_state(db, imported, id_key="id")
    out = {"count": len(imported), "items": created}
    if truncated:
        out["next_step"] = f"一次最多 {UPLOAD_BATCH} 个，请分批拖入。哈希相同会合并。"
    elif skipped:
        out["next_step"] = f"{skipped} 个 PDF 没有导入。可换一份可复制文字的 PDF 再拖一次。"
    return out


@router.get("/labs/{lab_id}/literature/digest-status")
def literature_digest_status(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.digest import digest_status

    return digest_status(db, lab_id)


@router.post("/labs/{lab_id}/literature/digest-all")
def literature_digest_all(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    from fengsuan.app.services.digest import digest_status, set_auto_digest

    set_auto_digest(db, lab_id, True, boost=True)
    db.commit()
    return digest_status(db, lab_id)


@router.get("/labs/{lab_id}/literature")
def list_literature(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str | None = None,
    year: int | None = None,
    direction_id: str | None = None,
    reading_status: str | None = None,
    reading_scope: str | None = None,
    reader_id: str | None = None,
    core: bool = False,
    cited_by_published: bool = False,
    has_pdf: bool | None = None,
    has_card: bool | None = None,
    reproduce: str | None = None,
    author_id: str | None = None,
    read_level: str | None = None,
    min_cited: int | None = None,
    sort: str | None = None,
    page: int | None = None,
    page_size: int = 50,
) -> list[dict] | dict:
    mem = require_membership(db, user, lab_id)
    query = db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
    if year:
        query = query.filter(LiteratureItem.publish_year == year)
    author_row = None
    if author_id:
        from fengsuan.app.models.literature import Authorship
        from fengsuan.app.services.authors import live_author

        author_row = live_author(db, author_id)
        if author_row is None or author_row.lab_id != lab_id:
            query = query.filter(LiteratureItem.id.is_(None))
        else:
            query = query.filter(
                exists().where(Authorship.item_id == LiteratureItem.id, Authorship.author_id == author_row.id)
            )
    level_want = READ_LEVEL_FILTER.get((read_level or "").strip())
    if level_want is not None:
        query = query.filter(LiteratureItem.read_level.in_(level_want))
    if min_cited:
        query = query.filter(LiteratureItem.cited_by_count >= int(min_cited))
    dir_scope: list[str] = []
    includes_children = False
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        dir_scope = resolve_direction_scope(db, lab_id, direction_id)
        includes_children = len(dir_scope) > 1
        if not dir_scope:
            query = query.filter(LiteratureItem.id.is_(None))
        else:
            query = query.filter(
                exists().where(
                    LiteratureDirectionLink.item_id == LiteratureItem.id,
                    LiteratureDirectionLink.direction_id.in_(dir_scope),
                )
            )
    if core:
        query = query.filter(
            exists().where(
                LiteratureDirectionLink.item_id == LiteratureItem.id,
                LiteratureDirectionLink.is_core.is_(True),
                *([LiteratureDirectionLink.direction_id.in_(dir_scope)] if dir_scope else []),
            )
        )
    qn = (q or "").strip()
    if qn:
        like = f"%{qn}%"
        query = query.filter(
            or_(
                LiteratureItem.title.ilike(like),
                LiteratureItem.abstract.ilike(like),
                LiteratureItem.doi.ilike(like),
            )
        )
    if reading_status == "unread":
        if reader_id:
            query = query.filter(
                ~exists().where(
                    LiteratureReading.item_id == LiteratureItem.id,
                    LiteratureReading.user_id == reader_id,
                    LiteratureReading.status == "reading",
                )
            )
        else:
            query = query.filter(
                ~exists().where(
                    LiteratureReading.item_id == LiteratureItem.id,
                    LiteratureReading.status == "reading",
                )
            )
    elif reading_status:
        scope = (reading_scope or "").strip().lower()
        if scope == "lab" and not reader_id:
            query = query.filter(
                exists().where(
                    LiteratureReading.item_id == LiteratureItem.id,
                    LiteratureReading.status == reading_status,
                )
            )
        else:
            query = query.filter(
                exists().where(
                    LiteratureReading.item_id == LiteratureItem.id,
                    LiteratureReading.user_id == (reader_id or user.id),
                    LiteratureReading.status == reading_status,
                )
            )
    if has_card is True or has_card is False:
        from fengsuan.app.services.analysis import written_method_item_ids

        live_ids = [
            iid
            for (iid,) in db.query(LiteratureItem.id)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .all()
        ]
        written = written_method_item_ids(db, live_ids)
        if has_card:
            query = query.filter(LiteratureItem.id.in_(written or ["__none__"]))
        elif written:
            query = query.filter(~LiteratureItem.id.in_(written))
    if cited_by_published:
        from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation

        query = query.filter(
            exists().where(
                ManuscriptCitation.literature_item_id == LiteratureItem.id,
                ManuscriptCitation.manuscript_id == Manuscript.id,
                Manuscript.lab_id == lab_id,
                Manuscript.status == "published",
                Manuscript.deleted_at.is_(None),
            )
        )
    if has_pdf is True:
        query = query.filter(exists().where(LiteratureFile.item_id == LiteratureItem.id))
    elif has_pdf is False:
        query = query.filter(~exists().where(LiteratureFile.item_id == LiteratureItem.id))
    order = (sort or "").strip()
    if order == "cited":
        query = query.order_by(LiteratureItem.cited_by_count.desc().nullslast(), LiteratureItem.created_at.desc())
    elif order == "year":
        query = query.order_by(LiteratureItem.publish_year.desc().nullslast(), LiteratureItem.created_at.desc())
    else:
        query = query.order_by(LiteratureItem.created_at.desc())
    want_repro = (reproduce or "").strip()
    mem_filter = want_repro in {"measured", "not_run", "none", "awaiting", "blocked", "unmeasured"}
    if mem_filter:
        rows = query.limit(500).all()
        total = len(rows)
        size = total
    elif page and page > 0:
        size = max(1, min(page_size, 200))
        total = query.count()
        rows = query.offset((page - 1) * size).limit(size).all()
    else:
        rows = query.limit(500).all()
        total = len(rows)
        size = total
    ids = [x.id for x in rows]
    mine: dict[str, LiteratureReading] = {}
    dirs_by: dict[str, list[str]] = {}
    if ids:
        for rd in db.query(LiteratureReading).filter(LiteratureReading.user_id == user.id, LiteratureReading.item_id.in_(ids)).all():
            mine[rd.item_id] = rd
        from collections import defaultdict

        grouped: dict[str, list[str]] = defaultdict(list)
        core_by: dict[str, bool] = {}
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(ids)).all():
            grouped[link.item_id].append(link.direction_id)
            if dir_scope and link.direction_id not in dir_scope:
                continue
            if getattr(link, "is_core", False):
                core_by[link.item_id] = True
        dirs_by = grouped
    else:
        core_by = {}
    files_by: dict[str, str] = {}
    people_by: dict[str, list[dict]] = {}
    if ids:
        for f in db.query(LiteratureFile).filter(LiteratureFile.item_id.in_(ids)).all():
            files_by.setdefault(f.item_id, f.filename or "")
        from fengsuan.app.models.literature import Authorship

        for s in (
            db.query(Authorship)
            .filter(Authorship.item_id.in_(ids), Authorship.author_id.isnot(None))
            .order_by(Authorship.item_id, Authorship.position.asc())
            .all()
        ):
            people_by.setdefault(s.item_id, []).append({"name": s.raw_name, "author_id": s.author_id})
    out = []
    for x in rows:
        dto = paper_dto(x, filename=files_by.get(x.id, ""))
        dto.update(reading_fields(mine.get(x.id)))
        dto["direction_ids"] = dirs_by.get(x.id, [])
        dto["is_core"] = bool(core_by.get(x.id))
        dto["read_level"] = int(x.read_level if x.read_level is not None else -1)
        dto["cited_by_count"] = x.cited_by_count
        dto["author_links"] = people_by.get(x.id, [])[:8]
        out.append(dto)
    from fengsuan.app.services.literature import stamp_lab_readers

    stamp_lab_readers(db, lab_id, out)
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href

    stamp_items_open_href(db, user.id, out, id_key="id")
    stamp_reproduce_href(lab_id, out, id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, out, id_key="id")
    from fengsuan.app.services.digest import stamp_digest_state

    stamp_digest_state(db, out, id_key="id")
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, out, id_key="id")
    from fengsuan.app.services.insight import literature_list_empty_next
    from fengsuan.app.services.literature import (
        direction_gap,
        induce_direction_drafts,
        stamp_direction_hook,
        _live_lab_directions,
    )

    dirs_live = _live_lab_directions(db, lab_id)
    for dto in out:
        stamp_direction_hook(db, lab_id, dto, dirs=dirs_live)

    def _empty_fields() -> dict:
        lab_n = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .count()
        )
        kind = "library" if lab_n == 0 else "filter"
        return {
            "empty_kind": kind,
            "empty_next": literature_list_empty_next(
                lab_id,
                reader_id=reader_id,
                reading_status=reading_status,
                direction_id=direction_id,
                has_card=has_card,
                reproduce=want_repro or None,
                q=qn or None,
                empty_kind=kind,
                includes_children=includes_children,
            ),
            "includes_children": includes_children,
        }

    def _with_gap(pack: dict) -> dict:
        teacher_here = mem.role == "teacher" or bool(getattr(user, "is_admin", False))
        gap = direction_gap(db, lab_id, teacher=teacher_here)
        if gap:
            pack["direction_gap"] = gap
        drafts = induce_direction_drafts(db, lab_id)
        if drafts:
            pack["direction_drafts"] = drafts
        pack["keyword_hints"] = []
        if teacher_here:
            from fengsuan.app.services.literature import classify_unlinked_papers, leftover_keyword_hints

            _hangable, leftover_rows = classify_unlinked_papers(db, lab_id)
            pack["keyword_hints"] = leftover_keyword_hints(db, lab_id, leftover_rows, limit=8)
        return pack

    if mem_filter:
        from fengsuan.app.services.reproduce import filter_rows_by_reproduce

        out = filter_rows_by_reproduce(out, want_repro)
        total = len(out)
        if page and page > 0:
            size = max(1, min(page_size, 200))
            out = out[(page - 1) * size : page * size]
            pack = {"items": out, "total": total, "page": page, "page_size": size, "includes_children": includes_children}
            if total == 0:
                pack.update(_empty_fields())
            return _with_gap(pack)
        return out
    if page and page > 0:
        pack = {"items": out, "total": total, "page": page, "page_size": size, "includes_children": includes_children}
        if author_row is not None:
            pack["author"] = {"id": author_row.id, "name": author_row.display_name}
        if total == 0:
            pack.update(_empty_fields())
        return _with_gap(pack)
    return out


@router.patch("/labs/{lab_id}/literature/{item_id}/core")
def pin_literature_core(
    lab_id: str,
    item_id: str,
    body: PinCoreBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None or item.lab_id != lab_id:
        raise HTTPException(404, detail="文献不存在")
    from fengsuan.app.models.lab import ResearchDirection

    direction = db.get(ResearchDirection, body.direction_id)
    if direction is None or direction.lab_id != lab_id or direction.deleted_at is not None:
        raise HTTPException(404, detail="方向不存在")
    link = (
        db.query(LiteratureDirectionLink)
        .filter(LiteratureDirectionLink.item_id == item.id, LiteratureDirectionLink.direction_id == body.direction_id)
        .first()
    )
    if link is None:
        link = LiteratureDirectionLink(item_id=item.id, direction_id=body.direction_id, is_core=bool(body.is_core))
        db.add(link)
    else:
        link.is_core = bool(body.is_core)
    db.commit()
    return {"ok": True, "item_id": item.id, "direction_id": body.direction_id, "is_core": bool(link.is_core)}


@router.get("/labs/{lab_id}/literature/export-bibtex")
def export_lab_bibtex(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
):
    from fastapi.responses import PlainTextResponse

    require_membership(db, user, lab_id)
    query = db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            query = query.filter(LiteratureItem.id.is_(None))
        else:
            query = query.filter(
                exists().where(
                    LiteratureDirectionLink.item_id == LiteratureItem.id,
                    LiteratureDirectionLink.direction_id.in_(scope),
                )
            )
    rows = query.order_by(LiteratureItem.created_at.desc()).all()
    text = ("\n\n".join(item_to_bibtex(x) for x in rows) + "\n") if rows else "% empty\n"
    emit_audit(db, user_id=user.id, action="literature.export", resource="lab", resource_id=lab_id)
    db.commit()
    return PlainTextResponse(
        text,
        media_type="application/x-bibtex",
        headers={"Content-Disposition": "attachment; filename=lab.bib"},
    )


@router.post("/literature/{item_id}/code-links")
def refresh_code_links(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """点一下才去对代码仓库和 Hugging Face，写进这篇已有的外部编号，不新建表。"""
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    from fengsuan.app.services.literature import store_code_links
    from fengsuan.app.services.web import scholarly

    github = ""
    hf = ""
    note = ""
    try:
        repos = scholarly.github_repos(item.title or "", limit=1) or []
        github = str((repos[0] or {}).get("url") or "") if repos else ""
    except Exception:
        note = "这次没连上代码库。"
    try:
        needle = (item.title or "").lower()[:24]
        for paper in scholarly.hf_papers(limit=5) or []:
            if needle and needle in str(paper.get("title") or "").lower():
                hf = str(paper.get("url") or "")
                break
    except Exception:
        note = (note + " 这次没连上 Hugging Face。").strip()
    links = store_code_links(item, github=github, hf=hf)
    db.commit()
    if not github and not hf and not note:
        note = "没有对上公开代码或 Hugging Face 论文页。"
    return {**links, "note": note}


@router.get("/literature/{item_id}")
def get_literature(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    session: str | None = Query(default=None),
    tutor_page: int = Query(default=1, ge=1),
    tutor_page_size: int = Query(default=8, ge=1, le=20),
    page: int | None = Query(default=None, ge=1, le=20000),
    direction_id: str | None = None,
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    _ = direction_id
    # 兼容路由：仍带辅导历史。阅读器热路径走 /detail，默认瘦身（A3）。
    return build_literature_detail(
        db,
        item,
        user.id,
        session_id=session,
        tutor_page=tutor_page,
        tutor_page_size=tutor_page_size,
        page_no=page,
        include="tutor",
    )


@router.delete("/literature/{item_id}")
def delete_literature(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    membership = require_membership(db, user, item.lab_id)
    if item.added_by != user.id and membership.role != "teacher":
        raise HTTPException(403, detail="\u4ec5\u5f55\u5165\u8005\u6216\u5bfc\u5e08\u53ef\u5220\u9664")
    item.deleted_at = utcnow()
    emit_audit(db, user_id=user.id, action="literature.delete", resource="literature", resource_id=item.id)
    db.commit()
    return {"ok": True}


@router.post("/literature/import-doi")
def import_doi(
    body: ImportDoiBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, body.lab_id)
    try:
        data = fetch_crossref(body.doi)
    except ClassifiedHTTPException:
        raise
    except Exception:
        raise HTTPException(400, detail="DOI \u65e0\u6cd5\u89e3\u6790")
    item, already = _create_item(db, body.lab_id, user.id, data, "doi", body.mark_read)
    dto = paper_dto(item)
    dto["already_in_library"] = already
    _hang_import_dirs(db, body.lab_id, user.id, item, dto, body.direction_ids)
    db.commit()
    db.refresh(item)
    return dto


@router.post("/literature/import-bibtex")
def import_bibtex(
    body: ImportBibtexBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, body.lab_id)
    parsed = parse_bibtex(body.bibtex)
    items = [_create_item(db, body.lab_id, user.id, p, "bibtex", body.mark_read)[0] for p in parsed]
    last_ids: list[str] = []
    last_via: dict[str, str] = {}
    for item in items:
        dto = paper_dto(item)
        _hang_import_dirs(db, body.lab_id, user.id, item, dto, body.direction_ids)
        last_ids = dto.get("direction_ids") or []
        last_via = dto.get("linked_via") or {}
    db.commit()
    return {"count": len(items), "ids": [i.id for i in items], "direction_ids": last_ids, "linked_via": last_via}


@router.post("/literature/import-csv")
def import_csv(
    body: ImportCsvBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, body.lab_id)
    reader = csv.DictReader(io.StringIO(body.csv_text))
    items = []
    for row in reader:
        year = row.get("year") or row.get("publish_year")
        data = {
            "title": row.get("title") or "untitled",
            "authors": [x.strip() for x in (row.get("authors") or "").split(";") if x.strip()],
            "doi": row.get("doi") or None,
            "url": row.get("url"),
            "publish_year": int(year) if year and str(year).isdigit() else None,
        }
        items.append(_create_item(db, body.lab_id, user.id, data, "csv", body.mark_read)[0])
    last_ids: list[str] = []
    last_via: dict[str, str] = {}
    for item in items:
        dto = paper_dto(item)
        _hang_import_dirs(db, body.lab_id, user.id, item, dto, body.direction_ids)
        last_ids = dto.get("direction_ids") or []
        last_via = dto.get("linked_via") or {}
    db.commit()
    return {"count": len(items), "direction_ids": last_ids, "linked_via": last_via}


@router.post("/literature/import-capture")
def import_capture(
    body: ImportCaptureBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, body.lab_id)
    parsed = {}
    if body.page_html:
        from fengsuan.app.services.capture import parse_paper_html

        parsed = parse_paper_html(body.page_html, body.page_url)
    adapter = body.site_adapter or parsed.get("site_adapter") or "generic"
    db.add(
        BrowserCapture(
            lab_id=body.lab_id,
            user_id=user.id,
            page_url=body.page_url,
            site_adapter=adapter,
            raw_meta_json=json.dumps(body.raw_meta or parsed, ensure_ascii=False),
        )
    )
    title = body.title or parsed.get("title") or body.page_url
    data = {
        "title": title,
        "authors": body.authors or parsed.get("authors") or [],
        "abstract": body.abstract or parsed.get("abstract") or "",
        "doi": body.doi or parsed.get("doi"),
        "url": body.page_url,
        "venue": body.venue or parsed.get("venue"),
        "publish_year": body.publish_year or parsed.get("publish_year"),
        "arxiv_id": parsed.get("arxiv_id"),
        "pubmed_id": parsed.get("pubmed_id"),
        "cnki_id": parsed.get("cnki_id"),
    }
    item, already = _create_item(db, body.lab_id, user.id, data, "browser_ext", body.mark_read)
    dto_hang = paper_dto(item)
    _hang_import_dirs(db, body.lab_id, user.id, item, dto_hang, body.direction_ids)
    pdf_hint = ""
    if body.pdf_base64:
        import base64

        try:
            raw = base64.b64decode(body.pdf_base64)
            if raw:
                _attach_pdf(db, item, body.lab_id, raw, body.pdf_filename or "capture.pdf")
        except Exception:
            pdf_hint = "PDF \u89e3\u7801\u5931\u8d25\uff0c\u5df2\u4ec5\u5165\u5e93\u9898\u5f55\u3002"
    elif body.pdf_fetch_failed:
        pdf_hint = "\u6d4f\u89c8\u5668\u672a\u80fd\u62c9\u53d6 PDF\uff0c\u8bf7\u4e0b\u8f7d\u540e\u4e0a\u4f20\u3002"
    if not already:
        emit_activity(
            db,
            lab_id=body.lab_id,
            actor_id=user.id,
            verb="literature.imported_ext",
            object_type="literature",
            object_id=item.id,
            summary=item.title,
        )
    db.commit()
    db.refresh(item)
    dto = paper_dto(item)
    dto["already_in_library"] = already
    dto["direction_ids"] = dto_hang.get("direction_ids") or []
    dto["linked_via"] = dto_hang.get("linked_via") or {}
    dto["direction_suggestions"] = dto_hang.get("direction_suggestions") or []
    adapter = (adapter or "").lower()
    parsed_title = (parsed.get("title") or "").strip()
    weak_meta = not data.get("authors") and not data.get("doi")
    title_from_page = bool(body.title or parsed_title)
    if adapter in {"cnki", "ieee", "wanfang"} and weak_meta:
        dto["degraded"] = True
        dto["hint"] = (
            "\u77e5\u7f51/IEEE \u5143\u6570\u636e\u4e0d\u5b8c\u6574\uff0c\u5df2\u964d\u7ea7\u5165\u5e93\u3002"
            "\u8bf7\u6838\u5bf9\u9898\u5f55\u6216\u6539\u7528 PDF \u4e0a\u4f20\u3002"
        )
    elif not title_from_page:
        dto["degraded"] = True
        dto["hint"] = "\u672a\u80fd\u89e3\u6790\u9898\u5f55\uff0c\u8bf7\u6838\u5bf9\u6216\u6539\u7528 PDF \u4e0a\u4f20\u3002"
    if pdf_hint:
        dto["degraded"] = True
        dto["hint"] = ((dto.get("hint") or "") + " " + pdf_hint).strip()
    return dto


@router.post("/literature/{item_id}/upload-pdf")
def upload_pdf(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    try:
        content = read_upload_limited(file)
    except ValueError:
        raise HTTPException(400, detail="\u6587\u4ef6\u8fc7\u5927\uff0c\u5355\u6587\u4ef6\u4e0d\u8d85\u8fc7 32MB")
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, item.lab_id):
        db.refresh(item)
        if item.deleted_at is not None:
            raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
        _file = _attach_pdf(
            db, item, item.lab_id, content, file.filename or "paper.pdf", file.content_type or "application/pdf"
        )
        collapse_same_hash_items(db, item.lab_id)
        db.commit()
    return {"ok": True, "page_count": _file.page_count, "parse_status": _file.parse_status}


@router.post("/literature/{item_id}/mark-read")
def mark_read(
    item_id: str,
    body: MarkReadBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    progress = body.progress_pct
    if progress is None and body.status == "finished":
        progress = 100
    row, prev = _upsert_reading(db, item.id, user.id, status=body.status, progress=progress)
    _emit_reading_transition(db, item, user.id, prev, row.status)
    db.commit()
    return {"ok": True, **reading_fields(row)}


@router.post("/literature/{item_id}/progress")
def reading_progress(
    item_id: str,
    body: ReadingProgressBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    existing = (
        db.query(LiteratureReading)
        .filter(LiteratureReading.item_id == item.id, LiteratureReading.user_id == user.id)
        .first()
    )
    prev = existing.status if existing is not None else None
    status = existing.status if existing is not None else "reading"
    if prev in (None, "want_read"):
        status = "reading"
    pct = body.progress_pct
    if pct is None and body.page_no and body.page_count:
        pct = round(100.0 * min(body.page_no, body.page_count) / body.page_count, 1)
    row, _ = _upsert_reading(
        db,
        item.id,
        user.id,
        status=status,
        progress=pct,
        last_page_no=body.page_no,
    )
    _emit_reading_transition(db, item, user.id, prev, row.status)
    db.commit()
    return {"ok": True, **reading_fields(row)}


def _anchor_json(db: Session, item: LiteratureItem, anchor, page_no: int | None) -> str | None:
    """锚点入库前先校验：矩形要在 0–1 之间，原文子串要真的在那一页上（A9）。"""
    if anchor is None:
        return None
    rects = []
    for r in anchor.rects or []:
        try:
            box = {k: float(r.get(k)) for k in ("x", "y", "w", "h")}
        except (TypeError, ValueError):
            continue
        if any(v < 0 or v > 1.2 for v in box.values()) or box["w"] <= 0 or box["h"] <= 0:
            continue
        rects.append(box)
    if not rects:
        return None
    pno = anchor.page_no or page_no
    quote = (anchor.quote or "").strip()[:400]
    if quote:
        from fengsuan.app.services.pagecache import item_single_page

        pack = item_single_page(db, item, pno)
        if pack["has_fulltext"]:
            flat = re.sub(r"\s+", "", pack["page_text"])
            if re.sub(r"\s+", "", quote) not in flat:
                quote = ""
    return json.dumps({"page_no": pno, "quote": quote, "rects": rects}, ensure_ascii=False)


@router.get("/literature/{item_id}/structure")
def literature_structure(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """章节与图表树。没有 GROBID 结果就明说还没有结构，不假装有（A9）。"""
    from fengsuan.app.services.pagecache import item_structure

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    from fengsuan.app.services.reading_tier import section_notes

    tree = item_structure(db, item.id)
    has = bool(tree.get("sections") or tree.get("figures"))
    return {
        **tree,
        "section_notes": section_notes(db, item.id),
        "has_structure": has,
        "next_step": "" if has else "\u8fd8\u6ca1\u6709\u7ae0\u8282\u7ed3\u6784\uff1a\u672c\u7bc7\u7684 PDF \u8fd8\u6ca1\u88ab\u7ed3\u6784\u5316\u89e3\u6790\uff0c\u53ef\u5148\u6309\u9875\u9605\u8bfb\u3002",
    }


@router.get("/literature/{item_id}/page-translation")
def get_page_translation(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1, le=20000),
    lang: str = Query(default="zh", max_length=16),
) -> dict:
    """页译文服务端缓存（A9）。换浏览器也在，命中不再烧一次配额。"""
    from fengsuan.app.services.pagecache import item_single_page, page_fingerprint, read_page_translation

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    pack = item_single_page(db, item, page)
    fp = page_fingerprint(pack["page_text"]) if pack["has_fulltext"] else ""
    text = read_page_translation(db, item.id, page, lang, fingerprint=fp) if fp else None
    return {
        "page_no": page,
        "lang": lang,
        "text": text or "",
        "hit": bool(text),
        "text_hash": fp,
        "page_text_missing": not pack["has_fulltext"],
    }


@router.put("/literature/{item_id}/page-translation")
def put_page_translation(
    item_id: str,
    body: PageTranslationBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.services.pagecache import item_single_page, page_fingerprint, write_page_translation

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    pack = item_single_page(db, item, body.page_no)
    if not pack["has_fulltext"]:
        raise ClassifiedHTTPException(400, "\u8fd9\u4e00\u9875\u8fd8\u6ca1\u6709\u53ef\u590d\u5236\u6587\u5b57\uff0c\u4e0d\u5b58\u8bd1\u6587\u3002", "parse")
    fp = page_fingerprint(pack["page_text"])
    write_page_translation(db, item.id, body.page_no, body.lang, body.text, fingerprint=fp)
    db.commit()
    return {"ok": True, "page_no": body.page_no, "lang": body.lang, "text_hash": fp}


@router.get("/literature/{item_id}/notes")
def list_notes(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    notes = db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id, LiteratureNote.deleted_at.is_(None)).all()
    return [_note_dict(db, n) for n in notes]


@router.post("/literature/{item_id}/notes")
def add_note(
    item_id: str,
    body: NoteCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    note = LiteratureNote(
        item_id=item_id,
        user_id=user.id,
        content_md=body.content_md,
        page_no=body.page_no,
        anchor_json=_anchor_json(db, item, body.anchor, body.page_no),
    )
    db.add(note)
    db.flush()
    emit_activity(
        db,
        lab_id=item.lab_id,
        actor_id=user.id,
        verb="note.created",
        object_type="literature",
        object_id=item_id,
        summary="\u5199\u4e86\u7b14\u8bb0",
        payload={"page_no": body.page_no, "note_id": note.id},
    )
    db.commit()
    db.refresh(note)
    return _note_dict(db, note)


@router.patch("/literature/notes/{note_id}")
def patch_note(
    note_id: str,
    body: NotePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    note = db.get(LiteratureNote, note_id)
    if note is None or note.deleted_at is not None:
        raise HTTPException(404, detail="\u7b14\u8bb0\u4e0d\u5b58\u5728")
    item = db.get(LiteratureItem, note.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    if note.user_id != user.id:
        raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u53ef\u7f16\u8f91\u7b14\u8bb0")
    if body.content_md is not None:
        note.content_md = body.content_md
    if body.page_no is not None:
        note.page_no = body.page_no
    if body.anchor is not None:
        note.anchor_json = _anchor_json(db, item, body.anchor, body.page_no or note.page_no)
    note.updated_at = utcnow()
    emit_activity(
        db,
        lab_id=item.lab_id,
        actor_id=user.id,
        verb="note.updated",
        object_type="literature",
        object_id=item.id,
        summary="\u66f4\u65b0\u4e86\u7b14\u8bb0",
        payload={"page_no": note.page_no, "note_id": note.id},
    )
    db.commit()
    return _note_dict(db, note)


@router.delete("/literature/notes/{note_id}")
def delete_note(
    note_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    note = db.get(LiteratureNote, note_id)
    if note is None or note.deleted_at is not None:
        raise HTTPException(404, detail="\u7b14\u8bb0\u4e0d\u5b58\u5728")
    item = db.get(LiteratureItem, note.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    membership = require_membership(db, user, item.lab_id)
    require_owner_or_teacher(membership, user, note.user_id, "\u4ec5\u4f5c\u8005\u6216\u5bfc\u5e08\u53ef\u5220\u9664\u7b14\u8bb0")
    note.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/literature/{item_id}/analyze")
def analyze_literature(
    item_id: str,
    body: AnalyzeBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.services.analysis import begin_or_run_analyze, card_is_running
    from fengsuan.app.services.activity import emit_activity as _emit
    from fengsuan.app.services.llm import quota_warning

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    require_membership(db, user, item.lab_id)
    try:
        row, deferred = begin_or_run_analyze(db, item, user.id, body.analysis_type)
    except ClassifiedHTTPException as exc:
        if exc.status_code in {502, 504}:
            raise ClassifiedHTTPException(
                400,
                exc.detail,
                exc.error_class or "llm",
                next_step=exc.next_step or "请再点「提炼方法」。不要把网关超时当分析成功。",
            ) from exc
        raise
    except ValueError:
        raise ClassifiedHTTPException(400, "Could not parse PDF text", "parse")
    except Exception:
        raise ClassifiedHTTPException(
            400,
            "提炼方法未完成",
            "llm",
            next_step="请再点「提炼方法」。不要把网关超时当分析成功。",
        )
    parsed = json.loads(row.result_json or "{}")
    if deferred or card_is_running(parsed if isinstance(parsed, dict) else {}):
        db.commit()
        from fengsuan.app.services.analysis import card_empty_state, readable_method_card
        from fengsuan.app.services.literature import stamp_direction_hook

        display = parsed if isinstance(parsed, dict) else {}
        files = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).all()
        payload = {
            "id": row.id,
            "result": display,
            "status": "running",
            "quota_warning": quota_warning(db, item.lab_id),
            "card_empty": card_empty_state(files=files, card=display),
            "next_step": {
                "key": "running",
                "label": "正在提炼",
                "text": "正在写这篇怎么做，写完会显示在侧栏。",
                "href": f"/literature/{item.id}?panel=card",
            },
        }
        linked = [
            r.direction_id
            for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
        ]
        payload["direction_ids"] = linked
        stamp_direction_hook(db, item.lab_id, payload, title=item.title or "", abstract=item.abstract or "", linked=linked)
        from fengsuan.app.services.analysis import stamp_analysis_ai

        stamp_analysis_ai(payload, item, empty=payload.get("card_empty"), result=display if isinstance(display, dict) else {})
        return payload
    _emit(
        db,
        lab_id=item.lab_id,
        actor_id=user.id,
        verb="analysis.ready",
        object_type="literature",
        object_id=item.id,
        summary=item.title,
    )
    db.commit()
    parsed = json.loads(row.result_json)
    from fengsuan.app.services.analysis import (
        analysis_next_step,
        card_empty_state,
        card_quality,
        load_best_method_card,
        readable_method_card,
        stamp_analysis_ai,
    )

    if isinstance(parsed, dict):
        parsed = readable_method_card(parsed)
    else:
        parsed = {}

    _best_row, best_card = load_best_method_card(db, item.id)
    reused = False
    display = parsed
    if best_card and card_quality(best_card)["score"] > card_quality(parsed)["score"]:
        reused = True
        warn = (parsed.get("warning") or "这次没提炼出这篇怎么做。") + " 仍显示上次写好的这篇怎么做。"
        display = dict(best_card)
        display["warning"] = warn

    files = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).all()
    empty = card_empty_state(files=files, card=display)
    payload = {"id": row.id, "result": display, "quota_warning": quota_warning(db, item.lab_id), "card_empty": empty}
    if reused:
        payload["reused"] = True
    sid = getattr(row, "agent_session_id", "") or (parsed.get("session_id") if isinstance(parsed, dict) else "") or ""
    if sid:
        payload["session_id"] = sid
        payload["trace_id"] = sid
    step = analysis_next_step(item, empty)
    if step:
        payload["next_step"] = step
    stamp_analysis_ai(payload, item, empty=empty, result=display if isinstance(display, dict) else {})
    from fengsuan.app.services.analysis import card_is_written
    from fengsuan.app.services.literature import auto_place_written_card, stamp_direction_hook

    placed = auto_place_written_card(db, item) if card_is_written(display if isinstance(display, dict) else {}) else []
    if placed:
        db.commit()
    linked = [
        r.direction_id
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    ]
    payload["direction_ids"] = linked
    payload["auto_placed"] = placed
    payload["has_method_card"] = card_is_written(display if isinstance(display, dict) else {})
    stamp_direction_hook(db, item.lab_id, payload, title=item.title or "", abstract=item.abstract or "", linked=linked)
    return payload


def _finish_tutor(db, item, user, body, result: dict) -> dict:
    from fengsuan.app.services.activity import emit_activity as _emit
    from fengsuan.app.services.analysis import analysis_next_step
    from fengsuan.app.services.llm import quota_warning

    if result.get("next_step") is None and result.get("page_text_missing"):
        result["next_step"] = analysis_next_step(item, page_text_missing=True)
    already_wrote = any(
        (s.get("tool") == "write_note" and not s.get("error")) for s in (result.get("steps") or [])
    )
    if body.write_note and not result.get("pending_confirm") and not already_wrote:
        from fengsuan.app.services.literature import literature_open_href

        note = LiteratureNote(item_id=item.id, user_id=user.id, content_md=result.get("answer") or "", page_no=body.page_no)
        db.add(note)
        db.flush()
        result["note_id"] = note.id
        result["page_no"] = note.page_no
        result["href"] = literature_open_href(item.id, note.page_no, note_id=note.id)
        _emit(
            db,
            lab_id=item.lab_id,
            actor_id=user.id,
            verb="note.created",
            object_type="literature",
            object_id=item.id,
            summary="精读辅导写回笔记",
            payload={"page_no": note.page_no, "note_id": note.id},
        )
    _emit(
        db,
        lab_id=item.lab_id,
        actor_id=user.id,
        verb="analysis.tutor",
        object_type="literature",
        object_id=item.id,
        summary=(body.question or "精读辅导")[:80],
        payload={"page_no": body.page_no, "note_id": result.get("note_id")},
    )
    db.commit()
    result["quota_warning"] = quota_warning(db, item.lab_id)
    return result


@router.post("/literature/{item_id}/tutor")
def tutor_literature(
    item_id: str,
    body: TutorBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from fengsuan.app.core.errors import ClassifiedHTTPException as LlmErr
    from fengsuan.app.services.analysis import iter_any_tutor_item, tutor_item
    from fengsuan.app.services.llm import sse_pack

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    if not body.stream:
        result = tutor_item(
            db,
            item,
            user.id,
            body.question,
            body.page_no,
            body.session_id,
            excerpt=body.excerpt,
            context=body.context,
            scope=body.scope,
            confirm=body.confirm,
            confirm_tools=body.confirm_tools,
        )
        return _finish_tutor(db, item, user, body, result)

    def gen():
        try:
            result = None
            for ev in iter_any_tutor_item(
                db,
                item,
                user.id,
                body.question,
                body.page_no,
                body.session_id,
                excerpt=body.excerpt,
                context=body.context,
                scope=body.scope,
                confirm=body.confirm,
                confirm_tools=body.confirm_tools,
            ):
                if ev.get("done"):
                    result = {k: v for k, v in ev.items() if k != "done"}
                    break
                yield sse_pack(ev)
            if result is None:
                yield sse_pack({"error": "辅导未完成", "error_class": "llm"})
                return
            finished = _finish_tutor(db, item, user, body, result)
            yield sse_pack({**finished, "done": True})
        except LlmErr as exc:
            db.rollback()
            yield sse_pack({"error": exc.detail, "error_class": exc.error_class})
        except Exception:
            db.rollback()
            yield sse_pack({"error": "辅导未完成", "error_class": "llm"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/labs/{lab_id}/literature/compare/latest")
def compare_latest(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import latest_own_text

    return latest_own_text(db, lab_id, user.id, "compare", kind="compare")


@router.post("/labs/{lab_id}/literature/compare")
def compare_literature(
    lab_id: str,
    body: CompareBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.services.analysis import compare_items

    require_membership(db, user, lab_id)
    if not (2 <= len(body.item_ids) <= 8):
        raise HTTPException(400, detail="\u8bf7\u9009\u62e9 2 \u81f3 8 \u7bc7\u6587\u732e")
    items = []
    for iid in body.item_ids:
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None or item.lab_id != lab_id:
            raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5c5e\u4e8e\u672c\u5b9e\u9a8c\u5ba4")
        items.append(item)
    result = compare_items(db, items, user.id, lab_id)
    db.commit()
    meta = {x.get("id"): x for x in result.pop("card_meta", []) if isinstance(x, dict)}
    ev_by = result.pop("reproduce_by_id", {}) or {}
    result["items"] = [
        {
            "id": it.id,
            "title": it.title,
            "card_quality": (meta.get(it.id) or {}).get("card_quality"),
            "reproduce": ev_by.get(it.id),
        }
        for it in items
    ]
    result.setdefault("gap_md", "")
    result.setdefault("reproduce_gap_md", "")
    from fengsuan.app.services.agent.loop import narration_to_zh

    for key in ("gap_md", "table_md", "reproduce_gap_md"):
        if result.get(key):
            result[key] = narration_to_zh("对比这几篇", result[key])
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href

    stamp_items_open_href(db, user.id, result["items"], id_key="id")
    stamp_reproduce_href(lab_id, result["items"], id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, result["items"], id_key="id")
    blob = result.get("json") if isinstance(result.get("json"), dict) else {}
    row_payloads = []
    for key in ("methods", "data", "metrics", "claims"):
        for row in blob.get(key) or []:
            if not isinstance(row, dict):
                continue
            iid = row.get("item_id") or row.get("literature_item_id")
            if not iid:
                continue
            row["literature_item_id"] = iid
            pages = row.get("pages") or []
            if pages and row.get("page_no") is None:
                try:
                    row["page_no"] = int(pages[0])
                except (TypeError, ValueError):
                    pass
            row_payloads.append(row)
    stamp_items_open_href(db, user.id, row_payloads, id_key="literature_item_id")
    stamp_reproduce_href(lab_id, row_payloads, id_key="literature_item_id")
    from fengsuan.app.services.insight import compare_empty_next, compare_next_step

    result["next_step"] = compare_next_step(lab_id, result)
    empty = compare_empty_next(lab_id, result)
    if empty:
        result["empty_next"] = empty
    from fengsuan.app.services.writing import user_unused_research_note

    unused = user_unused_research_note(db, lab_id, user.id)
    if unused:
        result["unused_research"] = unused
        result["honesty_notes"] = [unused]
    from fengsuan.app.services.agent.features import stamp_followups

    stamp_followups(result)
    from fengsuan.app.services.insight import remember_result_json

    remember_result_json(
        db,
        lab_id=lab_id,
        user_id=user.id,
        session_id=result.get("session_id") or "",
        feature="compare",
        question="对比",
        kind="compare",
        payload={
            "markdown": result.get("gap_md") or result.get("table_md") or "",
            "gap_md": result.get("gap_md") or "",
            "table_md": result.get("table_md") or "",
            "reproduce_gap_md": result.get("reproduce_gap_md") or "",
            "item_ids": [it.id for it in items],
            "titles": [(it.title or "")[:180] for it in items],
        },
    )
    db.commit()
    return result


@router.post("/literature/compare")
def compare_literature_alias(
    body: CompareBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not body.item_ids:
        raise HTTPException(400, detail="\u8bf7\u9009\u62e9 2 \u81f3 8 \u7bc7\u6587\u732e")
    first = db.get(LiteratureItem, body.item_ids[0])
    if first is None:
        raise HTTPException(404, detail="\u6587\u732e\u4e0d\u5b58\u5728")
    lab_id = body.lab_id or first.lab_id
    return compare_literature(lab_id, body, user, db)


@router.post("/labs/{lab_id}/literature/recommend")
def recommend_literature(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> dict:
    require_membership(db, user, lab_id)
    return recommend_for_lab(db, lab_id, user.id, direction_id)
