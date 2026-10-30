"""后台把 PDF 送去 GROBID：拿章节、作者、参考文献，再按章节重切知识库块。

GROBID 没配、没起来或解析失败，都退回按页切，不影响入库和翻页。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy.orm import Session

from fengsuan.app.models.literature import LiteratureFile, LiteratureItem

_DOWN_UNTIL = {"t": 0.0}


def _apply(db: Session, f: LiteratureFile, item: LiteratureItem, parsed: dict) -> bool:
    from fengsuan.app.services.biblio import link_item_as_cited, save_references, sync_authorships
    from fengsuan.app.services.kb import index_literature_structured
    from fengsuan.app.services.pagecache import cached_pages
    from fengsuan.app.services.pdfparse import clean_extracted_text
    from fengsuan.app.services.tei import chunk_structured, structure_from_tei

    structure = structure_from_tei(parsed)
    old: dict = {}
    if f.structure_json:
        try:
            old = json.loads(f.structure_json) or {}
        except json.JSONDecodeError:
            old = {}
    pages = cached_pages(db, f.id)
    for sec in structure["sections"]:
        if not sec.get("page_no"):
            from fengsuan.app.services.pdfparse import _locate_heading

            sec["page_no"] = _locate_heading(sec.get("title") or "", pages)
    if structure["sections"] or structure["figures"] or not old:
        f.structure_json = clean_extracted_text(json.dumps(structure, ensure_ascii=False))[:200000]
    if not (item.abstract or "").strip() and parsed.get("abstract"):
        item.abstract = clean_extracted_text(parsed["abstract"])[:6000]
    if parsed.get("doi") and not item.doi:
        item.doi = parsed["doi"][:128]
    details = [
        {"name": a["name"], "orcid": a.get("orcid") or "", "affiliation": a.get("affiliation") or ""}
        for a in parsed.get("authors") or []
        if a.get("name")
    ]
    if details:
        if item.authors_json in ("", "[]", None):
            item.authors_json = json.dumps([d["name"] for d in details], ensure_ascii=False)
        with db.begin_nested():
            sync_authorships(db, item, details, evidence_source="grobid")
    if parsed.get("references"):
        with db.begin_nested():
            save_references(db, item, parsed["references"], source="grobid")
    link_item_as_cited(db, item)
    chunks = chunk_structured(parsed, pages)
    if chunks:
        index_literature_structured(db, item, pages, chunks, file_path=f.file_path or "")
    return bool(chunks)


def structure_pending(db: Session, *, limit: int = 1) -> int:
    from fengsuan.app.services.pdfparse import grobid_alive, grobid_base_url, grobid_xml
    from fengsuan.app.services.tei import parse_tei

    if not grobid_base_url() or _DOWN_UNTIL["t"] > time.time():
        return 0
    files = (
        db.query(LiteratureFile)
        .filter(
            LiteratureFile.parse_status == "ok",
            LiteratureFile.text_hash.isnot(None),
            LiteratureFile.structure_state == "",
        )
        .order_by(LiteratureFile.id.desc())
        .limit(limit)
        .all()
    )
    if not files:
        return 0
    if not grobid_alive():
        _DOWN_UNTIL["t"] = time.time() + 300
        return 0
    done = 0
    for f in files:
        item = db.get(LiteratureItem, f.item_id) if f.item_id else None
        path = Path(f.file_path or "")
        if item is None or item.deleted_at is not None or not path.exists():
            f.structure_state = "miss"
            continue
        xml = grobid_xml(path.read_bytes(), timeout=120.0, detailed=True)
        parsed = parse_tei(xml or "")
        if not parsed:
            f.structure_state = "miss"
            done += 1
            continue
        try:
            with db.begin_nested():
                ok = _apply(db, f, item, parsed)
            f.structure_state = "ok" if ok else "miss"
        except Exception:
            f.structure_state = "miss"
        done += 1
    return done


def structure_progress(db: Session, lab_id: str) -> dict:
    from sqlalchemy import func

    rows = (
        db.query(LiteratureFile.structure_state, func.count(LiteratureFile.id))
        .join(LiteratureItem, LiteratureItem.id == LiteratureFile.item_id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None), LiteratureFile.parse_status == "ok")
        .group_by(LiteratureFile.structure_state)
        .all()
    )
    counts = {str(k or ""): int(v) for k, v in rows}
    return {"done": counts.get("ok", 0), "fallback": counts.get("miss", 0), "waiting": counts.get("", 0)}
