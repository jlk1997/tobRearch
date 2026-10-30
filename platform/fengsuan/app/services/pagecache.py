"""页文本缓存（A1）。

翻页与辅导以前每次都全量重解析 PDF（GROBID 25s + pypdf 全页），只为拿单页几千字。
这里把解析结果落到 `literature_file_pages`，请求路径改为「读缓存 → 未命中才解析并回写」。

缓存写入走独立 session，不掺进调用方的事务：GET 详情、POST 辅导都可能在更大的事务里，
缓存是可重建的派生数据，不该因为调用方回滚而丢，也不该把调用方的半成品提前提交。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from fengsuan.app.models.literature import LiteratureFile, LiteratureFilePage, LiteratureItem
from fengsuan.app.services.pdfparse import clean_extracted_text

PAGE_TEXT_LIMIT = 40000


def text_hash_of(pages: list[str]) -> str:
    h = hashlib.sha256()
    for page in pages or []:
        h.update((page or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()


def _is_pdf(f: LiteratureFile) -> bool:
    return "pdf" in (f.mime or "") or (f.file_path or "").lower().endswith(".pdf")


def pdf_files(db: Session, item_id: str) -> list[LiteratureFile]:
    rows = db.query(LiteratureFile).filter(LiteratureFile.item_id == item_id).all()
    return [f for f in rows if _is_pdf(f)]


def primary_file(db: Session, item_id: str) -> LiteratureFile | None:
    """挑一份代表文件：优先已抽好文本的，否则第一份 PDF，否则第一份附件。"""
    rows = db.query(LiteratureFile).filter(LiteratureFile.item_id == item_id).all()
    pdfs = [f for f in rows if _is_pdf(f)]
    for f in pdfs:
        if f.text_hash:
            return f
    if pdfs:
        return pdfs[0]
    return rows[0] if rows else None


def cached_pages(db: Session, file_id: str) -> list[str]:
    rows = (
        db.query(LiteratureFilePage)
        .filter(LiteratureFilePage.file_id == file_id)
        .order_by(LiteratureFilePage.page_no.asc())
        .all()
    )
    return [r.text or "" for r in rows]


def cached_single_page(db: Session, file_id: str, page_no: int) -> str | None:
    """单页命中查询。翻页只用得到这一条，不必把整篇读进内存。"""
    row = (
        db.query(LiteratureFilePage)
        .filter(LiteratureFilePage.file_id == file_id, LiteratureFilePage.page_no == page_no)
        .first()
    )
    return None if row is None else (row.text or "")


def cached_page_count(db: Session, file_id: str) -> int:
    return db.query(LiteratureFilePage).filter(LiteratureFilePage.file_id == file_id).count()


def store_pages(db: Session, file_row: LiteratureFile, pages: list[str]) -> str:
    """把页文本写进缓存并回填 `text_hash` / `page_count`。调用方负责提交。"""
    cleaned = [clean_extracted_text(p)[:PAGE_TEXT_LIMIT] for p in (pages or [])]
    digest = text_hash_of(cleaned)
    unchanged = file_row.text_hash == digest and cached_page_count(db, file_row.id) == len(cleaned)
    if not unchanged:
        db.query(LiteratureFilePage).filter(LiteratureFilePage.file_id == file_row.id).delete()
        for idx, text in enumerate(cleaned, start=1):
            db.add(
                LiteratureFilePage(
                    file_id=file_row.id,
                    item_id=file_row.item_id,
                    page_no=idx,
                    text=text,
                )
            )
        file_row.text_hash = digest
    if cleaned:
        file_row.page_count = len(cleaned)
    return digest


def store_structure(file_row: LiteratureFile, structure: dict | None) -> None:
    """GROBID 缺失时不写空壳，宁可让侧栏说「还没有结构」。"""
    if not structure:
        return
    if not (structure.get("sections") or structure.get("figures")):
        return
    file_row.structure_json = clean_extracted_text(json.dumps(structure, ensure_ascii=False))[:200000]


def item_structure(db: Session, item_id: str) -> dict:
    for f in pdf_files(db, item_id):
        raw = f.structure_json
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and (
            payload.get("sections") or payload.get("figures") or payload.get("equations") or payload.get("algorithms")
        ):
            return payload
    return {"title": "", "sections": [], "figures": []}


def _persist_isolated(
    file_id: str,
    pages: list[str],
    *,
    parse_status: str,
    parse_error: str | None,
    structure: dict | None = None,
) -> None:
    """用独立 session 回写缓存，失败就当没缓存，不影响本次请求。"""
    from fengsuan.app.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(LiteratureFile, file_id)
        if row is None:
            return
        row.parse_status = parse_status
        row.parse_error = parse_error
        store_pages(db, row, pages)
        store_structure(row, structure)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def item_pages(db: Session, item: LiteratureItem, *, parse_if_missing: bool = True) -> tuple[list[str], str]:
    """整篇页文本。返回 `(pages, source)`，`source ∈ {cache, parsed, none}`。"""
    files = pdf_files(db, item.id)
    for f in files:
        pages = cached_pages(db, f.id)
        if any((p or "").strip() for p in pages):
            return pages, "cache"
    if not parse_if_missing:
        return [], "none"
    from fengsuan.app.services.pdfparse import extract_pdf_path_full

    for f in files:
        path = f.file_path or ""
        if not path or not Path(path).exists():
            continue
        try:
            pages, status, err, structure = extract_pdf_path_full(path, use_grobid=False)
        except Exception:
            continue
        if status == "unusable":
            _persist_isolated(f.id, [], parse_status=status, parse_error=err)
            continue
        if any((p or "").strip() for p in pages):
            _persist_isolated(f.id, pages, parse_status=status, parse_error=err, structure=structure)
            return pages, "parsed"
    return [], "none"


def item_single_page(db: Session, item: LiteratureItem, page_no: int | None) -> dict:
    """翻页专用：命中缓存时只查一行，未命中才退回整篇解析。"""
    wanted = max(1, int(page_no or 1))
    f = primary_file(db, item.id)
    if f is not None and f.text_hash:
        total = cached_page_count(db, f.id)
        if total:
            text = cached_single_page(db, f.id, wanted)
            if text is None and wanted > total:
                text = cached_single_page(db, f.id, 1)
            body = (text or "")[:4000]
            return {
                "page_text": body,
                "has_fulltext": True,
                "page_count": total,
                "source": "cache",
            }
    pages, source = item_pages(db, item, parse_if_missing=False)
    has_fulltext = bool(pages and any((p or "").strip() for p in pages))
    body = ""
    if has_fulltext:
        idx = wanted if 1 <= wanted <= len(pages) else 1
        body = (pages[idx - 1] or "")[:4000]
    return {
        "page_text": body,
        "has_fulltext": has_fulltext,
        "page_count": len(pages) if pages else 0,
        "source": source,
    }


def page_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


def read_page_translation(db: Session, item_id: str, page_no: int, lang: str, *, fingerprint: str) -> str | None:
    """页译文只在指纹一致时算命中。原文重抽过就当没译过，绝不拿旧译文冒充。"""
    from fengsuan.app.models.literature import LiteraturePageTranslation

    row = (
        db.query(LiteraturePageTranslation)
        .filter(
            LiteraturePageTranslation.item_id == item_id,
            LiteraturePageTranslation.page_no == page_no,
            LiteraturePageTranslation.lang == lang,
        )
        .first()
    )
    if row is None or not (row.text or "").strip():
        return None
    if fingerprint and row.text_hash and row.text_hash != fingerprint:
        return None
    return row.text


def write_page_translation(db: Session, item_id: str, page_no: int, lang: str, text: str, *, fingerprint: str) -> None:
    from sqlalchemy.exc import IntegrityError

    from fengsuan.app.db.base import utcnow
    from fengsuan.app.models.literature import LiteraturePageTranslation

    def _load():
        return (
            db.query(LiteraturePageTranslation)
            .filter(
                LiteraturePageTranslation.item_id == item_id,
                LiteraturePageTranslation.page_no == page_no,
                LiteraturePageTranslation.lang == lang,
            )
            .first()
        )

    row = _load()
    body = (text or "")[:PAGE_TEXT_LIMIT]
    if row is None:
        try:
            with db.begin_nested():
                row = LiteraturePageTranslation(item_id=item_id, page_no=page_no, lang=lang)
                db.add(row)
                row.text = body
                row.text_hash = fingerprint
                row.updated_at = utcnow()
                db.flush()
                return
        except IntegrityError:
            row = _load()
            if row is None:
                raise
    row.text = body
    row.text_hash = fingerprint
    row.updated_at = utcnow()


def queue_item_extract(db: Session, item_id: str) -> int:
    """把这篇的 PDF 标回 pending，让 worker 在后台抽取并落库。返回入队份数。"""
    rows = pdf_files(db, item_id)
    queued = 0
    for f in rows:
        if f.text_hash and cached_page_count(db, f.id):
            continue
        f.parse_status = "pending"
        queued += 1
    return queued
