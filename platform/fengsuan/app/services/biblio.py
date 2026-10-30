"""书目：署名、参考文献、OpenAlex / Crossref 补全。

原则：
- 老的 authors_json 不删，署名表是它的「带编号」版本；
- 参考文献来自 PDF（GROBID）或 OpenAlex / Crossref，写明来源；
- 被引那篇在组里时才填 cited_item_id，关系都从这张表算，不让模型猜；
- 断网或对方限流就记 offline，过几小时再试，不影响入库。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy.orm import Session

from fengsuan.app.models.literature import Authorship, LiteratureItem, LiteratureReference
from fengsuan.app.services.tei import normalize_doi

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
MAILTO = "fengsuan-research@example.com"


def _mailto() -> str:
    import os

    return (os.environ.get("FENGSUN_CONTACT_EMAIL") or MAILTO).strip()
_OFFLINE_UNTIL: dict[str, float] = {}


def _title_key(title: str) -> str:
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (title or "").lower())
    return t[:120]


def ref_key(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    title: str | None = None,
    author: str | None = None,
    year: int | str | None = None,
) -> str:
    d = normalize_doi(doi or "")
    if d:
        return f"doi:{d}"[:200]
    a = re.sub(r"v\d+$", "", (arxiv_id or "").strip().lower())
    if a:
        return f"arxiv:{a}"[:200]
    from openlens_ai.utils.paper_dedupe import norm_title

    t = norm_title(title or "")
    if len(t) >= 16:
        return f"t:{t}"[:200]
    who = norm_title(author or "")
    yr = str(year or "").strip()[:4]
    if who and yr.isdigit():
        return f"ty:{t[:48]}:{who[:40]}:{yr}"[:200]
    return f"t:{t}"[:200] if len(t) >= 8 else ""


def _item_author(item: LiteratureItem) -> str:
    try:
        raw = json.loads(item.authors_json or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(raw, list) or not raw:
        return ""
    first = raw[0]
    if isinstance(first, dict):
        return str(first.get("name") or "")
    return str(first or "")


def item_keys(item: LiteratureItem) -> list[str]:
    keys = []
    author = _item_author(item)
    year = item.publish_year
    for k in (
        ref_key(doi=item.doi),
        ref_key(arxiv_id=item.arxiv_id),
        ref_key(title=item.title, author=author, year=year),
    ):
        if k and k not in keys:
            keys.append(k)
    return keys


def _lab_key_map(db: Session, lab_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = (
        db.query(LiteratureItem.id, LiteratureItem.doi, LiteratureItem.arxiv_id, LiteratureItem.title)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .all()
    )
    for iid, doi, arxiv, title in rows:
        for k in (ref_key(doi=doi), ref_key(arxiv_id=arxiv), ref_key(title=title)):
            if k:
                out.setdefault(k, iid)
    return out


# ---------------------------------------------------------------- 署名


def _names_from_json(raw: str) -> list[str]:
    try:
        val = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for x in val if isinstance(val, list) else []:
        if isinstance(x, str) and x.strip():
            out.append(" ".join(x.split()))
        elif isinstance(x, dict):
            name = " ".join(str(x.get("name") or x.get("full_name") or "").split())
            if name:
                out.append(name)
    return out


def sync_authorships(
    db: Session,
    item: LiteratureItem,
    details: list[dict] | None = None,
    *,
    evidence_source: str = "",
) -> int:
    """把署名写进 authorships。details 带编号（orcid / openalex / s2 / affiliation）就一并记下。

    不给 details 就从 authors_json 取名字。已有署名时只补缺，不覆盖人工认过的。
    """
    from fengsuan.app.services.authors import name_key, resolve_item_authors

    if details:
        people = [d for d in details if isinstance(d, dict) and (d.get("name") or "").strip()]
    else:
        people = [{"name": n} for n in _names_from_json(item.authors_json)]
    if not people:
        return 0
    existing = {
        s.position: s for s in db.query(Authorship).filter(Authorship.item_id == item.id).all()
    }
    changed = 0
    for pos, person in enumerate(people[:80]):
        name = " ".join(str(person.get("name") or "").split())[:300]
        orcid = str(person.get("orcid") or "").strip()
        m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])", orcid)
        orcid = m.group(1).upper() if m else ""
        ext = ""
        if person.get("openalex"):
            ext = "oa:" + str(person["openalex"]).rsplit("/", 1)[-1][:50]
        elif person.get("s2"):
            ext = "s2:" + str(person["s2"])[:50]
        aff = str(person.get("affiliation") or "")[:500]
        row = existing.get(pos)
        if row is None:
            db.add(
                Authorship(
                    item_id=item.id,
                    position=pos,
                    raw_name=name,
                    affiliation=aff,
                    orcid=orcid or None,
                    external_id=ext or None,
                )
            )
            changed += 1
            continue
        if row.evidence == "manual":
            continue
        same = name_key(row.raw_name) == name_key(name) or not row.raw_name
        if not same and details:
            row.raw_name = name
            row.author_id = None
            row.evidence = ""
            row.orcid = orcid or None
            row.external_id = ext or None
            row.affiliation = aff
            changed += 1
            continue
        if orcid and not row.orcid:
            row.orcid, row.author_id, changed = orcid, None, changed + 1
        if ext and not row.external_id:
            row.external_id = ext
            if not row.orcid:
                row.author_id = None
            changed += 1
        if aff and not row.affiliation:
            row.affiliation = aff
    db.flush()
    resolve_item_authors(db, item)
    return changed


def backfill_authorships(db: Session, *, limit: int = 40) -> int:
    """老论文只有 authors_json：分批补成署名表。"""
    has = db.query(Authorship.item_id)
    rows = (
        db.query(LiteratureItem)
        .filter(
            LiteratureItem.deleted_at.is_(None),
            ~LiteratureItem.id.in_(has),
            LiteratureItem.authors_json.isnot(None),
            LiteratureItem.authors_json != "[]",
            LiteratureItem.authors_json != "",
        )
        .limit(limit)
        .all()
    )
    n = 0
    for item in rows:
        try:
            with db.begin_nested():
                sync_authorships(db, item)
            n += 1
        except Exception:
            continue
    return n


# ---------------------------------------------------------------- 参考文献


def save_references(db: Session, item: LiteratureItem, refs: list[dict], *, source: str) -> int:
    """整篇替换同一来源的参考文献。GROBID 的比 OpenAlex 的细，两者都有时各存一份，查询时去重。"""
    if not refs:
        return 0
    db.query(LiteratureReference).filter(
        LiteratureReference.citing_item_id == item.id, LiteratureReference.source == source
    ).delete(synchronize_session=False)
    keymap = _lab_key_map(db, item.lab_id)
    own = set(item_keys(item))
    n = 0
    for pos, ref in enumerate(refs[:400]):
        key = ref_key(doi=ref.get("doi"), arxiv_id=ref.get("arxiv_id"), title=ref.get("title"))
        if key and key in own:
            continue
        cited = keymap.get(key) if key else None
        db.add(
            LiteratureReference(
                lab_id=item.lab_id,
                citing_item_id=item.id,
                cited_item_id=cited if cited != item.id else None,
                position=int(ref.get("position", pos) or pos),
                raw=str(ref.get("raw") or "")[:2000],
                title=str(ref.get("title") or "")[:500],
                doi=normalize_doi(ref.get("doi") or "") or None,
                arxiv_id=(ref.get("arxiv_id") or None),
                year=ref.get("year") if isinstance(ref.get("year"), int) else None,
                first_author=str(ref.get("first_author") or "")[:200],
                ref_key=key,
                source=source[:20],
            )
        )
        n += 1
    db.flush()
    return n


def link_item_as_cited(db: Session, item: LiteratureItem) -> int:
    """新入库的这篇，如果别的篇早就引过它，把那些参考文献条目指过来。"""
    keys = item_keys(item)
    if not keys:
        return 0
    n = (
        db.query(LiteratureReference)
        .filter(
            LiteratureReference.lab_id == item.lab_id,
            LiteratureReference.ref_key.in_(keys),
            LiteratureReference.cited_item_id.is_(None),
            LiteratureReference.citing_item_id != item.id,
        )
        .update({LiteratureReference.cited_item_id: item.id}, synchronize_session=False)
    )
    return int(n or 0)


# ---------------------------------------------------------------- OpenAlex / Crossref


def _get_json(url: str, params: dict | None = None, *, host: str) -> dict | None:
    if _OFFLINE_UNTIL.get(host, 0) > time.time():
        raise ConnectionError(host)
    import httpx

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            sent = dict(params or {})
            if "mailto" in sent:
                sent["mailto"] = _mailto()
            r = client.get(url, params=sent, headers={"User-Agent": f"fengsuan-research (mailto:{_mailto()})"})
    except Exception as exc:
        _OFFLINE_UNTIL[host] = time.time() + 3 * 3600
        raise ConnectionError(host) from exc
    if r.status_code == 429 or r.status_code >= 500:
        _OFFLINE_UNTIL[host] = time.time() + 1800
        raise ConnectionError(host)
    if r.status_code >= 400:
        return None
    try:
        body = r.json()
    except Exception:
        return None
    # 有些接口在限流时会回一段纯文本或数组；不能当 dict 往下传。
    return body if isinstance(body, dict) else None


def _openalex_work(item: LiteratureItem) -> dict | None:
    params = {"mailto": MAILTO}
    if item.openalex_id:
        return _get_json(f"{OPENALEX}/works/{item.openalex_id}", params, host="openalex")
    doi = normalize_doi(item.doi or "")
    if doi:
        return _get_json(f"{OPENALEX}/works/doi:{doi}", params, host="openalex")
    if item.arxiv_id:
        hit = _get_json(
            f"{OPENALEX}/works",
            {**params, "filter": f"ids.arxiv:{item.arxiv_id}", "per-page": 1},
            host="openalex",
        )
        rows = (hit or {}).get("results") or []
        if rows:
            return rows[0]
    title = (item.title or "").strip()
    if len(title) < 16:
        return None
    hit = _get_json(
        f"{OPENALEX}/works",
        {**params, "search": title[:200], "per-page": 3},
        host="openalex",
    )
    want = _title_key(title)
    for row in (hit or {}).get("results") or []:
        got = _title_key(str(row.get("display_name") or ""))
        if got and (got == want or (len(want) > 30 and (got.startswith(want[:40]) or want.startswith(got[:40])))):
            if item.publish_year and row.get("publication_year") and abs(int(row["publication_year"]) - int(item.publish_year)) > 1:
                continue
            return row
    return None


def _openalex_refs(work: dict, cap: int = 120) -> list[dict]:
    ids = [str(w).rsplit("/", 1)[-1] for w in (work.get("referenced_works") or [])][:cap]
    out: list[dict] = []
    for start in range(0, len(ids), 50):
        part = ids[start : start + 50]
        data = _get_json(
            f"{OPENALEX}/works",
            {
                "mailto": MAILTO,
                "filter": "openalex:" + "|".join(part),
                "per-page": 50,
                "select": "id,doi,display_name,publication_year,authorships,ids",
            },
            host="openalex",
        )
        for row in (data or {}).get("results") or []:
            first = ""
            ships = row.get("authorships") or []
            if ships:
                first = str(((ships[0] or {}).get("author") or {}).get("display_name") or "")
            arxiv = ""
            for k, v in (row.get("ids") or {}).items():
                if "arxiv" in str(k).lower():
                    arxiv = str(v).rsplit("/", 1)[-1]
            out.append(
                {
                    "position": len(out),
                    "title": str(row.get("display_name") or ""),
                    "doi": normalize_doi(str(row.get("doi") or "")),
                    "arxiv_id": arxiv,
                    "year": row.get("publication_year"),
                    "first_author": first,
                    "raw": str(row.get("display_name") or ""),
                    "openalex": str(row.get("id") or "").rsplit("/", 1)[-1],
                }
            )
    return out


def _details_from_openalex(work: dict) -> list[dict]:
    out = []
    for ship in work.get("authorships") or []:
        a = (ship or {}).get("author") or {}
        name = str(a.get("display_name") or (ship or {}).get("raw_author_name") or "").strip()
        if not name:
            continue
        insts = (ship or {}).get("institutions") or []
        out.append(
            {
                "name": name,
                "openalex": str(a.get("id") or "").rsplit("/", 1)[-1],
                "orcid": str(a.get("orcid") or ""),
                "affiliation": str((insts[0] or {}).get("display_name") or "") if insts else "",
            }
        )
    return out


def _crossref_work(doi: str) -> dict | None:
    data = _get_json(f"{CROSSREF}/works/{doi}", {"mailto": MAILTO}, host="crossref")
    return (data or {}).get("message") if isinstance(data, dict) else None


def _details_from_crossref(msg: dict) -> list[dict]:
    out = []
    for a in msg.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x).strip() or str(a.get("name") or "")
        if not name:
            continue
        affs = [str(x.get("name") or "") for x in (a.get("affiliation") or []) if isinstance(x, dict)]
        out.append({"name": name, "orcid": str(a.get("ORCID") or ""), "affiliation": "; ".join(x for x in affs if x)})
    return out


def _refs_from_crossref(msg: dict) -> list[dict]:
    out = []
    for i, ref in enumerate(msg.get("reference") or []):
        title = str(ref.get("article-title") or ref.get("volume-title") or "")
        raw = str(ref.get("unstructured") or title or ref.get("journal-title") or "")
        doi = normalize_doi(str(ref.get("DOI") or ""))
        if not (title or raw or doi):
            continue
        year = None
        m = re.search(r"(19|20)\d{2}", str(ref.get("year") or ""))
        if m:
            year = int(m.group(0))
        out.append(
            {"position": i, "title": title, "raw": raw, "doi": doi, "year": year, "first_author": str(ref.get("author") or "")}
        )
    return out


def enrich_item(db: Session, item: LiteratureItem) -> str:
    """补书目：作者编号、单位、被引数、参考文献。返回 ok / miss / offline。"""
    try:
        work = _openalex_work(item)
    except ConnectionError:
        work = None
        oa_down = True
    else:
        oa_down = False
    state = "miss"
    if work:
        state = "ok"
        oid = str(work.get("id") or "").rsplit("/", 1)[-1]
        if oid:
            item.openalex_id = oid[:40]
        if work.get("cited_by_count") is not None:
            item.cited_by_count = int(work.get("cited_by_count") or 0)
        doi = normalize_doi(str(work.get("doi") or ""))
        if doi and not item.doi:
            item.doi = doi
        if not item.publish_year and work.get("publication_year"):
            item.publish_year = int(work["publication_year"])
        details = _details_from_openalex(work)
        if details:
            if not _names_from_json(item.authors_json):
                item.authors_json = json.dumps([d["name"] for d in details], ensure_ascii=False)
            sync_authorships(db, item, details, evidence_source="openalex")
        try:
            refs = _openalex_refs(work)
        except ConnectionError:
            refs = []
        if refs:
            save_references(db, item, refs, source="openalex")
    doi = normalize_doi(item.doi or "")
    if doi and (not work or not work.get("referenced_works")):
        try:
            msg = _crossref_work(doi)
        except ConnectionError:
            msg = None
            if oa_down:
                return "offline"
        if msg:
            state = "ok"
            details = _details_from_crossref(msg)
            if details and not work:
                sync_authorships(db, item, details, evidence_source="crossref")
            refs = _refs_from_crossref(msg)
            if refs:
                save_references(db, item, refs, source="crossref")
            if item.cited_by_count is None and msg.get("is-referenced-by-count") is not None:
                item.cited_by_count = int(msg.get("is-referenced-by-count") or 0)
    elif oa_down and not work:
        return "offline"
    link_item_as_cited(db, item)
    return state


def enrich_pending(db: Session, *, limit: int = 2) -> int:
    """后台补书目：没补过的先补；断网的过几小时再试。"""
    if any(until > time.time() for until in _OFFLINE_UNTIL.values()) and all(
        _OFFLINE_UNTIL.get(h, 0) > time.time() for h in ("openalex", "crossref")
    ):
        return 0
    rows = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.deleted_at.is_(None), LiteratureItem.enrich_state.in_(("", "offline")))
        .order_by(LiteratureItem.created_at.desc())
        .limit(limit)
        .all()
    )
    n = 0
    for item in rows:
        try:
            with db.begin_nested():
                item.enrich_state = enrich_item(db, item)
            n += 1
        except Exception:
            item.enrich_state = "miss"
    return n


def references_of(db: Session, item_id: str, *, limit: int = 200) -> list[dict]:
    """这篇引了谁。多来源去重，GROBID 优先（带原文），组里有的标出来。"""
    rows = (
        db.query(LiteratureReference)
        .filter(LiteratureReference.citing_item_id == item_id)
        .order_by(LiteratureReference.source.asc(), LiteratureReference.position.asc())
        .all()
    )
    rank = {"grobid": 0, "crossref": 1, "openalex": 2}
    rows.sort(key=lambda r: (rank.get(r.source or "", 3), r.position or 0))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = r.ref_key or f"raw:{(r.raw or '')[:80]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": r.id,
                "title": r.title or r.raw[:200],
                "year": r.year,
                "doi": r.doi or "",
                "arxiv_id": r.arxiv_id or "",
                "first_author": r.first_author or "",
                "in_lab_item_id": r.cited_item_id or "",
                "source": r.source,
            }
        )
        if len(out) >= limit:
            break
    return out


def citing_in_lab(db: Session, item_id: str, *, limit: int = 100) -> list[str]:
    """组里谁引了这篇。"""
    rows = (
        db.query(LiteratureReference.citing_item_id)
        .filter(LiteratureReference.cited_item_id == item_id)
        .distinct()
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r[0] and r[0] != item_id]
