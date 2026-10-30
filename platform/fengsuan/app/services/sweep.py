"""全面检索：规划、检索、顺着引用、初筛。中间结果放文件，不建表。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

_CS = ("学习", "网络", "算法", "模型", "语言", "视觉", "graph", "model", "learning")
_BIO = ("药物", "基因", "临床", "蛋白", "癌", "细胞", "drug", "gene", "clinic")
_MAT = ("材料", "催化", "电池", "晶体", "polymer", "catalyst")
_SOC = ("管理", "经济", "政策", "社会", "治理")


def _dir(lab_id: str) -> Path:
    from fengsuan.app.core.config import get_settings

    dest = Path(get_settings().file_root) / "cache" / "sweeps" / (lab_id or "none")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def load_sweep(lab_id: str, sweep_id: str) -> dict | None:
    path = _dir(lab_id) / f"{sweep_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_sweep(lab_id: str, data: dict) -> None:
    sid = str(data.get("id") or "")
    if not sid:
        return
    (_dir(lab_id) / f"{sid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def discipline_of(topic: str) -> str:
    text = (topic or "").lower()
    if any(tok in text for tok in _BIO):
        return "bio"
    if any(tok in text for tok in _MAT):
        return "materials"
    if any(tok in text for tok in _SOC):
        return "social"
    if any(tok in text for tok in _CS):
        return "cs"
    return "general"


def sources_for(discipline: str) -> list[str]:
    base = ["lab", "native", "scholar", "cnki", "wanfang", "openalex"]
    extra = {
        "cs": ["semantic", "arxiv", "dblp", "openreview"],
        "bio": ["pubmed", "europepmc"],
        "materials": ["crossref", "arxiv"],
        "social": ["crossref", "ncpssd"],
        "general": ["crossref"],
    }
    out = []
    for name in base + extra.get(discipline, []):
        if name not in out:
            out.append(name)
    return out


def plan_queries(topic: str, *, years: int = 5, must: list[str] | None = None, exclude: list[str] | None = None, terms: list[str] | None = None) -> list[str]:
    topic = (topic or "").strip()
    bits = [topic]
    for term in (terms or [])[:4]:
        if term and term not in bits:
            bits.append(term)
    for word in must or []:
        if word and word not in topic:
            bits.append(f"{topic} {word}".strip())
    year_bit = f"{topic} {years} years" if years else topic
    if year_bit not in bits:
        bits.append(year_bit)
    if any("\u4e00" <= ch <= "\u9fff" for ch in topic):
        bits.append(topic)
    out = []
    for item in bits:
        item = " ".join(item.split())[:180]
        if item and item not in out and not any(x and x in item for x in (exclude or [])):
            out.append(item)
    return out[:8]


def sweep_plan(lab_id: str, topic: str, *, years: int = 5, must: list[str] | None = None, exclude: list[str] | None = None, terms: list[str] | None = None) -> dict:
    from fengsuan.app.db.base import utcnow

    disc = discipline_of(topic)
    data = {
        "id": uuid.uuid4().hex,
        "lab_id": lab_id,
        "topic": (topic or "")[:180],
        "years": int(years or 5),
        "discipline": disc,
        "sources": sources_for(disc),
        "queries": plan_queries(topic, years=years, must=must, exclude=exclude, terms=terms),
        "exclude": exclude or [],
        "hits": [],
        "screened": [],
        "themes": [],
        "missing": [],
        "counts": {},
        "notes": [],
        "updated_at": utcnow().isoformat(),
    }
    save_sweep(lab_id, data)
    return data


def _key(row: dict) -> str:
    doi = str(row.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    arxiv = str(row.get("arxiv_id") or "").strip().lower()
    if arxiv:
        return "arxiv:" + arxiv
    title = "".join(ch for ch in str(row.get("title") or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return "title:" + title[:80]


def _add(bucket: list[dict], seen: set[str], rows: list[dict], source: str) -> int:
    n = 0
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        item = dict(row)
        item["source"] = item.get("source") or source
        key = _key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        bucket.append(item)
        n += 1
    return n


def sweep_search(lab_id: str, sweep_id: str, *, lab_hits: list[dict] | None = None, llm=None) -> dict:
    data = load_sweep(lab_id, sweep_id)
    if data is None:
        return {"error": "没有这次检索"}
    from fengsuan.app.services.web import cn, scholarly, search
    from fengsuan.app.services.web.scholar import scholar_search

    hits: list[dict] = []
    seen: set[str] = set()
    counts = {"lab": _add(hits, seen, lab_hits or [], "lab")}
    notes = list(data.get("notes") or [])
    query = (data.get("queries") or [data.get("topic")])[0]
    years = int(data.get("years") or 5)

    def _one(source: str) -> tuple[str, list[dict], str]:
        rows: list[dict] = []
        note = ""
        if source == "native":
            pack = search.search_web(query, llm=llm, recent=True)
            rows = pack.get("hits") or []
            note = pack.get("note") or ""
        elif source == "scholar":
            pack = scholar_search(query, session=sweep_id)
            rows = pack.get("papers") or []
            note = pack.get("note") or ""
        elif source in {"cnki", "wanfang", "ncpssd"}:
            pack = cn.search_cn(query, sources=[source], llm=None)
            rows = pack.get("hits") or []
            note = pack.get("note") or ""
        elif source == "openalex":
            rows = scholarly.openalex_search_works(query, years=years, per_page=20)
        elif source == "arxiv":
            rows = scholarly.openalex_search_works(query + " arxiv", years=years, per_page=8)
        elif source == "dblp":
            rows = scholarly.dblp_pubs(query, limit=8)
        elif source == "openreview":
            rows = scholarly.openreview_search(query, limit=5)
        elif source == "pubmed":
            rows = scholarly.europepmc_search(query + " PUBMED", limit=8)
        elif source == "europepmc":
            rows = scholarly.europepmc_search(query, limit=8)
        elif source == "crossref":
            rows = scholarly.crossref_search(query, limit=8)
        return source, rows, note

    from concurrent.futures import ThreadPoolExecutor, wait

    sources = [s for s in (data.get("sources") or []) if s != "lab"]
    pool = ThreadPoolExecutor(max_workers=6)
    futures = [pool.submit(_one, source) for source in sources]
    done, pending = wait(futures, timeout=16)
    if pending:
        notes.append("有的来源这次没赶上，先用已经回来的。")
    pool.shutdown(wait=False, cancel_futures=True)
    for fut in done:
        try:
            source, rows, note = fut.result()
        except Exception as exc:
            notes.append(f"有个来源没连上（{type(exc).__name__}）")
            continue
        if note:
            notes.append(note)
        counts[source] = _add(hits, seen, rows, source)
    from openlens_ai.utils.paper_dedupe import merge_versions

    merged_hits = merge_versions(hits)
    data["hits"] = merged_hits[:80]
    # D-HONEST：found 记版本合并前的条数，deduped 是预印本和正式版并完之后。
    data["counts"] = {
        "found": len(hits),
        "stored": min(len(merged_hits), 80),
        "by_source": counts,
        "deduped": len(merged_hits),
        "version_merged": max(0, len(hits) - len(merged_hits)),
    }
    if len(hits) > len(merged_hits):
        notes.append(f"预印本和正式版并掉了 {len(hits) - len(merged_hits)} 条。")
    data["notes"] = notes[:12]
    save_sweep(lab_id, data)
    return data


def sweep_expand(lab_id: str, sweep_id: str) -> dict:
    data = load_sweep(lab_id, sweep_id)
    if data is None:
        return {"error": "没有这次检索"}
    from fengsuan.app.services.web import scholarly

    hits = list(data.get("hits") or [])
    seen = {_key(row) for row in hits}
    added = 0
    seeds = [row for row in hits if row.get("openalex_id")][:6]

    def _cite(seed_id: str) -> list[dict]:
        try:
            return scholarly.openalex_citing(seed_id, limit=5)
        except Exception:
            return []

    def _refs(seed_id: str) -> list[dict]:
        try:
            return scholarly.openalex_referenced(seed_id, limit=5)
        except Exception:
            return []

    from concurrent.futures import ThreadPoolExecutor, wait

    pool = ThreadPoolExecutor(max_workers=4)
    cite_futs = [pool.submit(_cite, seed.get("openalex_id") or "") for seed in seeds]
    ref_futs = [pool.submit(_refs, seed.get("openalex_id") or "") for seed in seeds]
    done, _pending = wait(cite_futs + ref_futs, timeout=12)
    pool.shutdown(wait=False, cancel_futures=True)
    for fut in cite_futs:
        if fut in done:
            added += _add(hits, seen, fut.result(), "citation")
    ref_counts: dict[str, int] = {}
    ref_rows: dict[str, dict] = {}
    for fut in ref_futs:
        if fut not in done:
            continue
        for row in fut.result() or []:
            if not isinstance(row, dict):
                continue
            key = _key(row)
            if not key:
                continue
            ref_counts[key] = ref_counts.get(key, 0) + 1
            ref_rows.setdefault(key, row)
    ranked_refs = sorted(ref_rows, key=lambda key: ref_counts.get(key, 0), reverse=True)
    foundational = 0
    for key in ranked_refs:
        row = dict(ref_rows[key])
        n = ref_counts.get(key, 0)
        if n >= 2:
            row["foundational"] = True
            row["cited_by_seeds"] = n
            foundational += 1
        added += _add(hits, seen, [row], "reference")
    data["hits"] = hits[:100]
    counts = data.get("counts") or {}
    counts["expanded"] = added
    counts["expand_hops"] = 1
    counts["expand_seeds"] = len(seeds)
    counts["expand_new"] = added
    counts["foundational"] = foundational
    data["counts"] = counts
    data["expand_note"] = f"沿引用走了 1 层，种子 {len(seeds)} 篇，去重后新增 {added} 篇，其中 {foundational} 篇被多颗种子共同引用。"
    save_sweep(lab_id, data)
    return data


def _topic_tokens(topic: str) -> list[str]:
    """中文不按空格切。长词改用双字片段，避免没有拉丁词就整批丢掉。"""
    text = (topic or "").lower().replace("，", " ").replace("、", " ").replace(",", " ")
    out: list[str] = []
    for part in [t for t in text.split() if len(t) >= 2]:
        cjk = "".join(ch for ch in part if "\u4e00" <= ch <= "\u9fff")
        latin = "".join(ch for ch in part if ch.isascii() and ch.isalnum())
        if latin and len(latin) >= 2 and latin not in out:
            out.append(latin)
        if len(cjk) <= 4 and len(cjk) >= 2 and cjk not in out:
            out.append(cjk)
        for i in range(0, max(0, len(cjk) - 1)):
            gram = cjk[i : i + 2]
            if len(gram) == 2 and gram not in out:
                out.append(gram)
    return out[:24]


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "abstract", "snippet", "venue")).lower()


def _overlap(topic: str, row: dict) -> int:
    text = _row_text(row)
    score = 0
    hits = 0
    for tok in _topic_tokens(topic):
        if tok and tok in text:
            score += 2 if len(tok) >= 3 else 1
            hits += 1
    if hits == 0:
        return 0
    score += min(5, int(row.get("citation_count") or 0) // 20)
    return score


def _themes(rows: list[dict], topic: str = "") -> list[dict]:
    tokens = _topic_tokens(topic)
    groups: dict[str, list[str]] = {}
    for row in rows:
        title = str(row.get("title") or "")
        low = title.lower()
        token = next((tok for tok in tokens if tok in low), "")
        if not token:
            cjk = "".join(ch for ch in title if "\u4e00" <= ch <= "\u9fff")
            token = cjk[:4] if len(cjk) >= 4 else ""
        if not token:
            for word in title.replace("：", " ").split():
                if len(word) >= 2:
                    token = word[:12]
                    break
        if not token:
            continue
        groups.setdefault(token, [])
        if title not in groups[token]:
            groups[token].append(title)
    themes = [{"name": name, "papers": papers[:4]} for name, papers in groups.items() if len(papers) >= 2]
    return themes[:6]


def sweep_screen(lab_id: str, sweep_id: str, *, keep: int = 12) -> dict:
    data = load_sweep(lab_id, sweep_id)
    if data is None:
        return {"error": "没有这次检索"}
    topic = data.get("topic") or ""
    queries = [str(q) for q in (data.get("queries") or []) if str(q).strip() and str(q).strip() != topic]

    def _score(row: dict) -> int:
        best = _overlap(topic, row)
        for query in queries:
            best = max(best, _overlap(query, row))
        return best

    ranked = sorted(data.get("hits") or [], key=_score, reverse=True)
    kept = []
    for row in ranked:
        if _score(row) <= 0 and (row.get("source") not in {"lab", "scholar"}):
            continue
        item = dict(row)
        item["why"] = _why(topic, item)
        kept.append(item)
        if len(kept) >= max(1, min(20, keep)):
            break
    if not kept:
        fallback = sorted(ranked, key=lambda row: int(row.get("citation_count") or 0), reverse=True)
        kept = [dict(row, why="和检索词对得不紧，先按被引留下。") for row in fallback[: min(5, keep)]]
    titles = {row.get("title") for row in kept}
    missing = []
    for row in ranked:
        if row.get("title") in titles:
            continue
        if int(row.get("citation_count") or 0) >= 50:
            missing.append({"title": row.get("title"), "why": "被引较多，但这次没放进要读的那一批。"})
        if len(missing) >= 5:
            break
    data["screened"] = kept
    data["themes"] = _themes(kept, topic)
    data["missing"] = missing
    data["brief"] = build_sweep_brief(data)
    counts = data.get("counts") or {}
    counts["screened"] = len(kept)
    data["counts"] = counts
    save_sweep(lab_id, data)
    return data


def build_sweep_brief(data: dict) -> dict:
    """用已经查到的命中整理一份简报，不另开检索。"""
    hits = [row for row in (data.get("hits") or []) if isinstance(row, dict)]
    themes = [row for row in (data.get("themes") or []) if isinstance(row, dict)]
    lines = []
    for theme in themes[:4]:
        papers = [str(title) for title in (theme.get("papers") or []) if str(title).strip()][:3]
        if papers:
            lines.append({"name": str(theme.get("name") or "分支")[:40], "papers": papers})
    if not lines and hits:
        lines.append({"name": str(data.get("topic") or "主线")[:40], "papers": [str(row.get("title") or "") for row in hits[:3]]})
    by_year: dict[str, list[str]] = {}
    for row in hits:
        year = str(row.get("year") or "")[:4]
        title = str(row.get("title") or "").strip()
        if year.isdigit() and title:
            by_year.setdefault(year, [])
            if title not in by_year[year] and len(by_year[year]) < 3:
                by_year[year].append(title)
    questions = []
    for theme in themes[:4]:
        papers = [str(title) for title in (theme.get("papers") or []) if str(title).strip()]
        if not papers:
            continue
        questions.append({"text": f"「{theme.get('name') or '这一支'}」还有没有更新的对照", "source": papers[0]})
    if not questions and hits:
        questions.append({"text": "这些代表作之间还缺哪一块对照", "source": str(hits[0].get("title") or "")})
    counts = data.get("counts") or {}
    notes = [str(note) for note in (data.get("notes") or [])]
    failed = [note for note in notes if "没连上" in note or "没赶上" in note]
    return {
        "lines": lines,
        "timeline": [{"year": year, "papers": titles} for year, titles in sorted(by_year.items())][-8:],
        "open_questions": questions[:4],
        "in_lab": [{"title": str(row.get("title") or "")} for row in hits if row.get("source") == "lab"][:8],
        "coverage": {
            "sources": [name for name, n in (counts.get("by_source") or {}).items() if n],
            "failed": failed[:4],
            "found": counts.get("found"),
            "stored": counts.get("stored"),
            "gap": failed[0] if failed else "",
        },
    }


def _why(topic: str, row: dict) -> str:
    bits = []
    matched = [tok for tok in _topic_tokens(topic) if tok in _row_text(row)]
    if matched:
        bits.append("对上「" + "、".join(matched[:3]) + "」")
    if int(row.get("citation_count") or 0):
        bits.append(f"被引 {int(row['citation_count'])}（{row.get('source') or '来源未写'}）")
    if row.get("year"):
        bits.append(f"{row['year']} 年")
    return "，".join(bits) or "和这次的检索式相关。"


def revise_sweep(lab_id: str, sweep_id: str, *, add: str = "", drop: str = "", years: int | None = None) -> dict:
    data = load_sweep(lab_id, sweep_id)
    if data is None:
        return {"error": "没有这次检索"}
    if years:
        data["years"] = int(years)
    queries = list(data.get("queries") or [])
    if add and add not in queries:
        queries.append(add[:180])
    if drop:
        queries = [q for q in queries if drop not in q]
    data["queries"] = queries[:8]
    data["hits"] = []
    data["screened"] = []
    save_sweep(lab_id, data)
    return data
