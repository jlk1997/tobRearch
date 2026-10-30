from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.safepath import write_bytes_under
from fengsuan.app.models.kb import GlossaryTerm, KbChunk, KbDocument, KnowledgeBase

SOURCE_KINDS = ("paper", "lab_intro", "meeting_minutes", "manuscript", "profile")


def infer_source_kind(filename: str = "", explicit: str | None = None) -> str:
    kind = (explicit or "").strip()
    if kind in SOURCE_KINDS:
        return kind
    name = (filename or "").lower()
    if any(tok in name for tok in ("minute", "纪要", "组会")):
        return "meeting_minutes"
    if any(tok in name for tok in ("intro", "handbook", "入门", "手册", "welcome")):
        return "lab_intro"
    if any(tok in name for tok in ("manuscript", "draft", "手稿")):
        return "manuscript"
    if any(tok in name for tok in ("profile", "bio", "档案")):
        return "profile"
    return "paper"


def hash_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for tok in re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower()):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(a[i] * b[i] for i in range(n))


def index_member_note(db: Session, lab_id: str, user_id: str, *, url: str, text: str, member_user_id: str) -> str:
    """把成员留给助手的网页摘要放进现有知识库。"""
    kb = get_or_create_kb(db, lab_id)
    filename = (url or "profile")[:300]
    doc = (
        db.query(KbDocument)
        .filter(KbDocument.kb_id == kb.id, KbDocument.original_filename == filename, KbDocument.source_kind == "profile")
        .first()
    )
    if doc is None:
        doc = KbDocument(
            kb_id=kb.id,
            original_filename=filename,
            file_path="",
            status="pending",
            uploaded_by=user_id or member_user_id,
            source_kind="profile",
        )
        db.add(doc)
        db.flush()
    body = (text or "").strip()[:4000] or filename
    index_document(
        db,
        doc,
        [body],
        chunks=[
            {
                "page_no": 1,
                "content": body,
                "heading_path": filename[:180],
                "member_user_id": member_user_id,
                "kind": "page",
            }
        ],
    )
    return doc.id


def get_or_create_kb(db: Session, lab_id: str) -> KnowledgeBase:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
    if kb:
        return kb
    settings = get_settings()
    kb = KnowledgeBase(lab_id=lab_id, embedding_model=settings.embedding_model, dim=settings.embedding_dim)
    db.add(kb)
    db.flush()
    return kb


def _heading_from_text(text: str) -> str:
    m = re.match(r"\[sec\.\d+\]\s*(.{1,80})", text or "", flags=re.I)
    if m:
        return m.group(1).strip()
    first = (text or "").strip().split("\n", 1)[0].strip()
    return first[:80]


def chunk_pages(pages: list[str], max_chars: int = 1200, overlap: int = 180) -> list[dict]:
    """按页切块，约 15% 重叠，并写入 heading_path。"""
    out: list[dict] = []
    for i, page in enumerate(pages, start=1):
        text = (page or "").strip()
        if not text:
            continue
        heading = _heading_from_text(text)
        if len(text) <= max_chars:
            out.append({"page_no": i, "content": text, "heading_path": heading})
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            piece = text[start:end].strip()
            if piece:
                out.append({"page_no": i, "content": piece, "heading_path": heading})
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return out


