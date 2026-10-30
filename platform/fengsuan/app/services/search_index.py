"""几千篇论文的检索底座：向量索引、全文索引、关键词召回、排名融合。

Postgres 上用 pgvector HNSW + pg_trgm；本地 SQLite 退回 LIKE 与 Python 余弦。
"""

from __future__ import annotations

import re

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

DIM = 1024
RRF_K = 60
PER_DOC_CAP = 3

_STATE: dict[str, object] = {"checked": False, "vec_expr": "", "q_type": "vector"}

_STOP_ZH = (
    "什么",
    "这个",
    "那个",
    "我们",
    "你们",
    "他们",
    "论文",
    "文章",
    "哪些",
    "哪篇",
    "怎么",
    "一下",
    "讲讲",
    "介绍",
    "组里",
    "有没有",
    "是不是",
    "为什么",
    "如何",
    "的",
    "了",
    "吗",
    "呢",
    "和",
    "与",
    "在",
    "是",
)
_STOP_EN = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "what",
    "which",
    "paper",
    "papers",
    "about",
    "into",
    "are",
    "was",
    "how",
    "why",
    "does",
    "using",
    "based",
}


def is_postgres(conn_or_db) -> bool:
    try:
        bind = conn_or_db.get_bind() if hasattr(conn_or_db, "get_bind") else conn_or_db
        return bind.dialect.name == "postgresql"
    except Exception:
        return False


def ensure_search_schema(engine) -> None:
    """启动时补齐：老块回填 lab_id / literature_item_id；Postgres 建扩展和索引。"""
    backfill_sql = (
        "UPDATE kb_chunks SET "
        "lab_id = COALESCE((SELECT kb.lab_id FROM kb_documents d JOIN knowledge_bases kb ON kb.id = d.kb_id "
        "WHERE d.id = kb_chunks.doc_id), ''), "
        "literature_item_id = (SELECT d.literature_item_id FROM kb_documents d WHERE d.id = kb_chunks.doc_id) "
        "WHERE lab_id IS NULL OR lab_id = ''"
    )
    try:
        with engine.begin() as conn:
            conn.execute(sql_text(backfill_sql))
    except Exception:
        pass
    if engine.dialect.name != "postgresql":
        return
    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        f"ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS embedding vector({DIM})",
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_content_trgm ON kb_chunks USING gin (content gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_content_fts ON kb_chunks USING gin (to_tsvector('simple', content))",
        "CREATE INDEX IF NOT EXISTS ix_literature_items_title_trgm ON literature_items USING gin (title gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ix_literature_items_abstract_trgm ON literature_items USING gin (abstract gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ix_literature_items_authors_trgm ON literature_items USING gin (authors_json gin_trgm_ops)",
    ]
    for stmt in statements:
        try:
            with engine.begin() as conn:
                conn.execute(sql_text(stmt))
        except Exception:
            continue
    hnsw_halfvec = (
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding_hnsw ON kb_chunks "
        f"USING hnsw ((embedding::halfvec({DIM})) halfvec_cosine_ops)"
    )
    hnsw_plain = (
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding_hnsw_v ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    for stmt in (hnsw_halfvec, hnsw_plain):
        try:
            with engine.begin() as conn:
                conn.execute(sql_text(stmt))
            break
        except Exception:
            continue
    _STATE["checked"] = False


def _vector_mode(db: Session) -> tuple[str, str]:
    """返回 (列表达式, 查询向量类型)。有半精度 HNSW 就走它，省一半索引空间。"""
    if _STATE.get("checked"):
        return str(_STATE["vec_expr"]), str(_STATE["q_type"])
    expr, qtype = "c.embedding", "vector"
    try:
        with db.begin_nested():
            row = db.execute(
                sql_text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_kb_chunks_embedding_hnsw'")
            ).first()
        if row and "halfvec" in str(row[0]):
            expr, qtype = f"(c.embedding::halfvec({DIM}))", f"halfvec({DIM})"
    except Exception:
        pass
    _STATE.update({"checked": True, "vec_expr": expr, "q_type": qtype})
    return expr, qtype


def vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def search_terms(q: str, *, cap: int = 8) -> list[str]:
    """关键词召回用的词：英文取实词，中文按连续汉字切，去掉问句里的虚词。"""
    text = (q or "").strip()
    if not text:
        return []
    out: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9\-\.]{2,}", text):
        low = word.lower().strip(".-")
        if low in _STOP_EN or len(low) < 3:
            continue
        if low not in out:
            out.append(low)
    zh = text
    for stop in sorted(_STOP_ZH, key=len, reverse=True):
        zh = zh.replace(stop, " ")
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", zh):
        if len(run) <= 6:
            pieces = [run]
        else:
            pieces = [run[i : i + 4] for i in range(0, len(run) - 3, 2)]
        for piece in pieces:
            if piece not in out:
                out.append(piece)
    for num in re.findall(r"\b\d{4}\.\d{4,5}\b|10\.\d{4,9}/\S+", text):
        if num.lower() not in out:
            out.append(num.lower())
    return out[:cap]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _scope_sql(
    params: dict,
    *,
    doc_ids: list[str] | None,
    source_kinds: list[str] | None,
    direction_ids: list[str] | None,
    pg: bool,
) -> str | None:
    """返回额外 AND 条件。doc_ids 给了空列表表示范围为空，调用方应直接没有结果。"""
    if doc_ids is not None and not doc_ids:
        return None
    bits = []
    if doc_ids is not None:
        if pg:
            params["docs"] = list(doc_ids)
            bits.append(" AND c.doc_id = ANY(:docs)")
        else:
            names = []
            for i, did in enumerate(doc_ids):
                params[f"d{i}"] = did
                names.append(f":d{i}")
            bits.append(f" AND c.doc_id IN ({','.join(names)})")
    if source_kinds:
        if pg:
            params["kinds"] = list(source_kinds)
            bits.append(" AND d.source_kind = ANY(:kinds)")
        else:
            names = []
            for i, kind in enumerate(source_kinds):
                params[f"k{i}"] = kind
                names.append(f":k{i}")
            bits.append(f" AND d.source_kind IN ({','.join(names)})")
    if direction_ids:
        if pg:
            params["dirs"] = list(direction_ids)
            bits.append(
                " AND (d.direction_id = ANY(:dirs) OR c.literature_item_id IN "
                "(SELECT item_id FROM literature_direction_links WHERE direction_id = ANY(:dirs)))"
            )
        else:
            names = []
            for i, did in enumerate(direction_ids):
                params[f"dir{i}"] = did
                names.append(f":dir{i}")
            listed = ",".join(names)
            bits.append(
                f" AND (d.direction_id IN ({listed}) OR c.literature_item_id IN "
                f"(SELECT item_id FROM literature_direction_links WHERE direction_id IN ({listed})))"
            )
    return "".join(bits)


