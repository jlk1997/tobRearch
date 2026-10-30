"""几千篇之后才需要的工具：认人、查引用、找相关、按段取原文。

作者按组各存一份；同名不同人时不猜，交回给用户选。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from fengsuan.app.models.literature import Author, Authorship, LiteratureItem
from fengsuan.app.services.agent.registry import ToolContext, ToolResult, tool
from fengsuan.app.services.agent.tools import (
    _clip,
    _ctx_question,
    _direction_id,
    _literature_miss,
    _member,
    _member_named_in_question,
    _resolve_literature_item,
)

EVIDENCE_LABEL = {
    "manual": "导师确认过",
    "orcid": "ORCID 对上",
    "openalex": "OpenAlex 编号对上",
    "s2": "Semantic Scholar 编号对上",
    "coauthor": "合作者对上",
    "affiliation": "单位对上",
    "name_only": "只按名字",
}

_LATIN_NAME = re.compile(r"\b([A-Z][A-Za-z'\-]+(?:[\s\.]+[A-Z][A-Za-z'\-]*\.?){1,3})")
_CJK_BEFORE = re.compile(
    r"([\u4e00-\u9fff]{2,5})(?:老师|教授|博士)?(?:还写过|写过|写了|发过|的论文|的文章|的工作|是谁|这个作者|这个人|和谁)"
)
_NOT_NAMES = {"arxiv", "doi", "pmid", "openalex", "orcid", "pdf"}


def _raw_question(ctx: ToolContext) -> str:
    return str((ctx.extra or {}).get("question") or "")


def _author_href(ctx: ToolContext, author_id: str) -> str:
    return f"/labs/{ctx.lab_id}/literature?author={author_id}"


def _names_from_question(ctx: ToolContext) -> list[str]:
    raw = _raw_question(ctx)
    out: list[str] = []
    for m in _LATIN_NAME.finditer(raw):
        name = m.group(1).strip(" .")
        if name.lower() not in _NOT_NAMES and len(name) >= 4:
            out.append(name)
    for m in _CJK_BEFORE.finditer(raw):
        run = m.group(1)
        out.extend(run[-n:] for n in range(len(run), 1, -1))
    return list(dict.fromkeys(out))


def _claimed_author_of_member(ctx: ToolContext, user_id: str) -> Author | None:
    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.services.authors import live_author

    mem = (
        ctx.db.query(LabMembership)
        .filter(LabMembership.lab_id == ctx.lab_id, LabMembership.user_id == user_id)
        .first()
    )
    if mem is None or not getattr(mem, "author_id", None):
        return None
    return live_author(ctx.db, mem.author_id)


def _scan_known_names(ctx: ToolContext) -> list[Author]:
    """问句里没有像名字的词时，拿组里作者名去对问句。只认 5 个字母以上或两个汉字以上的整名。"""
    asked = _ctx_question(ctx)
    if not asked:
        return []
    hits: list[Author] = []
    rows = (
        ctx.db.query(Author)
        .filter(Author.lab_id == ctx.lab_id, Author.merged_into.is_(None))
        .order_by(Author.paper_count.desc())
        .limit(20000)
        .all()
    )
    for a in rows:
        name = (a.display_name or "").strip().lower()
        ascii_name = all(ord(c) < 128 for c in name)
        if (ascii_name and len(name) >= 5 or not ascii_name and len(name) >= 2) and name in asked:
            hits.append(a)
    return hits


def _disambiguate(ctx: ToolContext, cands: list[Author]) -> Author | None:
    """问句里带了单位、题名或合作者，就用它挑出唯一的那位。挑不出返回空。"""
    import json

    asked = _ctx_question(ctx)
    scored: list[tuple[int, Author]] = []
    for a in cands:
        score = 0
        try:
            affs = json.loads(a.affiliations_json or "[]")
        except json.JSONDecodeError:
            affs = []
        for aff in affs:
            aff = str(aff or "").strip().lower()
            if len(aff) >= 4 and (aff in asked or aff[:16] in asked):
                score += 3
                break
        titles = (
            ctx.db.query(LiteratureItem.title)
            .join(Authorship, Authorship.item_id == LiteratureItem.id)
            .filter(Authorship.author_id == a.id, LiteratureItem.deleted_at.is_(None))
            .limit(40)
            .all()
        )
        for (t,) in titles:
            t = (t or "").strip().lower()
            if len(t) >= 6 and (t in asked or t[:24] in asked):
                score += 4
                break
        scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def resolve_author(ctx: ToolContext, args: dict) -> tuple[Author | None, list[Author], str]:
    """返回（认定的作者, 同名候选, 用来找的名字）。候选多于一个且挑不出时，认定为空。"""
    from fengsuan.app.services.authors import find_authors, live_author

    aid = str(args.get("author_id") or "").strip()
    if aid:
        row = live_author(ctx.db, aid)
        if row is not None and row.lab_id == ctx.lab_id:
            return row, [row], row.display_name
    name = str(args.get("name") or "").strip()
    if not name:
        uid = _member_named_in_question(ctx)
        if uid:
            claimed = _claimed_author_of_member(ctx, uid)
            if claimed is not None:
                return claimed, [claimed], claimed.display_name
    names = [name] if name else _names_from_question(ctx)
    for n in names:
        cands = find_authors(ctx.db, ctx.lab_id, n, limit=8)
        if cands:
            return _pick(ctx, cands, n)
    if not name:
        known = _scan_known_names(ctx)
        if known:
            longest = max(len(a.display_name or "") for a in known)
            top = [a for a in known if len(a.display_name or "") == longest]
            cands = find_authors(ctx.db, ctx.lab_id, top[0].display_name, limit=8) or top
            return _pick(ctx, cands, top[0].display_name)
    return None, [], name or (names[0] if names else "")


def _pick(ctx: ToolContext, cands: list[Author], name: str) -> tuple[Author | None, list[Author], str]:
    if len(cands) == 1:
        return cands[0], cands, name
    chosen = _disambiguate(ctx, cands)
    return chosen, cands, name


def _author_line(ctx: ToolContext, a: Author) -> str:
    import json

    from fengsuan.app.services.authors import author_dto

    dto = author_dto(ctx.db, a, with_papers=2)
    bits = [f"组里 {int(a.paper_count or 0)} 篇"]
    try:
        affs = [x for x in json.loads(a.affiliations_json or "[]") if x]
    except json.JSONDecodeError:
        affs = []
    if affs:
        bits.append(str(affs[0])[:40])
    titles = [p["title"] for p in dto.get("papers") or [] if p.get("title")]
    if titles:
        bits.append("例如《" + titles[0][:40] + "》")
    if dto.get("member"):
        bits.append(f"组里的 {dto['member']['name']} 认领了")
    if a.orcid:
        bits.append("有 ORCID")
    return " · ".join(bits)


def _author_source(ctx: ToolContext, a: Author) -> dict:
    return {
        "id": f"author:{a.id}",
        "kind": "author",
        "title": a.display_name or "",
        "author_id": a.id,
        "href": _author_href(ctx, a.id),
        "excerpt": _author_line(ctx, a),
    }


def _ask_back(ctx: ToolContext, cands: list[Author], name: str) -> ToolResult:
    """同名不同人：不猜，列出候选让用户点。"""
    from fengsuan.app.services.authors import author_dto

    raw = _raw_question(ctx).strip() or f"{name} 还写过什么？"
    options = []
    sources = []
    for a in cands[:5]:
        dto = author_dto(ctx.db, a, with_papers=1)
        first = (dto.get("papers") or [{}])[0].get("title") or ""
        aff = (dto.get("affiliations") or [""])[0]
        hint = f"写《{first[:40]}》的" if first else (f"{aff[:30]} 的" if aff else "")
        label_bits = [a.display_name or name]
        if aff:
            label_bits.append(aff[:24])
        label_bits.append(f"{int(a.paper_count or 0)} 篇")
        follow = raw.replace(name, f"{hint}{a.display_name}", 1) if name and name in raw else f"{hint}{a.display_name} {raw}"
        options.append({"author_id": a.id, "label": " · ".join(label_bits), "question": follow[:200]})
        sources.append(_author_source(ctx, a))
    text = f"论文署名里有 {len(options)} 位接近「{name}」。不一定是组里的成员。"
    return ToolResult(
        text=text,
        sources=sources,
        data={"ask_back": {"text": text, "name": name, "options": options, "where": "lab"}, "ambiguous": True},
    )


def _no_author(ctx: ToolContext, name: str) -> ToolResult:
    who = f"「{name}」" if name else "这个人"
    return ToolResult(
        text=f"组里的论文署名里没有找到{who}。这不表示网上没有，接着按公开学术库查，不用先把人拉进组。",
        data={"empty": True, "miss": True, "name": name},
    )


@tool(
    "find_author",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="按名字找组里论文的作者：是谁、在组里有几篇、在哪个单位、是不是组里成员。同名的人可能不止一位，拿不准时会列出候选请用户选，不要自己猜。",
    params={
        "properties": {
            "name": {"type": "string", "description": "作者名，原文写法，如 Wei Zhang"},
            "author_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("这个作者", "作者是谁", "同名", "是同一个人"),
)
def find_author(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    chosen, cands, name = resolve_author(ctx, args)
    if not cands:
        return _no_author(ctx, name)
    if chosen is None:
        return _ask_back(ctx, cands, name)
    src = _author_source(ctx, chosen)
    others = [a for a in cands if a.id != chosen.id]
    text = f"{chosen.display_name}：{src['excerpt']}"
    if others:
        text += f"。另有 {len(others)} 位同名的作者，没有算进来。"
    return ToolResult(text=text, sources=[src], data={"author_id": chosen.id})


@tool(
    "get_author_works",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="某位作者在组里的全部论文，按年份排，注明第几作者、凭什么认成这个人、常一起署名的人。同名拿不准时列出候选请用户选。",
    params={
        "properties": {
            "name": {"type": "string"},
            "author_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("还写过", "写过哪些", "的论文有哪些", "发过哪些"),
)
def get_author_works(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.authors import author_dto, live_author

    chosen, cands, name = resolve_author(ctx, args)
    if not cands:
        return _no_author(ctx, name)
    if chosen is None:
        return _ask_back(ctx, cands, name)
    dto = author_dto(ctx.db, chosen, with_papers=40)
    papers = dto.get("papers") or []
    sources = [_author_source(ctx, chosen)]
    for p in papers[:12]:
        bits = [f"第 {int(p.get('position') or 0) + 1} 作者"]
        if p.get("year"):
            bits.append(str(p["year"]))
        if p.get("venue"):
            bits.append(str(p["venue"])[:40])
        bits.append(EVIDENCE_LABEL.get(p.get("evidence") or "", "只按名字"))
        sources.append(
            {
                "id": f"lit:{p['id']}",
                "kind": "literature",
                "title": p.get("title") or "",
                "item_id": p["id"],
                "href": f"/labs/{ctx.lab_id}/literature/{p['id']}",
                "excerpt": " · ".join(bits),
                "relation": "own_work",
                "author_id": chosen.id,
            }
        )
    item_ids = [p["id"] for p in papers]
    co = Counter()
    if item_ids:
        for (aid,) in (
            ctx.db.query(Authorship.author_id)
            .filter(Authorship.item_id.in_(item_ids), Authorship.author_id.isnot(None), Authorship.author_id != chosen.id)
            .all()
        ):
            co[aid] += 1
    partners = []
    for aid, n in co.most_common(5):
        a = live_author(ctx.db, aid)
        if a is not None:
            partners.append(f"{a.display_name}（{n} 篇）")
    weak = sum(1 for p in papers if (p.get("evidence") or "") == "name_only")
    parts = [f"{chosen.display_name} 在组里有 {len(papers)} 篇"]
    if partners:
        parts.append("常一起署名的：" + "、".join(partners))
    if weak:
        parts.append(f"其中 {weak} 篇只按名字对上，可能混进了同名的人")
    if dto.get("member"):
        parts.append(f"组里的 {dto['member']['name']} 认领了这个作者")
    others = [a for a in cands if a.id != chosen.id]
    if others:
        parts.append(f"另有 {len(others)} 位同名作者没有算进来")
    return ToolResult(
        text="。".join(parts) + "。",
        sources=sources,
        data={"author_id": chosen.id, "paper_count": len(papers), "name_only": weak},
    )


def _paper_head(ctx: ToolContext, item: LiteratureItem, excerpt: str) -> dict:
    return {
        "id": f"lit:{item.id}",
        "kind": "literature",
        "title": item.title or "",
        "item_id": item.id,
        "href": f"/labs/{ctx.lab_id}/literature/{item.id}",
        "excerpt": excerpt,
    }


@tool(
    "get_paper_references",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="这篇论文引了哪些文献（它的参考文献），标出哪些组里也有。只报拆出来的参考文献，不要凭记忆补。",
    params={"properties": {"item_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": []},
    stub_keywords=("参考文献", "引了哪些", "它引了", "这篇引了"),
)
def get_paper_references(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.biblio import references_of

    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    refs = references_of(ctx.db, item.id, limit=300)
    if not refs:
        return ToolResult(
            text=f"《{item.title}》还没拆出参考文献：PDF 可能还在排队拆结构，或书目库里查不到。",
            sources=[_paper_head(ctx, item, "还没拆出参考文献")],
            data={"empty": True, "reference_count": 0},
        )
    in_lab = [r for r in refs if r.get("in_lab_item_id")]
    outside = [r for r in refs if not r.get("in_lab_item_id")]
    head = _paper_head(ctx, item, f"共列了 {len(refs)} 条参考文献，组里有 {len(in_lab)} 篇")
    sources = [head]
    got = {
        it.id: it
        for it in ctx.db.query(LiteratureItem)
        .filter(LiteratureItem.id.in_([r["in_lab_item_id"] for r in in_lab]), LiteratureItem.deleted_at.is_(None))
        .all()
    } if in_lab else {}
    for r in in_lab[:10]:
        it = got.get(r["in_lab_item_id"])
        if it is None:
            continue
        src = _paper_head(ctx, it, f"《{item.title[:40]}》的参考文献里有它，组里也有")
        src["relation"] = "cites"
        sources.append(src)
    for r in outside[:8]:
        bits = [r.get("first_author") or "", str(r.get("year") or ""), r.get("doi") or ""]
        sources.append(
            {
                "id": f"ref:{r['id']}",
                "kind": "citation",
                "title": (r.get("title") or "")[:200],
                "href": f"https://doi.org/{r['doi']}" if r.get("doi") else "",
                "excerpt": "组里还没有这篇。" + " · ".join(b for b in bits if b),
                "relation": "cites",
                "doi": r.get("doi") or "",
                "arxiv_id": r.get("arxiv_id") or "",
                "year": r.get("year"),
                "authors": [r["first_author"]] if r.get("first_author") else [],
            }
        )
    text = f"《{item.title}》列了 {len(refs)} 条参考文献，其中 {len(in_lab)} 篇组里也有。"
    return ToolResult(
        text=text,
        sources=sources,
        data={"reference_count": len(refs), "in_lab": len(in_lab), "item_id": item.id},
    )


@tool(
    "get_citing_papers",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="组里哪些论文引了这篇；另报外部书目库记的总被引数（那是全世界的数，不是组里的）。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    stub_keywords=("谁引了", "哪些论文引了", "引了这篇", "被引了多少"),
)
def get_citing_papers(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.biblio import citing_in_lab

    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    ids = citing_in_lab(ctx.db, item.id, limit=200)
    rows = (
        ctx.db.query(LiteratureItem)
        .filter(LiteratureItem.id.in_(ids), LiteratureItem.deleted_at.is_(None), LiteratureItem.lab_id == ctx.lab_id)
        .order_by(LiteratureItem.publish_year.desc().nullslast())
        .all()
    ) if ids else []
    outside = item.cited_by_count
    note = f"外部书目库记它一共被引 {outside} 次（全世界的数）" if outside else ""
    head = _paper_head(ctx, item, f"组里有 {len(rows)} 篇引了它" + (f"；{note}" if note else ""))
    sources = [head]
    for it in rows[:12]:
        src = _paper_head(ctx, it, f"它的参考文献里有《{(item.title or '')[:40]}》" + (f" · {it.publish_year}" if it.publish_year else ""))
        src["relation"] = "cited_by"
        sources.append(src)
    if rows:
        text = f"组里有 {len(rows)} 篇引了《{item.title}》。"
    else:
        text = f"组里还没有论文引《{item.title}》，也可能是引用它的论文还没拆出参考文献。"
    if note:
        text += note + "。"
    return ToolResult(
        text=text,
        sources=sources,
        data={"cited_in_lab": len(rows), "cited_by_count": outside, "item_id": item.id, "empty": not rows},
    )


_REL_ORDER = ("cites", "cited_by", "same_author", "co_cited", "bib_coupled", "topic", "same_direction")


@tool(
    "get_related_papers",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="组里和这篇有关的论文，按关系分组：它引了、引了它、同一作者、常一起被引、引的文献相同、内容相近、同一方向。回答要按这些组分开说，只按名字对上的作者要说明可能不是同一人。",
    params={
        "properties": {
            "item_id": {"type": "string"},
            "kinds": {"type": "array", "items": {"type": "string"}, "description": "只要某几种关系，如 same_author、topic"},
        },
        "required": [],
    },
    stub_keywords=("相关的论文", "相近的论文", "类似的论文", "和这篇相关", "跟这篇像", "有关系的论文"),
)
def get_related_papers(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.relations import KIND_LABEL, related_papers

    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    kinds = args.get("kinds") if isinstance(args.get("kinds"), list) else None
    groups = related_papers(ctx.db, item, kinds=[str(k) for k in kinds] if kinds else None, limit_each=6)
    by_item: dict[str, dict] = {}
    order: list[str] = []
    for kind in _REL_ORDER:
        for row in groups.get(kind) or []:
            iid = row["item_id"]
            label = KIND_LABEL.get(kind, kind)
            if iid not in by_item:
                by_item[iid] = {"row": row, "kinds": [kind], "whys": [f"{label}：{row.get('why') or ''}".rstrip("：")]}
                order.append(iid)
            else:
                by_item[iid]["kinds"].append(kind)
                by_item[iid]["whys"].append(f"{label}：{row.get('why') or ''}".rstrip("："))
    head = _paper_head(ctx, item, "问的是这篇")
    sources = [head]
    for iid in order[:20]:
        got = by_item[iid]
        row = got["row"]
        sources.append(
            {
                "id": f"lit:{iid}",
                "kind": "related",
                "title": row.get("title") or "",
                "item_id": iid,
                "href": f"/labs/{ctx.lab_id}/literature/{iid}",
                "excerpt": "；".join(got["whys"])[:300] + (f" · {row['year']}" if row.get("year") else ""),
                "relation": got["kinds"][0],
                "relations": got["kinds"],
            }
        )
    if len(sources) == 1:
        return ToolResult(
            text=f"组里还没找到和《{item.title}》有关系的论文：参考文献、作者或正文向量可能还没整理好。",
            sources=sources,
            data={"empty": True, "item_id": item.id},
        )
    summary = "；".join(f"{KIND_LABEL.get(k, k)} {len(groups[k])} 篇" for k in _REL_ORDER if groups.get(k))
    return ToolResult(
        text=f"和《{item.title}》有关的：{summary}。",
        sources=sources,
        data={"item_id": item.id, "groups": {k: len(v) for k, v in groups.items()}},
    )


SURVEY_MAX = 40
_SURVEY_SOLO = 16
_SURVEY_FILLER = re.compile(
    r"综述一下|总结一下|主要方法|主要|有哪些|做法|脉络|发展趋势|整体上在做|整体|这个方向|这个话题|组里|我们|的|是|在做|什么|吗|呢|？|\?"
)


def _survey_pool(ctx: ToolContext, q: str, direction_id: str) -> tuple[list[tuple[float, LiteratureItem]], bool]:
    """按题名和摘要里的词打分挑篇；组里段落检索命中的篇再加分。返回（按分排好的篇，是否碰到候选上限）。"""
    from sqlalchemy import or_

    from fengsuan.app.models.literature import LiteratureDirectionLink
    from fengsuan.app.services.kb import query_kb
    from fengsuan.app.services.search_index import search_terms

    base = ctx.db.query(LiteratureItem).filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
    if direction_id:
        from fengsuan.app.services.directions import direction_scope_ids

        scope = direction_scope_ids(ctx.db, direction_id, include_children=True) or [direction_id]
        base = base.filter(
            LiteratureItem.id.in_(
                ctx.db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.direction_id.in_(scope))
            )
        )
    terms = search_terms(_SURVEY_FILLER.sub(" ", q), cap=8)
    pool_cap = 400
    scored: dict[str, tuple[float, LiteratureItem]] = {}
    capped = False
    if terms:
        conds = []
        for t in terms:
            conds.extend([LiteratureItem.title.ilike(f"%{t}%"), LiteratureItem.abstract.ilike(f"%{t}%")])
        rows = base.filter(or_(*conds)).limit(pool_cap).all()
        capped = len(rows) >= pool_cap
        counted = []
        for it in rows:
            title = (it.title or "").lower()
            abstract = (it.abstract or "").lower()
            matched = sum(1 for t in terms if t in title or t in abstract)
            s = sum(2.0 for t in terms if t in title) + sum(1.0 for t in terms if t in abstract) + matched
            counted.append((matched, s, it))
        top = max((m for m, _s, _it in counted), default=0)
        need = 2 if len(terms) > 2 and top >= 2 else 1
        for matched, s, it in counted:
            if matched >= need:
                scored[it.id] = (s + (it.read_score or 0) / 50.0, it)
    else:
        rows = base.order_by(LiteratureItem.read_score.desc(), LiteratureItem.created_at.desc()).limit(pool_cap).all()
        capped = len(rows) >= pool_cap
        for it in rows:
            scored[it.id] = (float(it.read_score or 0) / 50.0, it)
    try:
        hits, _fb = query_kb(ctx.db, ctx.lab_id, q, top_k=12, user_id=ctx.user_id, direction_id=direction_id or None, source_kinds=["paper"])
    except Exception:
        hits = []
    for rank, h in enumerate(hits):
        iid = h.get("literature_item_id")
        if not iid:
            continue
        bonus = 3.0 / (1 + rank)
        if iid in scored:
            s, it = scored[iid]
            scored[iid] = (s + bonus, it)
        else:
            it = ctx.db.get(LiteratureItem, iid)
            if it is not None and it.lab_id == ctx.lab_id and it.deleted_at is None:
                scored[iid] = (bonus, it)
    ranked = sorted(scored.values(), key=lambda row: -row[0])
    return ranked, capped


@tool(
    "get_survey_digests",
    scopes=["lab", "onboarding", "writing"],
    description="回答「这个方向 / 这个话题整体在做什么、主要有哪些做法」这类综述问题：挑组里最相关的至多 40 篇，直接用存好的论文笔记，按主题分组。超过 40 篇时要说明只看了最相关的 40 篇。",
    params={
        "properties": {
            "query": {"type": "string", "description": "话题关键词；只问方向整体时可以空着"},
            "direction_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("综述一下", "总结一下", "主要方法有哪些", "脉络", "发展趋势"),
)
def get_survey_digests(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.analysis import best_method_analysis, parse_card
    from fengsuan.app.services.lab_brief import plain_bits
    from fengsuan.app.services.lab_themes import item_theme_map

    q = str(args.get("query") or "").strip()
    if len(q) < 2:
        q = _raw_question(ctx)
    direction_id = _direction_id(ctx, args, inherit="chip")
    ranked, capped = _survey_pool(ctx, q, direction_id)
    if not ranked:
        return ToolResult(text="组里没有找到和这个话题相关的论文。", sources=[], data={"empty": True, "relevant": 0})
    relevant = len(ranked)
    picked = [it for _s, it in ranked[:SURVEY_MAX]]
    themes = item_theme_map(ctx.db, ctx.lab_id)
    rows = []
    for it in picked:
        best = best_method_analysis(ctx.db, it.id)
        line = plain_bits(parse_card(best) if best else {}).get("one_line") or (it.abstract or "")[:160] or ""
        rows.append({"item": it, "line": line.strip(), "theme": (themes.get(it.id) or {}).get("theme") or ""})
    sources = []
    for r in rows[:_SURVEY_SOLO]:
        it = r["item"]
        head = f"主题：{r['theme']}。" if r["theme"] else ""
        sources.append(
            {
                "id": f"lit:{it.id}",
                "kind": "digest",
                "title": it.title or "",
                "item_id": it.id,
                "href": f"/labs/{ctx.lab_id}/literature/{it.id}",
                "excerpt": (head + (r["line"] or "还没有笔记，只有题名。"))[:300],
            }
        )
    rest: dict[str, list[dict]] = {}
    for r in rows[_SURVEY_SOLO:]:
        rest.setdefault(r["theme"] or "其他", []).append(r)
    for theme, bundle in list(rest.items())[:4]:
        body = "；".join(f"《{(b['item'].title or '')[:60]}》：{(b['line'] or '')[:80]}" for b in bundle)
        sources.append(
            {
                "id": f"survey:{hashlib.sha1((theme + ','.join(b['item'].id for b in bundle)).encode()).hexdigest()[:12]}",
                "kind": "digest",
                "title": f"「{theme}」里另外 {len(bundle)} 篇" if theme != "其他" else f"另外 {len(bundle)} 篇",
                "excerpt": body[:900],
            }
        )
    by_theme = Counter(r["theme"] for r in rows if r["theme"])
    parts = [f"组里相关的有{'至少 ' if capped else ' '}{relevant} 篇"]
    if relevant > SURVEY_MAX:
        parts.append(f"只看了最相关的 {SURVEY_MAX} 篇")
    if by_theme:
        parts.append("按主题：" + "、".join(f"{t} {n} 篇" for t, n in by_theme.most_common(5)))
    no_note = sum(1 for r in rows if not r["line"])
    if no_note:
        parts.append(f"其中 {no_note} 篇还没有笔记，只按题名算")
    return ToolResult(
        text="，".join(parts) + "。",
        sources=sources,
        data={"relevant": relevant, "used": len(rows), "capped": relevant > SURVEY_MAX},
    )


@tool(
    "search_passages",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="在组里全部论文的正文里找原话：按章节切好的段落，带篇名、章节和页码。要引用原文、找某个细节、实验设置、数字在哪篇里写过时用这个。query 传关键词，不要传整句。",
    params={
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "description": "默认 8，最多 12"},
            "direction_id": {"type": "string"},
        },
        "required": ["query"],
    },
    stub_keywords=("原文怎么说", "原话", "哪篇里提到", "哪篇写过", "原文里"),
)
def search_passages(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.kb import query_kb

    q = str(args.get("query") or "").strip()
    if not q:
        return ToolResult(error="检索词为空")
    top_k = max(1, min(12, int(args.get("top_k") or 8)))
    hits, _fallback = query_kb(
        ctx.db,
        ctx.lab_id,
        q,
        top_k=top_k,
        user_id=ctx.user_id,
        direction_id=_direction_id(ctx, args, inherit="chip") or None,
        source_kinds=["paper"],
    )
    ids = list({h.get("literature_item_id") for h in hits if h.get("literature_item_id")})
    titles = dict(
        ctx.db.query(LiteratureItem.id, LiteratureItem.title).filter(LiteratureItem.id.in_(ids)).all()
    ) if ids else {}
    sources = []
    for h in hits:
        iid = h.get("literature_item_id") or ""
        title = titles.get(iid) or h.get("filename") or "组里论文"
        where = []
        if h.get("section"):
            where.append(str(h["section"])[:40])
        if h.get("chunk_kind") in {"caption", "table"}:
            where.append("图表说明" if h["chunk_kind"] == "caption" else "表格")
        sources.append(
            {
                "id": f"kb:{h.get('chunk_id')}",
                "kind": "page" if iid else "kb_chunk",
                "title": title,
                "page": h.get("page_no"),
                "item_id": iid,
                "href": h.get("open_href") or "",
                "section": h.get("section") or "",
                "excerpt": ("【" + " · ".join(where) + "】" if where else "") + _clip(h.get("content") or "", 700),
            }
        )
    if not sources:
        return ToolResult(text="正文里没有找到对得上的段落，可能这些论文还没拆完字。", sources=[], data={"empty": True})
    papers = len({s["item_id"] for s in sources if s.get("item_id")})
    return ToolResult(
        text=f"在 {papers} 篇论文的正文里找到 {len(sources)} 段",
        sources=sources,
        data={"retrieval_mode": hits[0].get("retrieval_mode") if hits else ""},
    )


_LEVEL_NAME = (("没读", (-1, 0)), ("读过摘要", (1,)), ("读过全文", (2,)), ("精读过", (3,)))


@tool(
    "get_library_stats",
    scopes=["lab", "onboarding"],
    description="组里论文的数字：一共多少篇、按年份、按方向、助手读到哪一层、署名最多的作者、常见的会议期刊。全部直接查库，问「多少篇、几篇、分布」时用这个，不要去检索。",
    params={"properties": {"direction_id": {"type": "string"}}, "required": []},
    stub_keywords=("多少篇", "几篇论文", "论文数量", "按年份", "统计一下"),
)
def get_library_stats(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from sqlalchemy import func

    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureFile

    base = ctx.db.query(LiteratureItem).filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
    want = _direction_id(ctx, args, inherit="chip")
    scope_name = ""
    if want:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(ctx.db, ctx.lab_id, want)
        base = base.filter(
            LiteratureItem.id.in_(
                ctx.db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.direction_id.in_(scope or ["-"]))
            )
        )
        row = ctx.db.get(ResearchDirection, want)
        scope_name = row.name if row is not None else ""
    ids = base.with_entities(LiteratureItem.id)
    total = base.count()
    if not total:
        return ToolResult(text="组里还没有论文。" if not want else "这个方向下还没有论文。", sources=[], data={"total": 0})
    years = (
        base.with_entities(LiteratureItem.publish_year, func.count(LiteratureItem.id))
        .filter(LiteratureItem.publish_year.isnot(None))
        .group_by(LiteratureItem.publish_year)
        .order_by(LiteratureItem.publish_year.desc())
        .limit(8)
        .all()
    )
    levels = dict(base.with_entities(LiteratureItem.read_level, func.count(LiteratureItem.id)).group_by(LiteratureItem.read_level).all())
    with_pdf = base.filter(LiteratureItem.id.in_(ctx.db.query(LiteratureFile.item_id))).count()
    venues = (
        base.with_entities(LiteratureItem.venue, func.count(LiteratureItem.id))
        .filter(LiteratureItem.venue.isnot(None), LiteratureItem.venue != "")
        .group_by(LiteratureItem.venue)
        .order_by(func.count(LiteratureItem.id).desc())
        .limit(5)
        .all()
    )
    authors = (
        ctx.db.query(Author.display_name, func.count(func.distinct(Authorship.item_id)))
        .join(Authorship, Authorship.author_id == Author.id)
        .filter(Authorship.item_id.in_(ids), Author.merged_into.is_(None))
        .group_by(Author.id, Author.display_name)
        .order_by(func.count(func.distinct(Authorship.item_id)).desc())
        .limit(5)
        .all()
    )
    dirs = []
    if not want:
        dirs = (
            ctx.db.query(ResearchDirection.name, func.count(func.distinct(LiteratureDirectionLink.item_id)))
            .join(LiteratureDirectionLink, LiteratureDirectionLink.direction_id == ResearchDirection.id)
            .filter(
                ResearchDirection.lab_id == ctx.lab_id,
                ResearchDirection.deleted_at.is_(None),
                LiteratureDirectionLink.item_id.in_(ids),
            )
            .group_by(ResearchDirection.id, ResearchDirection.name)
            .order_by(func.count(func.distinct(LiteratureDirectionLink.item_id)).desc())
            .limit(8)
            .all()
        )
    head = f"「{scope_name}」下" if scope_name else "组里"
    parts = [f"{head}一共 {total} 篇，{with_pdf} 篇有 PDF"]
    read_bits = []
    for label, keys in _LEVEL_NAME:
        n = sum(int(levels.get(k, 0)) for k in keys)
        if n:
            read_bits.append(f"{label} {n}")
    if read_bits:
        parts.append("助手" + "、".join(read_bits))
    if years:
        parts.append("按年份：" + "、".join(f"{y} 年 {n} 篇" for y, n in years))
    if dirs:
        parts.append("按方向：" + "、".join(f"{name} {n} 篇" for name, n in dirs))
    if authors:
        parts.append("署名最多：" + "、".join(f"{name} {n} 篇" for name, n in authors))
    if venues:
        parts.append("常见出处：" + "、".join(f"{v[:40]} {n} 篇" for v, n in venues))
    text = "；".join(parts) + "。"
    return ToolResult(
        text=text,
        sources=[
            {
                "id": f"stats:{ctx.lab_id}:{want or 'all'}",
                "kind": "biblio",
                "title": "组里论文的数字",
                "href": f"/labs/{ctx.lab_id}/literature" + (f"?direction_id={want}" if want else ""),
                "excerpt": text[:600],
            }
        ],
        data={"total": total, "with_pdf": with_pdf},
    )