def pages_hash(pages: list[str]) -> str:
    h = hashlib.sha256()
    for page in pages:
        h.update((page or "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def lexical_score(query: str, content: str) -> float:
    qtoks = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", (query or "").lower()))
    if not qtoks:
        return 0.0
    ctoks = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", (content or "").lower()))
    if not ctoks:
        return 0.0
    return len(qtoks & ctoks) / len(qtoks)


def expand_query_with_glossary(db: Session, lab_id: str, q: str) -> str:
    extra: list[str] = []
    needle = (q or "").lower()
    if not needle:
        return q
    for t in db.query(GlossaryTerm).filter(GlossaryTerm.lab_id == lab_id).all():
        src = (t.src or "").strip()
        tgt = (t.tgt or "").strip()
        if src and src.lower() in needle and tgt and tgt.lower() not in needle:
            extra.append(tgt)
        if tgt and tgt.lower() in needle and src and src.lower() not in needle:
            extra.append(src)
    if not extra:
        return q
    return (q + " " + " ".join(extra[:8])).strip()


def mmr_select(rows: list[tuple[float, str, str, int | None]], k: int = 8, lam: float = 0.7) -> list[tuple[float, str, str, int | None]]:
    """按分数与页/文档多样性挑选，避免 8 条全来自同一页。"""
    if len(rows) <= k:
        return rows
    picked: list[tuple[float, str, str, int | None]] = []
    rest = list(rows)
    while rest and len(picked) < k:
        best = None
        best_s = -1.0
        for cand in rest:
            score, _cid, doc_id, page_no = cand
            same = sum(1 for _s, _i, d, p in picked if d == doc_id and p == page_no)
            same_doc = sum(1 for _s, _i, d, _p in picked if d == doc_id)
            penalty = 0.35 * same + 0.15 * same_doc
            adj = lam * score - (1 - lam) * penalty
            if adj > best_s:
                best_s = adj
                best = cand
        if best is None:
            break
        picked.append(best)
        rest.remove(best)
    return picked


def kb_doc_ids_for_query(db: Session, kb: KnowledgeBase, direction_id: str | None) -> list[str]:
    q = db.query(KbDocument).filter(KbDocument.kb_id == kb.id, KbDocument.status == "indexed", KbDocument.deleted_at.is_(None))
    docs = q.all()
    if not direction_id:
        return [d.id for d in docs]
    from fengsuan.app.models.literature import LiteratureDirectionLink
    from fengsuan.app.services.directions import direction_scope_ids

    scope = set(direction_scope_ids(db, direction_id, include_children=True))
    if not scope:
        return []
    lit_ids = {
        iid
        for (iid,) in db.query(LiteratureDirectionLink.item_id)
        .filter(LiteratureDirectionLink.direction_id.in_(list(scope)))
        .all()
    }
    keep = []
    for d in docs:
        if d.direction_id and d.direction_id in scope:
            keep.append(d.id)
        elif d.literature_item_id and d.literature_item_id in lit_ids:
            keep.append(d.id)
    return keep


def feedback_boosts(db: Session, lab_id: str) -> dict[str, float]:
    from fengsuan.app.models.activity import ActivityEvent

    rows = (
        db.query(ActivityEvent)
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.verb == "kb.feedback",
            ActivityEvent.object_type == "kb_chunk",
        )
        .order_by(ActivityEvent.created_at.desc())
        .limit(400)
        .all()
    )
    scores: dict[str, float] = {}
    for ev in rows:
        cid = ev.object_id or ""
        if not cid:
            continue
        try:
            payload = json.loads(ev.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        useful = bool(payload.get("useful", True))
        scores[cid] = scores.get(cid, 0.0) + (0.12 if useful else -0.08)
    return scores


def _is_postgres(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


_PGVECTOR_OK: dict[str, bool] = {}


def ensure_pgvector(db: Session) -> bool:
    if not _is_postgres(db):
        return False
    if _PGVECTOR_OK.get("ok"):
        return True
    try:
        with db.begin_nested():
            db.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
            db.execute(sql_text("ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS embedding vector(1024)"))
        _PGVECTOR_OK["ok"] = True
        return True
    except Exception:
        return False


def _store_pgvector(db: Session, chunk_id: str, vec: list[float]) -> None:
    if not ensure_pgvector(db):
        return
    from fengsuan.app.services.search_index import store_vector

    store_vector(db, chunk_id, vec)


def approx_tokens(text: str) -> int:
    """粗估 token：汉字一个算一个，英文按词 1.3 个。"""
    body = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
    words = len(re.findall(r"[A-Za-z0-9]+", body))
    return int(cjk + words * 1.3) + 1


def read_document_pages(path: str) -> list[str]:
    p = Path(path or "")
    if not p.exists():
        return []
    if p.suffix.lower() == ".pdf":
        try:
            from fengsuan.app.services.pdfparse import extract_pdf_path

            pages, _status, _err = extract_pdf_path(str(p))
            return pages or []
        except Exception:
            return []
    try:
        return [p.read_text(encoding="utf-8", errors="ignore")]
    except Exception:
        return []


def index_document(db: Session, doc: KbDocument, pages: list[str], chunks: list[dict] | None = None) -> str:
    """切块、向量化、写库。chunks 由按章节切块给出；不给就按页切。"""
    kb = db.get(KnowledgeBase, doc.kb_id)
    dim = kb.dim if kb else get_settings().embedding_dim
    lab_id = kb.lab_id if kb else ""
    structured = chunks is not None
    digest = pages_hash(pages)
    if structured:
        digest = hashlib.sha256((digest + "|sections").encode("utf-8")).hexdigest()
    if getattr(doc, "content_hash", "") == digest and doc.status == "indexed" and (doc.chunk_count or 0) > 0:
        return "cached"
    existing = list(db.query(KbChunk).filter(KbChunk.doc_id == doc.id).all())
    by_content: dict[str, KbChunk] = {}
    for ch in existing:
        by_content.setdefault(ch.content, ch)
    if not structured:
        chunks = chunk_pages(pages)
        if _is_abstract_doc(doc):
            for ch in chunks:
                ch["kind"] = "abstract"
    pg = ensure_pgvector(db)
    fallback = "hash"
    to_embed: list[str] = []
    reuse: dict[int, list[float]] = {}
    planned: list[tuple[int, dict, KbChunk | None]] = []
    used_old: set[str] = set()
    for seq, ch in enumerate(chunks):
        prev = by_content.get(ch["content"])
        reusable = (
            prev is not None
            and (prev.embedding_json or "") not in ("", "[]")
            and (prev.embed_model or "") != "hash"
        )
        if prev is not None and prev.id not in used_old:
            used_old.add(prev.id)
            planned.append((seq, ch, prev))
            if reusable:
                try:
                    reuse[seq] = json.loads(prev.embedding_json or "[]")
                except json.JSONDecodeError:
                    to_embed.append(ch["content"])
            else:
                to_embed.append(ch["content"])
        else:
            planned.append((seq, ch, None))
            to_embed.append(ch["content"])
    vecs_new: list[list[float]] = []
    model_name = "hash"
    if to_embed:
        from fengsuan.app.services.llm import embed_model_name, embed_texts

        vecs_new, fallback = embed_texts(
            db,
            user_id=doc.uploaded_by or "",
            lab_id=lab_id,
            texts=to_embed,
            dim=dim,
        )
        if fallback != "hash":
            model_name = embed_model_name(db, user_id=doc.uploaded_by or "", lab_id=lab_id)
    else:
        fallback = "cached"
    new_iter = iter(vecs_new)
    keep_ids: list[str] = []
    for seq, ch, prev in planned:
        vec = reuse.get(seq)
        if vec is None:
            vec = next(new_iter, hash_embed(ch["content"], dim))
        meta_obj = {"page_no": ch.get("page_no"), "heading": ch.get("heading_path") or ""}
        if ch.get("member_user_id"):
            meta_obj["member_user_id"] = str(ch.get("member_user_id"))[:36]
        meta = json.dumps(meta_obj, ensure_ascii=False)
        heading = (ch.get("heading_path") or "")[:500]
        kind = (ch.get("kind") or "page")[:20]
        section = (ch.get("section") or "")[:300]
        tokens = int(ch.get("token_count") or approx_tokens(ch["content"]))
        if prev is not None:
            prev.seq = seq
            prev.page_no = ch.get("page_no")
            prev.heading_path = heading
            prev.metadata_json = meta
            prev.lab_id = lab_id
            prev.literature_item_id = doc.literature_item_id
            prev.kind = kind
            prev.section = section
            prev.token_count = tokens
            if seq not in reuse:
                prev.embedding_json = json.dumps(vec)
                prev.embed_model = model_name
            keep_ids.append(prev.id)
            if pg:
                _store_pgvector(db, prev.id, vec)
            continue
        row = KbChunk(
            doc_id=doc.id,
            seq=seq,
            content=ch["content"],
            embedding_json=json.dumps(vec),
            page_no=ch.get("page_no"),
            heading_path=heading,
            metadata_json=meta,
            lab_id=lab_id,
            literature_item_id=doc.literature_item_id,
            kind=kind,
            section=section,
            token_count=tokens,
            embed_model=model_name,
        )
        db.add(row)
        db.flush()
        keep_ids.append(row.id)
        if pg:
            _store_pgvector(db, row.id, vec)
    stale = [c.id for c in existing if c.id not in set(keep_ids)]
    if stale:
        db.query(KbChunk).filter(KbChunk.id.in_(stale)).delete(synchronize_session=False)
    doc.chunk_count = len(chunks)
    doc.content_hash = digest
    doc.status = "indexed" if chunks else "failed"
    if not chunks:
        doc.parse_error = "empty"
    return fallback


_EMBED_COOLDOWN: dict[str, float] = {}


def _looks_hashed(raw: str) -> bool:
    """老块没记模型名：兜底向量绝大多数维是 0，真向量几乎没有 0。"""
    try:
        vec = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return True
    if not vec:
        return True
    head = vec[:128]
    zeros = sum(1 for x in head if not x)
    return zeros >= len(head) * 0.6


def classify_legacy_chunks(db: Session, *, limit: int = 400) -> int:
    rows = db.query(KbChunk).filter((KbChunk.embed_model.is_(None)) | (KbChunk.embed_model == "")).limit(limit).all()
    for row in rows:
        row.embed_model = "hash" if _looks_hashed(row.embedding_json) else "legacy"
    return len(rows)


def reembed_hash_chunks(db: Session, *, limit: int = 64) -> int:
    """兜底向量的块，等有了向量服务再补成真向量。每轮只补一个组的一批。"""
    import time

    if classify_legacy_chunks(db):
        db.flush()
    now = time.time()
    cooled = [lab for lab, until in _EMBED_COOLDOWN.items() if until > now]
    q = db.query(KbChunk).filter(KbChunk.embed_model == "hash", KbChunk.lab_id != "")
    if cooled:
        q = q.filter(~KbChunk.lab_id.in_(cooled))
    first = q.first()
    if first is None:
        return 0
    lab_id = first.lab_id
    rows = q.filter(KbChunk.lab_id == lab_id).limit(limit).all()
    doc = db.get(KbDocument, first.doc_id)
    uid = (doc.uploaded_by if doc else "") or ""
    from fengsuan.app.services.llm import embed_model_name, embed_texts

    model = embed_model_name(db, user_id=uid, lab_id=lab_id)
    if model == "hash":
        _EMBED_COOLDOWN[lab_id] = now + 1800
        return 0
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
    dim = kb.dim if kb else get_settings().embedding_dim
    vecs, fallback = embed_texts(db, user_id=uid, lab_id=lab_id, texts=[r.content for r in rows], dim=dim)
    if fallback == "hash" or len(vecs) != len(rows):
        _EMBED_COOLDOWN[lab_id] = now + 1800
        return 0
    pg = ensure_pgvector(db)
    for row, vec in zip(rows, vecs):
        row.embedding_json = json.dumps(vec)
        row.embed_model = model
        if pg:
            _store_pgvector(db, row.id, vec)
    return len(rows)


def embed_progress(db: Session, lab_id: str) -> dict:
    from sqlalchemy import func

    rows = (
        db.query(KbChunk.embed_model, func.count(KbChunk.id))
        .filter(KbChunk.lab_id == lab_id)
        .group_by(KbChunk.embed_model)
        .all()
    )
    total = sum(int(n) for _m, n in rows)
    hashed = sum(int(n) for m, n in rows if (m or "") in {"hash", ""})
    return {"chunks": total, "fallback_chunks": hashed, "real_chunks": total - hashed}


def _is_abstract_doc(doc) -> bool:
    return (getattr(doc, "original_filename", "") or "").endswith(".abstract.txt")


def index_literature_if_readable(db: Session, item, user_id: str) -> str:
    """有正文就进知识库；只有题录摘要也先放进去，PDF 后补上来再换成全文。

    不再等挂上方向。读不出就跳过，不打断入库。
    """
    from pathlib import Path

    from fengsuan.app.models.kb import KbDocument
    from fengsuan.app.models.literature import LiteratureFile

    docs = (
        db.query(KbDocument)
        .filter(KbDocument.literature_item_id == item.id, KbDocument.deleted_at.is_(None))
        .all()
    )
    full = [
        d
        for d in docs
        if d.status == "indexed" and (d.chunk_count or 0) > 0 and not _is_abstract_doc(d)
    ]
    if full:
        return "skipped"
    abstract_doc = next((d for d in docs if _is_abstract_doc(d)), None)
    f = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).first()
    pages: list[str] = []
    file_path = ""
    abstract_only = False
    if f is not None and f.file_path and Path(f.file_path).exists():
        from fengsuan.app.services.pdfparse import extract_pdf_path

        extracted, status, _err = extract_pdf_path(f.file_path)
        pages = [p for p in (extracted or []) if (p or "").strip()]
        if status != "unusable" and pages:
            file_path = f.file_path
    if not pages:
        text = "\n\n".join(x for x in ((item.title or "").strip(), (item.abstract or "").strip()) if x).strip()
        if len(text) < 20:
            return "no_body"
        pages = [text]
        abstract_only = True
        if abstract_doc is not None and abstract_doc.status == "indexed" and (abstract_doc.chunk_count or 0) > 0:
            return "skipped"
    try:
        with db.begin_nested():
            kb = get_or_create_kb(db, item.lab_id)
            doc = abstract_doc
            name = (item.title or "paper")[:80] + (".abstract.txt" if abstract_only else ".txt")
            if doc is None:
                doc = KbDocument(
                    kb_id=kb.id,
                    literature_item_id=item.id,
                    original_filename=name,
                    file_path=file_path,
                    uploaded_by=user_id or item.added_by,
                    source_kind="paper",
                )
                db.add(doc)
                db.flush()
            else:
                doc.original_filename = name
                doc.file_path = file_path or doc.file_path
                doc.status = "pending"
            index_document(db, doc, pages)
        return "abstract" if abstract_only else "indexed"
    except Exception:
        return "no_body"


def _python_vector_scan(
    db: Session, lab_id: str, qvec: list[float], scope_ids: list[str] | None, limit: int
) -> list[tuple[str, str, int | None, float]]:
    """没有 pgvector 时的兜底：本组的块逐个算余弦。只在本地开发和小库用。"""
    rows_q = (
        db.query(KbChunk.id, KbChunk.doc_id, KbChunk.embedding_json, KbChunk.page_no)
        .join(KbDocument, KbDocument.id == KbChunk.doc_id)
        .filter(
            KbChunk.lab_id == lab_id,
            KbDocument.status == "indexed",
            KbDocument.deleted_at.is_(None),
            (KbChunk.kind.is_(None)) | (KbChunk.kind != "reference"),
        )
    )
    if scope_ids is not None:
        rows_q = rows_q.filter(KbChunk.doc_id.in_(scope_ids))
    scored = []
    for cid, doc_id, raw, page_no in rows_q.all():
        try:
            vec = json.loads(raw or "[]")
        except json.JSONDecodeError:
            vec = []
        scored.append((cid, doc_id, page_no, cosine(qvec, vec)))
    scored.sort(key=lambda row: row[3], reverse=True)
    return scored[:limit]


def index_literature_structured(db: Session, item, pages: list[str], chunks: list[dict], file_path: str = "") -> str:
    """GROBID 切好的块替换这篇原来的按页块。摘要文档升级成全文文档。"""
    docs = (
        db.query(KbDocument)
        .filter(KbDocument.literature_item_id == item.id, KbDocument.deleted_at.is_(None))
        .all()
    )
    doc = next((d for d in docs if not _is_abstract_doc(d)), None) or next(iter(docs), None)
    with db.begin_nested():
        kb = get_or_create_kb(db, item.lab_id)
        name = (item.title or "paper")[:80] + ".txt"
        if doc is None:
            doc = KbDocument(
                kb_id=kb.id,
                literature_item_id=item.id,
                original_filename=name,
                file_path=file_path,
                uploaded_by=item.added_by,
                source_kind="paper",
            )
            db.add(doc)
            db.flush()
        elif _is_abstract_doc(doc):
            doc.original_filename = name
            doc.file_path = file_path or doc.file_path
        index_document(db, doc, pages, chunks=chunks)
        for extra in docs:
            if extra.id != doc.id and _is_abstract_doc(extra):
                from fengsuan.app.db.base import utcnow

                extra.deleted_at = utcnow()
    return "structured"


def query_kb(
    db: Session,
    lab_id: str,
    q: str,
    top_k: int = 8,
    user_id: str | None = None,
    direction_id: str | None = None,
    source_kinds: list[str] | None = None,
    item_id: str | None = None,
) -> tuple[list[dict], str]:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
    if kb is None:
        return [], "hash"
    from fengsuan.app.services import search_index as si
    from fengsuan.app.services.llm import embed_texts, rerank_texts

    expanded = expand_query_with_glossary(db, lab_id, q)
    qvecs, fallback = embed_texts(db, user_id=user_id or "", lab_id=lab_id, texts=[expanded], dim=kb.dim)
    qvec = qvecs[0] if qvecs else hash_embed(expanded, kb.dim)
    pg = si.is_postgres(db)
    kinds = [k for k in (source_kinds or []) if k in SOURCE_KINDS] or None
    dir_ids = None
    if direction_id:
        from fengsuan.app.services.directions import direction_scope_ids

        dir_ids = direction_scope_ids(db, direction_id, include_children=True)
        if not dir_ids:
            return [], fallback
    scope_ids = None
    if not pg and (kinds or dir_ids):
        docs = (
            db.query(KbDocument.id, KbDocument.source_kind, KbDocument.direction_id, KbDocument.literature_item_id)
            .filter(KbDocument.kb_id == kb.id, KbDocument.status == "indexed", KbDocument.deleted_at.is_(None))
            .all()
        )
        if dir_ids:
            allowed = set(kb_doc_ids_for_query(db, kb, direction_id))
            docs = [d for d in docs if d.id in allowed]
        if kinds:
            docs = [d for d in docs if (d.source_kind or "paper") in set(kinds)]
        scope_ids = [d.id for d in docs]
        if not scope_ids:
            return [], fallback
        kinds, dir_ids = None, None
    if item_id:
        item_docs = [
            row.id
            for row in db.query(KbDocument.id)
            .filter(
                KbDocument.kb_id == kb.id,
                KbDocument.literature_item_id == item_id,
                KbDocument.status == "indexed",
                KbDocument.deleted_at.is_(None),
            )
            .all()
        ]
        if scope_ids is not None:
            allowed = set(item_docs)
            scope_ids = [doc_id for doc_id in scope_ids if doc_id in allowed]
        else:
            scope_ids = item_docs
        if not scope_ids:
            return [], fallback
        kinds, dir_ids = None, None
    elif (
        db.query(KbDocument.id)
        .filter(KbDocument.kb_id == kb.id, KbDocument.status == "indexed", KbDocument.deleted_at.is_(None))
        .first()
        is None
    ):
        return [], fallback
    pool = 100
    vec_rows = si.vector_candidates(
        db, lab_id, qvec, doc_ids=scope_ids, source_kinds=kinds, direction_ids=dir_ids, limit=pool
    )
    narrowed = bool(scope_ids or kinds or dir_ids)
    if vec_rows is None or (not vec_rows and not narrowed and not pg):
        vec_rows = _python_vector_scan(db, lab_id, qvec, scope_ids, pool)
    elif vec_rows is None:
        vec_rows = []
    terms = si.search_terms(expanded)
    kw_rows = si.keyword_candidates(
        db, lab_id, terms, doc_ids=scope_ids, source_kinds=kinds, direction_ids=dir_ids, limit=pool
    )
    used_lex = bool(kw_rows)
    vec_weight = 0.6 if fallback == "hash" else 1.0
    fused = si.rrf_fuse([(vec_weight, vec_rows), (1.0, kw_rows)])
    cand = fused[:60]
    cand_ids = [c[1] for c in cand]
    preview_chunks = {ch.id: ch.content for ch in db.query(KbChunk).filter(KbChunk.id.in_(cand_ids)).all()} if cand_ids else {}
    boosts = feedback_boosts(db, lab_id)
    reranked = []
    for s, cid, doc_id, page_no in cand:
        lex = lexical_score(expanded, preview_chunks.get(cid) or "")
        reranked.append((s + 0.004 * lex + boosts.get(cid, 0.0) * 0.08, cid, doc_id, page_no))
    reranked.sort(key=lambda row: row[0], reverse=True)
    if reranked:
        head = reranked[:30]
        docs_text = [preview_chunks.get(c[1]) or "" for c in head]
        ranked = rerank_texts(db, user_id=user_id or "", lab_id=lab_id, query=expanded, documents=docs_text)
        if ranked:
            order = {t: i for i, t in enumerate(ranked)}
            head = sorted(head, key=lambda row: order.get(preview_chunks.get(row[1]) or "", 999))
            reranked = head + reranked[30:]
    reranked = si.cap_per_doc(reranked)
    if reranked:
        levels = sorted((row[0] for row in reranked), reverse=True)
        peak = levels[0] or 1.0
        reranked = [(lvl / peak, cid, doc_id, page_no) for lvl, (_s, cid, doc_id, page_no) in zip(levels, reranked)]
    top = mmr_select(reranked, k=top_k)
    top_ids = [c[1] for c in top]
    if not top_ids:
        return [], fallback
    chunk_map = {ch.id: ch for ch in db.query(KbChunk).filter(KbChunk.id.in_(top_ids)).all()}
    top_doc_ids = list({c[2] for c in top})
    doc_map = {
        d.id: d
        for d in db.query(KbDocument.id, KbDocument.original_filename, KbDocument.literature_item_id)
        .filter(KbDocument.id.in_(top_doc_ids))
        .all()
    }
    mode = "simple" if fallback == "hash" else ("hybrid" if used_lex else "vector")
    hits = []
    for score, cid, doc_id, _page in top:
        ch = chunk_map.get(cid)
        if ch is None:
            continue
        doc = doc_map.get(doc_id)
        snippet = ch.content
        hits.append(
            {
                "chunk_id": ch.id,
                "content": snippet,
                "page_no": ch.page_no,
                "heading_path": ch.heading_path or "",
                "section": ch.section or "",
                "chunk_kind": ch.kind or "",
                "filename": doc.original_filename if doc else "",
                "literature_item_id": doc.literature_item_id if doc else None,
                "doc_id": ch.doc_id,
                "score": round(score, 4),
                "citation": snippet[:400],
                "retrieval_mode": mode,
            }
        )
    if user_id:
        from fengsuan.app.services.literature import attach_reading_fields

        attach_reading_fields(db, user_id, hits)
    from fengsuan.app.services.literature import stamp_open_href, stamp_reproduce_href
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_open_href(hits, id_key="literature_item_id")
    stamp_reproduce_href(lab_id, hits, id_key="literature_item_id")
    stamp_latest_reproduce(db, hits, id_key="literature_item_id")
    return hits, fallback


def assert_citations_are_substrings(hits: list[dict]) -> None:
    for h in hits:
        if h["citation"] not in h["content"]:
            raise ValueError("citation must be a substring of chunk")


def list_glossary(db: Session, lab_id: str) -> list[dict]:
    rows = db.query(GlossaryTerm).filter(GlossaryTerm.lab_id == lab_id).all()
    return [
        {
            "id": t.id,
            "src": t.src,
            "tgt": t.tgt,
            "lang_pair": t.lang_pair,
            "do_not_translate": t.do_not_translate,
            "domain": t.domain,
            "created_by": t.created_by,
        }
        for t in rows
    ]


def save_upload(lab_id: str, filename: str, content: bytes) -> Path:
    dest_dir = Path(get_settings().file_root) / "files" / lab_id / "kb"
    return write_bytes_under(dest_dir, filename, content, default="doc.txt")


def _kb_spoken(summary: str, hits: list, direction_id: str | None) -> str:
    text = (summary or "").strip()
    leaked = ("每条主张必须带来源页码", "无法综述", "你收到的规则", "禁止解释")
    empty_line = "这个方向的知识库还没有论文。先把文献入库。" if direction_id else "知识库还没有论文。先把文献入库。"
    if not hits:
        return empty_line
    if not text or any(bit in text for bit in leaked):
        return "这些论文里还没有足够的正文"
    return text


def summarize_lab_kb(db, lab_id: str, user_id: str, direction_id: str | None = None) -> dict:
    from fengsuan.app.services.llm import chat
    from fengsuan.app.services.prompts import load_prompt

    hits, fallback = query_kb(
        db, lab_id, "综述 方法 数据 结论 贡献", top_k=6, user_id=user_id, direction_id=direction_id
    )
    paper_hits = [h for h in hits if len((h.get("content") or "").strip()) >= 24]
    if not paper_hits:
        from fengsuan.app.services.writing import user_unused_research_note

        empty_line = "这个方向的知识库还没有论文。先把文献入库。" if direction_id else "知识库还没有论文。先把文献入库。"
        unused_empty = user_unused_research_note(db, lab_id, user_id)
        return {
            "summary": empty_line,
            "citations": [],
            "empty_kb": True,
            "embed_fallback": fallback,
            "ungrounded": False,
            "ungrounded_spans": [],
            "reproduce_gap_md": "",
            "ungrounded_reproduce": "",
            "unused_research": unused_empty,
            "honesty_notes": [unused_empty] if unused_empty else [],
            "retrieval_mode": "simple" if fallback == "hash" else "vector",
            "direction_id": direction_id,
        }
    hits = paper_hits
    assert_citations_are_substrings(hits)
    from fengsuan.app.services.writing import user_unused_research_note
    from fengsuan.app.services.agent.features import attach_evidence, stamp_trace, try_seed_tools

    unused = user_unused_research_note(db, lab_id, user_id)
    pack = try_seed_tools(
        db,
        user_id=user_id,
        lab_id=lab_id,
        feature="kb_query",
        scope="lab",
        seeds=[
            ("search_lab_kb", {"query": "综述 方法 数据 结论 贡献"}),
            ("get_lab_glossary", {}),
        ],
        direction_id=direction_id or "",
    )
    context = "\n\n".join(f"[p.{h.get('page_no')}] {h.get('citation')}" for h in hits) or "（知识库尚无可用片段）"
    if unused:
        context = context + "\n" + unused["text"]
    summary = chat(
        db,
        user_id=user_id,
        lab_id=lab_id,
        messages=attach_evidence(
            [
                {
                    "role": "system",
                    "content": load_prompt("kb_summarize"),
                },
                {"role": "user", "content": context},
            ],
            pack,
        ),
        feature="kb_query",
    )
    from fengsuan.app.services.analysis import KB_GROUND_NOTE, ground_against_corpus

    corpus = "\n".join((h.get("content") or h.get("citation") or "") for h in hits)
    grounded = ground_against_corpus(summary, corpus, note=KB_GROUND_NOTE)
    from fengsuan.app.services.reproduce import compose_reproduce_gap_md, language_output_warning

    summary = _kb_spoken(grounded["text"], hits, direction_id)
    return stamp_trace(
        {
            "summary": summary,
            "citations": hits,
            "embed_fallback": fallback,
            "ungrounded": grounded["ungrounded"],
            "ungrounded_spans": list(grounded.get("spans") or []),
            "reproduce_gap_md": compose_reproduce_gap_md(db, [h.get("literature_item_id") for h in hits]),
            "ungrounded_reproduce": language_output_warning(db, summary, lab_id=lab_id),
            "unused_research": unused,
            "honesty_notes": [unused] if unused else [],
            "retrieval_mode": (hits[0].get("retrieval_mode") if hits else ("simple" if fallback == "hash" else "vector")),
            "direction_id": direction_id,
        },
        pack,
    )