def keyword_candidates(
    db: Session,
    lab_id: str,
    terms: list[str],
    *,
    doc_ids: list[str] | None = None,
    source_kinds: list[str] | None = None,
    direction_ids: list[str] | None = None,
    limit: int = 100,
) -> list[tuple[str, str, int | None, float]]:
    """按命中词数给块排序。Postgres 每个词单独走索引并限量，避免几万段正文被一次扫完。"""
    if not terms:
        return []
    pg = is_postgres(db)
    if pg:
        # 两个字的中文在 trigram 里几乎分不清，会把整表核对一遍。长词已经够把那一段找出来。
        terms = [t for t in terms if len(t) >= 3 or not re.search(r"[\u4e00-\u9fff]", t)]
        if not terms:
            return []
        found: dict[str, tuple[str, int | None]] = {}
        counts: dict[str, int] = {}
        each = max(int(limit), 80)
        for term in terms:
            params = {"lab": lab_id, "k": each}
            extra = _scope_sql(
                params, doc_ids=doc_ids, source_kinds=source_kinds, direction_ids=direction_ids, pg=True
            )
            if extra is None:
                return []
            # 词写成字面量，优化器才能看清它冷不冷门。先只按词走索引并限量，再套本组条件。
            # 词和组号写在同一层时，会改走组号索引，把本组正文整表滤一遍。
            literal = _escape_like(term).replace("'", "''")
            sql = (
                "SELECT c.id, c.doc_id, c.page_no FROM ("
                f"SELECT id, doc_id, page_no, lab_id, kind FROM kb_chunks WHERE content ILIKE '%{literal}%' ESCAPE '\\' LIMIT 400"
                ") c JOIN kb_documents d ON d.id = c.doc_id "
                "WHERE c.lab_id = :lab AND d.status = 'indexed' AND d.deleted_at IS NULL "
                f"AND (c.kind IS NULL OR c.kind <> 'reference') {extra} LIMIT :k"
            )
            try:
                with db.begin_nested():
                    # 冷门词如果被估成「很常见」，会改成整表翻正文。关掉顺序扫描，强制走 trigram 索引。
                    try:
                        with db.begin_nested():
                            db.execute(sql_text("SET LOCAL enable_seqscan = off"))
                    except Exception:
                        pass
                    rows = db.execute(sql_text(sql), params).all()
            except Exception:
                continue
            for r in rows:
                cid = str(r[0])
                counts[cid] = counts.get(cid, 0) + 1
                found.setdefault(cid, (str(r[1]), r[2]))
        ranked = sorted(counts, key=lambda c: (-counts[c], c))[: int(limit)]
        return [(c, found[c][0], found[c][1], counts[c] / max(1, len(terms))) for c in ranked]
    op = "LIKE"
    params = {"lab": lab_id, "k": int(limit)}
    extra = _scope_sql(params, doc_ids=doc_ids, source_kinds=source_kinds, direction_ids=direction_ids, pg=False)
    if extra is None:
        return []
    hits_sql = []
    where_sql = []
    for i, term in enumerate(terms):
        key = f"t{i}"
        params[key] = f"%{_escape_like(term)}%"
        hits_sql.append(f"CASE WHEN c.content {op} :{key} ESCAPE '\\' THEN 1 ELSE 0 END")
        where_sql.append(f"c.content {op} :{key} ESCAPE '\\'")
    sql = (
        f"SELECT c.id, c.doc_id, c.page_no, ({' + '.join(hits_sql)}) AS hits "
        "FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id "
        "WHERE c.lab_id = :lab AND d.status = 'indexed' AND d.deleted_at IS NULL "
        f"AND (c.kind IS NULL OR c.kind <> 'reference') AND ({' OR '.join(where_sql)}){extra} "
        "ORDER BY hits DESC LIMIT :k"
    )
    try:
        with db.begin_nested():
            rows = db.execute(sql_text(sql), params).all()
    except Exception:
        return []
    return [(str(r[0]), str(r[1]), r[2], float(r[3] or 0) / max(1, len(terms))) for r in rows]


