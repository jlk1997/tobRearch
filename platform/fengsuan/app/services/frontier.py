"""每个方向向外看：库外前沿论文、相关作者、相邻方向、推荐读。七天刷新一次。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.services.web.common import SourceFailures


def _loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _age_ok(raw: dict, days: int = 7) -> bool:
    stamp = str(raw.get("updated_at") or "")
    if not stamp or not raw.get("papers"):
        return False
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return utcnow() - then < timedelta(days=days)


def _queries(direction, notes: list[str]) -> list[str]:
    try:
        keywords = json.loads(direction.keywords_json or "[]")
    except json.JSONDecodeError:
        keywords = []
    if not isinstance(keywords, list):
        keywords = []
    bits = [direction.name or ""]
    bits.extend(str(k) for k in keywords[:4] if str(k).strip())
    bits.extend(notes[:2])
    q = " ".join(x.strip() for x in bits if x and x.strip())
    q = q[:180]
    extra = " ".join(str(k) for k in keywords[:3] if str(k).strip())
    out = []
    for item in (q, extra):
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out[:3]


def _themes(papers: list[dict]) -> list[dict]:
    """把题目里一起出现的说法归成主题，每个主题都指回具体的篇。"""
    import re
    from collections import Counter

    stop = {"the", "and", "for", "with", "from", "using", "via", "based", "towards", "this", "that", "into", "over"}
    phrases: Counter = Counter()
    owners: dict[str, list[int]] = {}
    shown: dict[str, str] = {}
    for i, paper in enumerate(papers):
        words = re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,4}", paper.get("title") or "")
        words = [w for w in words if w.lower() not in stop]
        seen: set[str] = set()
        pairs = list(zip(words, words[1:])) or [(w, "") for w in words[:3]]
        for a, b in pairs:
            phrase = f"{a} {b}".strip() if b and a.isascii() else (a + b if b else a)
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            phrases[key] += 1
            shown.setdefault(key, phrase)
            owners.setdefault(key, [])
            if i not in owners[key]:
                owners[key].append(i)
    themes = []
    used: set[int] = set()
    for key, n in phrases.most_common(20):
        if n < 2:
            continue
        idxs = [i for i in owners.get(key) or [] if i not in used][:4]
        if len(idxs) < 2:
            continue
        used.update(idxs)
        themes.append({"name": shown.get(key) or key, "papers": [papers[i].get("title") for i in idxs]})
        if len(themes) >= 5:
            break
    return themes


def _authors(papers: list[dict]) -> list[dict]:
    found: dict[str, dict] = {}
    for paper in papers:
        names = paper.get("authors") or []
        if isinstance(names, str):
            names = [names]
        inst = ""
        extra = paper.get("extra") if isinstance(paper.get("extra"), dict) else {}
        inst = str(extra.get("institution") or paper.get("institution") or "")
        for name in names[:6]:
            name = str(name or "").strip()
            if len(name) < 2:
                continue
            row = found.setdefault(name, {"name": name, "count": 0, "institution": inst, "papers": []})
            row["count"] += 1
            if inst and not row["institution"]:
                row["institution"] = inst
            if paper.get("title") and paper["title"] not in row["papers"]:
                row["papers"].append(paper["title"])
    rows = sorted(found.values(), key=lambda r: r["count"], reverse=True)
    total = len(papers)
    out = []
    for row in rows[:8]:
        out.append(
            {
                "name": row["name"],
                "count": row["count"],
                "of": total,
                "institution": row["institution"],
                "papers": row["papers"][:2],
                "said": f"在最近搜到的 {total} 篇里出现了 {row['count']} 次",
            }
        )
    return out


def refresh_direction_frontier(db: Session, lab_id: str, direction, *, force: bool = False) -> dict[str, Any]:
    current = _loads(getattr(direction, "frontier_json", "") or "")
    if not force and _age_ok(current):
        return current
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.literature import cached_search_sources

    notes = []
    brief = _loads(getattr(direction, "brief_json", "") or "")
    for paper in brief.get("papers") or []:
        if paper.get("title"):
            notes.append(str(paper["title"])[:80])
    queries = _queries(direction, notes)
    sources = ["arxiv", "semantic", "pubmed", "medrxiv", "biorxiv", "openalex"]
    raw: list[dict] = []
    cached = False
    try:
        for q in queries:
            hits, was_cached = cached_search_sources(q, sources, 4, extra=direction.id)
            cached = cached or was_cached
            raw.extend(hits or [])
    except Exception:
        if current.get("papers"):
            current["note"] = "这次没连上外面，下面仍是上次的结果。"
            return current
        raw = []
    lab_rows = db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)).all()
    have = set()
    for row in lab_rows:
        if row.doi:
            have.add(row.doi.strip().lower())
        if row.title:
            have.add(row.title.strip().lower())
        if row.arxiv_id:
            have.add(row.arxiv_id.strip().lower())
    year_cut = utcnow().year - 2
    papers = []
    seen = set()
    for hit in raw:
        title = str(hit.get("title") or "").strip()
        key = title.lower()
        doi = str(hit.get("doi") or "").strip().lower()
        if not title or key in seen or key in have or (doi and doi in have):
            continue
        year = hit.get("year") or 0
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = 0
        if year and year < year_cut:
            continue
        seen.add(key)
        papers.append(
            {
                "title": title,
                "authors": hit.get("authors") or [],
                "year": year or None,
                "doi": hit.get("doi") or "",
                "arxiv_id": hit.get("arxiv_id") or "",
                "url": hit.get("url") or "",
                "abstract": str(hit.get("abstract") or "")[:400],
                "citation_count": int(hit.get("citation_count") or 0),
                "source": hit.get("source") or "",
                "venue": hit.get("venue") or "",
                "institution": (hit.get("extra") or {}).get("institution") if isinstance(hit.get("extra"), dict) else "",
                "outside": True,
            }
        )
    papers.sort(key=lambda p: (p.get("citation_count") or 0, p.get("year") or 0), reverse=True)
    papers = papers[:12]
    lab_title = next((n for n in notes if n), "")
    reading = []
    for paper in papers[:5]:
        why = "近两年外面搜到的，引用相对多。"
        if lab_title:
            why = f"和组里《{lab_title}》题目相近，可以接着读。"
        reading.append({"title": paper["title"], "doi": paper.get("doi") or "", "why": why, "outside": True})
    out = {
        "papers": papers,
        "authors": _authors(papers),
        "themes": _themes(papers),
        "reading": reading,
        "queries": queries,
        "cached": cached,
        "updated_at": utcnow().isoformat(),
        "note": "" if papers else "这次外面没有搜到新论文。",
        "outside": True,
    }
    from fengsuan.app.services.reading_route import outlet_text, venue_outlets

    for paper in papers:
        if not paper.get("shelf"):
            paper["shelf"] = _shelf_of(paper, utcnow().year)
    outlets = venue_outlets(papers)
    out["outlets"] = outlets
    out["outlet_text"] = outlet_text(outlets)
    old = _loads(direction.frontier_json)
    if isinstance(old.get("route"), list):
        out["route"] = old["route"]
    direction.frontier_json = json.dumps(out, ensure_ascii=False)
    return out


def topic_terms(topic: str) -> list[str]:
    """内置词表。理解层已经给出英文词时，调用方应直接用那个，不必再进这里。"""
    from fengsuan.app.services.terms import derive_terms

    return derive_terms(topic)


_JUNK_HOST = ("patent", "genius.", "deepwiki.", "scholar.google", "zhihu.", "csdn.")
# C218：书籍章节、百科词条、学位论文、出版社目录页不是同行评审论文，不与正式论文同等呈现。
_NON_PAPER_TITLE = re.compile(
    r"(handbook|encyclopedia|encyclopaedia|compendium|dictionary of|glossary of|"
    r"bibliography|proceedings of the .* symposium on|"
    r"博士论文|硕士学位论文|学位论文|综述述评|手册|百科全书|词典|年鉴)",
    re.I,
)
_NON_PAPER_VENUE = re.compile(
    r"(springerlink|books?\.|encyclopedia|wiley encyclopedia|"
    r"计算机科学百科|百科全书)",
    re.I,
)


def _junk_paper(paper: dict, *, code: bool) -> bool:
    blob = f"{paper.get('url') or ''} {paper.get('title') or ''}".lower()
    if any(tok in blob for tok in _JUNK_HOST):
        return True
    if "github." in blob and not code:
        return True
    title = str(paper.get("title") or "")
    venue = str(paper.get("venue") or "")
    if title and _NON_PAPER_TITLE.search(title):
        return True
    if venue and _NON_PAPER_VENUE.search(venue) and not paper.get("doi"):
        return True
    return False


def _relevant(paper: dict, queries: list[str]) -> bool:
    from fengsuan.app.services.sweep import _topic_tokens

    blob = f"{paper.get('title') or ''} {paper.get('abstract') or ''}".lower()
    for query in queries:
        tokens = _topic_tokens(query)
        if not tokens:
            continue
        latin = [tok for tok in tokens if tok.isascii()]
        hits = [tok for tok in tokens if tok in blob]
        cjk_hits = [tok for tok in hits if not tok.isascii()]
        if len(latin) >= 2:
            if len([tok for tok in latin if tok in blob]) >= 2:
                return True
            if len(cjk_hits) >= 2:
                return True
            continue
        query_cjk = "".join(ch for ch in query if "\u4e00" <= ch <= "\u9fff")
        if len(query_cjk) > 4:
            if len(cjk_hits) >= 2:
                return True
            continue
        if hits:
            return True
    return False


def _shelf_of(paper: dict, year_now: int) -> str:
    title = str(paper.get("title") or "")
    if re.search(r"综述|survey|review", title, re.I):
        if re.search(r"[\u4e00-\u9fff]", title):
            return "中文综述"
        return "入门综述"
    year = 0
    try:
        year = int(str(paper.get("year") or 0)[:4])
    except ValueError:
        year = 0
    if year and year >= year_now - 2:
        return "近两年代表"
    return "奠基经典"


def frontier_for_topic(
    topic: str,
    *,
    years: int = 2,
    queries: list[str] | None = None,
    plan: dict | None = None,
    focus: str = "",
) -> dict:
    """经典、近期、综述、中文一起查。不写进方向缓存。"""
    import re
    from concurrent.futures import ThreadPoolExecutor, wait

    from fengsuan.app.services.sweep import discipline_of
    from fengsuan.app.services.web import scholarly

    q = (topic or "").strip()
    terms = [q] if q else []
    for extra in queries or topic_terms(q):
        text = str(extra or "").strip()
        if text and text not in terms:
            terms.append(text)
    en = next((item for item in terms if item and not re.search(r"[\u4e00-\u9fff]", item)), "")
    cn = next((item for item in terms if re.search(r"[\u4e00-\u9fff]", item)), q)
    plan = plan if isinstance(plan, dict) else {}

    def _planned(key: str, fallback: str) -> str:
        raw = plan.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        for bit in raw:
            text = str(bit or "").strip()
            if text:
                return text[:180]
        return fallback

    for key in ("openalex", "scholar", "cnki", "web", "site"):
        raw = plan.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        for bit in raw:
            text = str(bit or "").strip()
            if text and text not in terms:
                terms.append(text)
    oa_q = _planned("openalex", en or cn)
    sch_q = _planned("scholar", en or cn)
    cn_q = _planned("cnki", f"{cn} 综述" if cn else q)
    code = discipline_of(q) == "cs" or any(tok in q for tok in ("代码", "复现"))

    def _safe(label, fn):
        try:
            return fn() or []
        except Exception as exc:
            # D-HONEST：源挂了要写明，不能让整轮失败看起来像「外面没有新论文」。
            holder = SourceFailures.current()
            if holder is not None:
                holder.note(label, type(exc).__name__)
            return []

    focus = str(focus or "")
    classic_n, recent_n, review_n = 12, 20, 8
    if focus == "classic":
        classic_n, recent_n, review_n = 20, 8, 8
    elif focus == "reviews":
        classic_n, recent_n, review_n = 8, 8, 20

    def _classic():
        return scholarly.openalex_search_works(oa_q, years=None, per_page=classic_n)

    def _recent():
        return scholarly.openalex_search_works(oa_q, years=max(years, 2), per_page=recent_n)

    def _review():
        review_q = oa_q if "survey" in oa_q.lower() or "综述" in oa_q else f"{oa_q} survey"
        return scholarly.openalex_search_works(review_q, years=None, per_page=review_n)

    def _cn():
        from fengsuan.app.services.web.cn import paper_from_hit, search_cn

        pack = search_cn(cn_q, llm=None)
        rows = []
        for hit in pack.get("hits") or []:
            rows.append(paper_from_hit(hit))
        return rows

    def _scholar():
        from fengsuan.app.db.base import utcnow
        from fengsuan.app.services.web.scholar import scholar_search

        pack = scholar_search(sch_q, ylo=utcnow().year - max(years, 3))
        return pack.get("papers") or []

    def _crossref():
        return scholarly.crossref_search(en or cn, limit=5)

    def _discipline():
        rows = []
        if disc == "cs":
            rows.extend(scholarly.dblp_pubs(en or cn, limit=5) or [])
            rows.extend(scholarly.openreview_search(en or cn, limit=4) or [])
        elif disc == "bio":
            rows.extend(scholarly.europepmc_search(en or cn, limit=5) or [])
        return rows

    disc = discipline_of(" ".join(terms) or q)
    pool = ThreadPoolExecutor(max_workers=6)
    jobs = [
        ("OpenAlex 经典", _classic),
        ("OpenAlex 近两年", _recent),
        ("OpenAlex 综述", _review),
        ("中文库", _cn),
        ("谷歌学术", _scholar),
        ("Crossref", _crossref),
        ("学科库", _discipline),
    ]
    futures = [pool.submit(_safe, label, fn) for label, fn in jobs]
    wait(futures, timeout=8)
    pool.shutdown(wait=False, cancel_futures=True)
    holder = SourceFailures.current()
    if holder is not None:
        for (label, _fn), fut in zip(jobs, futures):
            if not fut.done():
                holder.note(label, "超时")
    found = []
    for fut in futures:
        try:
            found.extend(fut.result() or [])
        except Exception:
            continue
    kept = []
    seen = set()
    search_total = 0
    for paper in found:
        if not isinstance(paper, dict):
            continue
        try:
            search_total = max(search_total, int(paper.pop("_search_total", 0) or 0))
        except (TypeError, ValueError):
            paper.pop("_search_total", None)
        if not paper.get("title") or _junk_paper(paper, code=code):
            continue
        if terms and not _relevant(paper, terms):
            continue
        key = str(paper.get("doi") or "").lower() or re.sub(r"\s+", "", str(paper.get("title") or "").lower())
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(paper)
    from openlens_ai.utils.paper_dedupe import merge_versions

    kept = merge_versions(kept)
    from fengsuan.app.db.base import utcnow

    year_now = utcnow().year
    for paper in kept:
        if not paper.get("shelf"):
            paper["shelf"] = _shelf_of(paper, year_now)
        if not paper.get("why"):
            abstract = str(paper.get("abstract") or "")
            if abstract:
                paper["why"] = abstract[:40]
    order = {"入门综述": 0, "奠基经典": 1, "近两年代表": 2, "中文综述": 3}
    kept.sort(key=lambda paper: (order.get(paper.get("shelf") or "", 9), -(int(paper.get("citation_count") or 0))))
    pool_n = len(kept)
    papers = kept[:40]
    failed = holder.as_dict() if holder is not None else {}
    # D-HONEST：报数用合并去重后的实际命中，不是 OpenAlex 过滤前的 meta.count。
    merged = pool_n
    seen_sources = sorted({str(p.get("source") or "") for p in kept if p.get("source")})
    src_bits = f"，来自 {'、'.join(seen_sources[:4])}" if seen_sources else ""
    if failed:
        names = "、".join(list(failed)[:3])
        more = "等" if len(failed) > 3 else ""
        fail_bit = f"这次 {names}{more} 没连上，下面只覆盖其余源。"
    else:
        fail_bit = ""
    if not papers:
        note = fail_bit or "这次外面没有搜到新论文。"
    elif pool_n > 40:
        note = f"合并去重后对得上 {merged} 篇{src_bits}。下面只看了前 {len(papers)} 条里的短名单。"
    else:
        note = f"合并去重后对得上 {merged} 篇{src_bits}，下面是短名单。"
    if fail_bit:
        note = f"{fail_bit} {note}"
    return {
        "papers": papers,
        "authors": _authors(papers),
        "signals": [],
        "topic": q,
        "queries": terms,
        "outside": True,
        "note": note,
        "merged_total": merged,
        "sources_seen": seen_sources,
        "failed_sources": failed,
    }


def refresh_one_frontier(db: Session) -> str:
    from fengsuan.app.models.lab import ResearchDirection

    rows = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.deleted_at.is_(None), ResearchDirection.parent_id.is_(None))
        .order_by(ResearchDirection.updated_at.asc())
        .limit(20)
        .all()
    )
    for row in rows:
        if _age_ok(_loads(row.frontier_json)):
            continue
        refresh_direction_frontier(db, row.lab_id, row)
        return row.id
    return ""
