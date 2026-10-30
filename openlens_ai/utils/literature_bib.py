"""从检索结果生成 literature_review.bib。禁止臆造条目。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .paper_dedupe import (
    dedupe_paper_dicts,
    enrich_paper_dict,
    extract_arxiv,
    extract_doi,
    is_valid_bib_title,
    norm_title,
    paper_usable_for_bib,
)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)

_CITE_KEY_RE = re.compile(r"[^A-Za-z0-9_]+")


def _as_authors(value) -> str:
    if isinstance(value, list):
        return " and ".join(str(a).strip() for a in value if str(a).strip())
    text = str(value or "").strip()
    if ";" in text:
        return " and ".join(p.strip() for p in text.split(";") if p.strip())
    return text


def _year(row: dict) -> str:
    for key in ("year", "publish_year", "published_date"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        text = str(raw)
        m = re.search(r"(19|20)\d{2}", text)
        if m:
            return m.group(0)
    return ""


def bib_key(row: dict, index: int) -> str:
    authors = _as_authors(row.get("authors"))
    last = authors.split(" and ")[0].split()[-1] if authors else "ref"
    year = _year(row) or "nd"
    title = str(row.get("title") or "paper")
    word = re.sub(r"[^A-Za-z0-9]+", "", title.split()[0] if title.split() else "p") or "p"
    raw = f"{last}{year}{word}{index}"
    return _CITE_KEY_RE.sub("", raw) or f"ref{index}"


def _row_looks_like_paper(row: dict) -> bool:
    if not isinstance(row, dict) or row.get("_search_ledger"):
        return False
    return bool(
        row.get("title")
        or row.get("doi")
        or row.get("arxiv_id")
        or row.get("arxiv")
        or row.get("url")
        or row.get("pdf_url")
        or row.get("pdf_hint")
        or row.get("pubmed_id")
    )


def paper_to_bibtex(row: dict, key: str) -> str:
    title = str(row.get("title") or "").replace("{", "").replace("}", "").strip()
    author = _as_authors(row.get("authors")).replace("{", "").replace("}", "")
    year = _year(row)
    venue = str(row.get("venue") or row.get("journal") or "").replace("{", "").replace("}", "")
    doi = extract_doi(row)
    url = str(row.get("url") or row.get("pdf_url") or row.get("pdf_hint") or "").strip()
    arxiv = extract_arxiv(row)
    fields = [
        f"  title={{{title}}}",
        f"  author={{{author}}}",
        f"  year={{{year}}}",
        f"  journal={{{venue}}}",
    ]
    if doi:
        fields.append(f"  doi={{{doi}}}")
    if url:
        fields.append(f"  url={{{url}}}")
    if arxiv:
        fields.append(f"  eprint={{{arxiv}}}")
    return "@article{" + key + ",\n" + ",\n".join(fields) + "\n}\n"


def render_literature_review_bib(papers: list[dict]) -> str:
    rows = dedupe_paper_dicts([p for p in papers or [] if isinstance(p, dict) and paper_usable_for_bib(p)])
    chunks = []
    for i, row in enumerate(rows, start=1):
        chunks.append(paper_to_bibtex(row, bib_key(row, i)))
    return "\n".join(chunks).strip() + ("\n" if chunks else "")


def write_literature_review_bib(save_path: str | Path, papers: list[dict]) -> Path:
    dest = Path(save_path) / "workspace"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "literature_review.bib"
    path.write_text(render_literature_review_bib(papers), encoding="utf-8")
    return path


def _collect_paper_rows(value) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if _row_looks_like_paper(value):
            found.append(enrich_paper_dict(value))
        for lit in value.get("literature") or []:
            if isinstance(lit, dict) and _row_looks_like_paper(lit):
                found.append(enrich_paper_dict(lit))
        return found
    if isinstance(value, list):
        for item in value:
            found.extend(_collect_paper_rows(item))
    return found


def _parse_json_papers(text: str) -> list[dict]:
    raw = (text or "").strip()
    if not raw or raw[0] not in "[{":
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return _collect_paper_rows(data)


def harvest_papers_from_text(text: str) -> list[dict]:
    """从工具缓冲串、markdown fence、夹杂 JSON 中收割题录。不从标题行臆造。"""
    raw = text or ""
    found: list[dict] = []
    for block in _FENCE_RE.findall(raw):
        found.extend(_parse_json_papers(block.strip()))
    found.extend(_parse_json_papers(raw.strip()))
    decoder = json.JSONDecoder()
    i = 0
    while i < len(raw):
        pos_obj = raw.find("{", i)
        pos_arr = raw.find("[", i)
        candidates = [p for p in (pos_obj, pos_arr) if p != -1]
        if not candidates:
            break
        pos = min(candidates)
        try:
            val, end = decoder.raw_decode(raw, pos)
        except json.JSONDecodeError:
            i = pos + 1
            continue
        found.extend(_collect_paper_rows(val))
        i = end
    for line in raw.splitlines():
        line = line.strip()
        if line[:1] in "[{":
            found.extend(_parse_json_papers(line))
    return dedupe_paper_dicts(found)


def load_search_hits(save_path: str | Path) -> list[dict]:
    path = Path(save_path) / "tool_calls" / "search_hits.jsonl"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return harvest_papers_from_text(text)


def _search_query_key(source: str, query: str) -> str:
    return f"{(source or '').strip().lower()}::{(query or '').strip().lower()}"


def _search_query_log(save_path: str | Path | None = None) -> Path | None:
    from .job_context import current_job_save_path

    root = Path(save_path or current_job_save_path() or "")
    if not str(root):
        return None
    return root / "tool_calls" / "search_queries.jsonl"


def search_query_seen(source: str, query: str, save_path: str | Path | None = None) -> bool:
    q = (query or "").strip()
    dest = _search_query_log(save_path)
    if not q or dest is None or not dest.is_file():
        return False
    key = _search_query_key(source, q)
    try:
        for line in dest.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _search_query_key(str(row.get("source") or ""), str(row.get("query") or "")) == key:
                return True
    except OSError:
        return False
    return False


def remember_search_query(source: str, query: str, save_path: str | Path | None = None) -> None:
    q = (query or "").strip()
    dest = _search_query_log(save_path)
    if not q or dest is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"source": source, "query": q}, ensure_ascii=False) + "\n")


def consume_search_query(source: str, query: str, save_path: str | Path | None = None) -> bool:
    """同一关键词是否已经检索过。只读判断；成功取回后再 remember_search_query。"""
    return search_query_seen(source, query, save_path=save_path)


def persist_search_hits(rows: list[dict], save_path: str | Path | None = None) -> Path | None:
    from .job_context import current_job_save_path

    root = Path(save_path or current_job_save_path() or "")
    if not str(root):
        return None
    incoming = [enrich_paper_dict(r) for r in rows or [] if isinstance(r, dict) and _row_looks_like_paper(r)]
    dest = root / "tool_calls" / "search_hits.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    merged = dedupe_paper_dicts(load_search_hits(root) + incoming)
    if not incoming and not merged:
        return dest if dest.is_file() else None
    dest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged),
        encoding="utf-8",
    )
    return dest


def papers_from_messages(messages) -> list[dict]:
    found: list[dict] = []
    for msg in messages or []:
        content = getattr(msg, "content", msg)
        if isinstance(content, dict) and _row_looks_like_paper(content):
            found.append(enrich_paper_dict(content))
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and _row_looks_like_paper(item):
                    found.append(enrich_paper_dict(item))
                elif isinstance(item, dict) and item.get("text"):
                    found.extend(harvest_papers_from_text(str(item.get("text"))))
                elif isinstance(item, str):
                    found.extend(harvest_papers_from_text(item))
            continue
        if isinstance(content, str):
            found.extend(harvest_papers_from_text(content))
    return dedupe_paper_dicts(found)


def _norm_read_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _hit_read_idents(hit: dict) -> set[str]:
    out: set[str] = set()
    for key in ("doi", "arxiv_id", "arxiv", "pubmed_id", "pmid", "paper_id"):
        token = _norm_read_token(hit.get(key) or "")
        if token:
            out.add(token)
    title = _norm_read_token(hit.get("title") or "")
    if len(title) >= 12:
        out.add(title)
    return out


def collected_read_tokens(save_path: str | Path | None) -> set[str]:
    """workspace/downloads 或 Read 工具落盘后的标识，用来标「已读全文」。"""
    root = Path(save_path or "")
    if not str(root) or not root.exists():
        return set()
    tokens: set[str] = set()
    exts = {".pdf", ".txt", ".md", ".html"}
    for folder in (root / "downloads", root / "workspace" / "downloads"):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                token = _norm_read_token(path.stem)
                if token:
                    tokens.add(token)
    tool_root = root / "tool_calls"
    if tool_root.is_dir():
        for path in tool_root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if "read" not in name or "search" in name:
                continue
            token = _norm_read_token(path.stem)
            if token:
                tokens.add(token)
            if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}:
                continue
            try:
                if path.stat().st_size > 200_000:
                    continue
                blob = path.read_text(encoding="utf-8", errors="ignore")[:8000]
            except OSError:
                continue
            for raw in re.findall(r"10\.\d{4,9}/[^\s\"']+|arxiv[:\s]*[0-9.]+|pmid[:\s]*\d+", blob, re.I):
                ident = _norm_read_token(raw)
                if ident:
                    tokens.add(ident)
    return tokens


def _hit_was_read(hit: dict, tokens: set[str]) -> bool:
    if hit.get("read") or hit.get("already_read") or hit.get("full_text"):
        return True
    for key in ("pdf_path", "local_pdf"):
        raw = str(hit.get(key) or "").strip()
        if raw and Path(raw).is_file():
            return True
    idents = _hit_read_idents(hit)
    return bool(idents and tokens and (idents & tokens))


def format_literature_evidence(hits: list[dict], *, limit: int = 8000, save_path: str | Path | None = None) -> str:
    """给后段看的命中账本：题名、标识、摘要截断、已读/有摘要/未读标记。"""
    if not hits:
        return ""
    tokens = collected_read_tokens(save_path)
    lines = ["# Literature evidence ledger", "", "Do not invent bibliography entries beyond this list.", ""]
    for i, hit in enumerate(hits[:40], start=1):
        if not isinstance(hit, dict) or hit.get("_search_ledger"):
            continue
        title = str(hit.get("title") or "untitled").strip()
        ident = str(
            hit.get("doi") or hit.get("arxiv_id") or hit.get("arxiv") or hit.get("pubmed_id") or hit.get("pmid") or ""
        ).strip()
        abstract = str(hit.get("abstract") or hit.get("summary") or "").strip()
        if _hit_was_read(hit, tokens):
            mark = "已读全文"
        elif len(abstract) >= 80:
            mark = "有摘要"
        else:
            mark = "未读全文"
        lines.append(f"{i}. {title}")
        if ident:
            lines.append(f"   id: {ident}")
        lines.append(f"   {mark}: {abstract[:280]}")
        lines.append("")
    return "\n".join(lines)[:limit]


def write_literature_evidence(save_path: str | Path | None = None) -> Path | None:
    from .job_context import current_job_save_path

    root = Path(save_path or current_job_save_path() or "")
    if not str(root):
        return None
    text = format_literature_evidence(load_search_hits(root), save_path=root)
    dest = root / "workspace" / "literature_evidence.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text or "（尚无检索账本，不得编造书目）\n", encoding="utf-8")
    return dest


def papers_from_tool_calls_dir(save_path: str | Path) -> list[dict]:
    root = Path(save_path) / "tool_calls"
    if not root.is_dir():
        return []
    found: list[dict] = []
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found.extend(harvest_papers_from_text(text))
    return dedupe_paper_dicts(found)


def write_literature_review_bib_from_state(save_path: str | Path, state: dict | None = None) -> Path:
    papers = papers_from_messages((state or {}).get("messages"))
    papers.extend(papers_from_tool_calls_dir(save_path))
    papers = [p for p in papers if isinstance(p, dict) and _row_looks_like_paper(p)]
    return write_literature_review_bib(save_path, papers)


def bib_titles(text: str) -> list[str]:
    titles = []
    for m in re.finditer(r"title\s*=\s*\{(.+?)\}", text or "", re.S | re.I):
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        if t:
            titles.append(t)
    return titles


def valid_bib_titles(text: str) -> list[str]:
    return [t for t in bib_titles(text) if is_valid_bib_title(t)]


def title_mentioned_in(blob: str, title: str) -> bool:
    text = blob or ""
    t = str(title or "").strip()
    if not t:
        return False
    if t in text or f"未使用：{t}" in text or f"未使用: {t}" in text:
        return True
    if len(t) >= 8 and t[:24] in text:
        return True
    normalized = norm_title(t)
    return bool(normalized and len(normalized) >= 8 and normalized in norm_title(text))


def unused_bib_titles(blob: str, titles: list[str]) -> list[str]:
    return [t for t in titles if not title_mentioned_in(blob, t)]


def load_literature_review_md(save_path: str | Path) -> str:
    path = Path(save_path) / "workspace" / "literature_review.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def finalize_literature_review(save_path: str | Path, state: dict | None = None) -> tuple[Path, str]:
    """写出工具结果书目，并用 utf-8 读回综述。不臆造 bib 条目。"""
    bib_path = write_literature_review_bib_from_state(save_path, state)
    return bib_path, load_literature_review_md(save_path)