def vector_candidates(
    db: Session,
    lab_id: str,
    qvec: list[float],
    *,
    doc_ids: list[str] | None = None,
    source_kinds: list[str] | None = None,
    direction_ids: list[str] | None = None,
    limit: int = 100,
) -> list[tuple[str, str, int | None, float]] | None:
    """pgvector 近邻。不是 Postgres 或没有向量列时返回 None，由调用方退回 Python 余弦。"""
    if not is_postgres(db):
        return None
    expr, qtype = _vector_mode(db)
    params: dict[str, object] = {"lab": lab_id, "q": vec_literal(qvec), "k": int(limit)}
    extra = _scope_sql(params, doc_ids=doc_ids, source_kinds=source_kinds, direction_ids=direction_ids, pg=True)
    if extra is None:
        return []
    sql = (
        f"SELECT c.id, c.doc_id, c.page_no, {expr} <=> CAST(:q AS {qtype}) AS dist "
        "FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id "
        "WHERE c.lab_id = :lab AND c.embedding IS NOT NULL AND d.status = 'indexed' AND d.deleted_at IS NULL "
        f"AND (c.kind IS NULL OR c.kind <> 'reference'){extra} "
        "ORDER BY dist LIMIT :k"
    )
    try:
        with db.begin_nested():
            # 多个组共用一张块表：近邻先取再按组过滤，小组会被大组挤空；iterative_scan（pgvector 0.8+）让它接着往下找。
            for stmt in ("SET LOCAL hnsw.ef_search = 120", "SET LOCAL hnsw.iterative_scan = relaxed_order"):
                try:
                    with db.begin_nested():
                        db.execute(sql_text(stmt))
                except Exception:
                    pass
            rows = db.execute(sql_text(sql), params).all()
    except Exception:
        return None
    rows = sorted(rows, key=lambda r: float(r[3] or 0.0))
    return [(str(r[0]), str(r[1]), r[2], 1.0 / (1.0 + float(r[3] or 0.0))) for r in rows]


def rrf_fuse(
    lists: list[tuple[float, list[tuple[str, str, int | None, float]]]],
) -> list[tuple[float, str, str, int | None]]:
    """排名融合：每路按名次给 1/(60+名次)，不看各路原始分数的尺度。"""
    score: dict[str, float] = {}
    meta: dict[str, tuple[str, int | None]] = {}
    for weight, rows in lists:
        for rank, (cid, doc_id, page_no, _raw) in enumerate(rows, start=1):
            score[cid] = score.get(cid, 0.0) + weight / (RRF_K + rank)
            meta.setdefault(cid, (doc_id, page_no))
    fused = [(s, cid, meta[cid][0], meta[cid][1]) for cid, s in score.items()]
    fused.sort(key=lambda row: row[0], reverse=True)
    return fused


def cap_per_doc(rows: list[tuple[float, str, str, int | None]], cap: int = PER_DOC_CAP) -> list:
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        doc_id = row[2]
        if seen.get(doc_id, 0) >= cap:
            continue
        seen[doc_id] = seen.get(doc_id, 0) + 1
        out.append(row)
    return out


def store_vector(db: Session, chunk_id: str, vec: list[float]) -> None:
    if not is_postgres(db):
        return
    try:
        with db.begin_nested():
            db.execute(
                sql_text("UPDATE kb_chunks SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": vec_literal(vec), "id": chunk_id},
            )
    except Exception:
        pass
