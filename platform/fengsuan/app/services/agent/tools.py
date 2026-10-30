from __future__ import annotations

import json
import re

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership, ResearchDirection
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.services.agent.registry import ToolContext, ToolResult, tool

# ---------------------------------------------------------------- 权限与取数


def _user(ctx: ToolContext) -> User:
    row = ctx.db.get(User, ctx.user_id)
    if row is None:
        raise ClassifiedHTTPException(403, "账号不可用", "permission")
    return row


def _member(ctx: ToolContext):
    """每个工具的第一步。授权只认这里，不认提示词。"""
    cached = ctx.extra.get("_membership")
    if cached is not None:
        return cached
    from fengsuan.app.core.deps import require_membership

    mem = require_membership(ctx.db, _user(ctx), ctx.lab_id, enforce_org=False)
    ctx.extra["_membership"] = mem
    ctx.role = mem.role or ctx.role
    return mem


def _item(ctx: ToolContext, item_id: str = "") -> LiteratureItem:
    _member(ctx)
    wanted = (item_id or ctx.item_id or "").strip()
    if ctx.scope == "paper" and ctx.item_id:
        wanted = ctx.item_id
    if not wanted:
        raise ClassifiedHTTPException(400, "没有指定文献", "parse")
    item = ctx.db.get(LiteratureItem, wanted)
    if item is None or item.deleted_at is not None or item.lab_id != ctx.lab_id:
        raise ClassifiedHTTPException(404, "文献不存在或不属于本实验室", "permission")
    return item


def _clip(text: str, n: int = 600) -> str:
    s = (text or "").strip()
    return s[:n]


def _ctx_question(ctx: ToolContext) -> str:
    return str((ctx.extra or {}).get("question") or "").lower()


_NAME_STOP = {
    "admin",
    "user",
    "member",
    "teacher",
    "student",
    "lab",
    "paper",
    "the",
    "and",
    "文献",
    "任务",
    "组会",
    "方向",
    "方法",
    "数据",
    "手稿",
    "作业",
    "综述",
    "核心",
    "进度",
    "成员",
    "导师",
    "同学",
    "师兄",
    "师姐",
    "档案",
    "知识库",
    "文件",
    "术语",
    "周志",
    "日志",
    "开题",
    "中期",
    "毕业",
    "节点",
    "看板",
    "复现",
    "调研",
    "本人",
    "这篇",
    "那篇",
    "谁",
    "行动",
}

_YEAR_IN_Q = re.compile(r"(?:19|20)\d{2}")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{4,}")
_QUOTED_TITLE = re.compile(r"[《“\"]([^》”\"]{4,300})[》”\"]")
_DOI_IN_Q = re.compile(r"10\.\d{4,9}/[^\s<>\"')\]]+", re.I)
_ARXIV_IN_Q = re.compile(
    r"(?:arxiv[:\s/-]*|(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?",
    re.I,
)
_PMID_IN_Q = re.compile(r"(?:pmid|pubmed)[:\s#]*(\d{5,8})\b", re.I)
_CNKI_IN_Q = re.compile(r"cnki[:\s#/]+([A-Za-z0-9._-]{4,})", re.I)
_PAGE_CN = re.compile(r"第\s*(\d{1,4})\s*页")
_PAGE_RANGE = re.compile(r"第\s*(\d{1,4})\s*[-~—到至]\s*(\d{1,4})\s*页")
_PAGE_EN = re.compile(r"\bp(?:age)?\.?\s*(\d{1,4})\b", re.I)
_SECTION_NO = re.compile(r"第\s*(\d{1,3})\s*节")
_FIG_IN_Q = re.compile(r"(?:fig(?:ure)?\.?\s*|图\s*)(\d{1,3})(?!\d)", re.I)
_TAB_IN_Q = re.compile(r"(?:tab(?:le)?\.?\s*|表\s*)(\d{1,3})(?!\d)", re.I)
_EQ_IN_Q = re.compile(r"(?:eq(?:uation)?\.?\s*|式\s*|公式\s*)(\d{1,3})(?!\d)", re.I)
_ALG_IN_Q = re.compile(r"(?:alg(?:orithm)?\.?\s*|算法\s*)(\d{1,3})(?!\d)", re.I)
_MS_KIND_ALIASES = (
    (("开题", "proposal"), "proposal"),
    (("中期", "midterm"), "midterm"),
    (("答辩", "毕业", "defense"), "defense"),
)
_QUOTE_IN_Q = re.compile(r"[「『\"“](.{4,80})[」』\"”]")
_METRIC_IN_Q = re.compile(r"\b\d+\.\d{1,4}\b")
_UUID_IN_Q = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
_RUN_SHORT = re.compile(r"(?:作业|run|复现|调研)[\s#:：-]*([0-9a-f]{8,12})", re.I)
_LATIN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,31}")
_SECTION_ALIASES = (
    (("method", "methods", "methodology", "approach", "方法"), "方法"),
    (("experiment", "experiments", "evaluation", "实验结果", "实验"), "实验"),
    (("introduction", "intro", "引言", "简介"), "引言"),
    (("related work", "related", "相关工作"), "相关工作"),
    (("conclusion", "conclusions", "讨论", "结论"), "结论"),
    (("abstract", "摘要"), "摘要"),
)


def _is_distinct_name(name: str, *, min_len: int = 2) -> bool:
    token = (name or "").strip().lower()
    if not token or len(token) < min_len:
        return False
    return token not in _NAME_STOP


def _mentioned(asked: str, name: str, *, min_len: int = 2) -> bool:
    token = (name or "").strip().lower()
    if not _is_distinct_name(token, min_len=min_len):
        return False
    hay = asked or ""
    if token in hay:
        return True
    # 长题名才允许用前缀，避免 8 字方向名误咬半句。
    return len(token) > 12 and token[:12] in hay


def _best_named(asked: str, rows, name_of, *, prefer=None, min_len: int = 2):
    """问句里点到的对象优先；点名失败时才用 prefer（如失败作业）。"""
    named = []
    for row in rows:
        name = (name_of(row) or "").strip()
        score = 0
        if _mentioned(asked, name, min_len=min_len):
            score = min(len(name), 32)
        if score:
            if prefer and prefer(row):
                score += 10
            named.append((score, row))
    if named:
        named.sort(key=lambda x: -x[0])
        return named[0][1]
    if prefer is None:
        return None
    for row in rows:
        if prefer(row):
            return row
    return None


def _norm_doi(raw: str) -> str:
    s = (raw or "").strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "doi:"):
        if s.startswith(p):
            s = s[len(p) :]
    return s.rstrip(").,;] ")


def _norm_arxiv(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = s.replace("https://arxiv.org/abs/", "").replace("http://arxiv.org/abs/", "")
    s = s.replace("https://arxiv.org/pdf/", "").replace("arxiv:", "")
    s = re.sub(r"v\d+$", "", s)
    return s.strip()


def _looks_arxiv(raw: str) -> bool:
    """YYMM.xxxxx：月份必须 01–12，避免把 2017.1234 / 版本号当成 arXiv。"""
    s = _norm_arxiv(raw)
    m = re.fullmatch(r"(\d{4})\.(\d{4,5})", s)
    if m is None:
        return False
    yy, mm = divmod(int(m.group(1)), 100)
    return 7 <= yy <= 30 and 1 <= mm <= 12


def _ids_in_question(asked: str) -> tuple[list[str], list[str], list[str], list[str]]:
    text = asked or ""
    dois = [_norm_doi(m.group(0)) for m in _DOI_IN_Q.finditer(text) if _norm_doi(m.group(0))]
    arxivs: list[str] = []
    for m in _ARXIV_IN_Q.finditer(text):
        aid = _norm_arxiv(m.group(1) or "")
        if aid and _looks_arxiv(aid) and aid not in arxivs:
            arxivs.append(aid)
    pmids = [m.group(1) for m in _PMID_IN_Q.finditer(text) if m.group(1)]
    cnkis = [_norm_doi(m.group(1)) for m in _CNKI_IN_Q.finditer(text) if m.group(1)]
    return dois, arxivs, pmids, cnkis


def _strip_ids(asked: str) -> str:
    text = _DOI_IN_Q.sub(" ", asked or "")
    text = _ARXIV_IN_Q.sub(" ", text)
    text = _PMID_IN_Q.sub(" ", text)
    return _CNKI_IN_Q.sub(" ", text)


def _citekey_in_asked(asked: str, key: str) -> bool:
    token = (key or "").strip().lower()
    if not _is_distinct_name(token, min_len=3):
        return False
    hay = asked or ""
    if all(ord(c) < 128 for c in token):
        return re.search(rf"\b{re.escape(token)}\b", hay, re.I) is not None
    return token in hay


def _lab_citekeys(ctx: ToolContext) -> dict[str, str]:
    extra = ctx.extra
    cached = extra.get("_fs_citekeys") if extra is not None else None
    if isinstance(cached, dict):
        return cached
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation

    rows = (
        ctx.db.query(ManuscriptCitation.citation_key, ManuscriptCitation.literature_item_id)
        .join(Manuscript, Manuscript.id == ManuscriptCitation.manuscript_id)
        .filter(Manuscript.lab_id == ctx.lab_id, Manuscript.deleted_at.is_(None))
        .all()
    )
    out: dict[str, str] = {}
    for key, iid in rows:
        k = (key or "").strip().lower()
        if _is_distinct_name(k, min_len=3) and iid:
            out.setdefault(k, iid)
    if extra is not None:
        extra["_fs_citekeys"] = out
    return out


def _years_in_question(asked: str) -> set[int]:
    return {int(y) for y in _YEAR_IN_Q.findall(_strip_ids(asked))}


def _page_in_question(asked: str) -> int | None:
    """只认「第 N 页 / 第 N-M 页 / p.N」，不认「第几页」。超出常规页数不当页码。"""
    text = asked or ""
    rng = _PAGE_RANGE.search(text)
    if rng:
        n = int(rng.group(1))
        return n if 1 <= n <= 400 else None
    m = _PAGE_CN.search(text)
    if m is None:
        m = _PAGE_EN.search(text)
    if m is None:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 400 else None


def _section_hit(asked: str, sections: list) -> dict | None:
    """问句点到的章节。章节名可以用「方法」这类停用词，不走人名停用表。"""
    q = (asked or "").lower()
    if not q or not sections:
        return None
    idx = _SECTION_NO.search(q)
    if idx:
        n = int(idx.group(1))
        if 1 <= n <= len(sections) and isinstance(sections[n - 1], dict):
            return sections[n - 1]
    named = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        if not title:
            continue
        low = title.lower()
        score = 0
        if len(title) >= 4 and title.lower() in q:
            score = min(len(title), 24)
        for aliases, _label in _SECTION_ALIASES:
            if any(a in q for a in aliases) and any(a in low for a in aliases):
                score = max(score, 12)
        if score:
            named.append((score, s))
    if not named:
        return None
    named.sort(key=lambda x: -x[0])
    return named[0][1]


def _num_label_hit(asked: str, items: list, cre: re.Pattern, aliases: tuple[str, ...]) -> dict | None:
    """问句点到的图/表/公式/算法号，不把「图神经网络」当成图号。"""
    q = asked or ""
    if not q or not items:
        return None
    m = cre.search(q)
    if m is None:
        return None
    n = int(m.group(1))
    if n < 1 or n > 200:
        return None
    typed = re.compile(rf"(?:{'|'.join(aliases)})\s*\.?\s*{n}(?!\d)", re.I)
    fallback = None
    for f in items:
        if not isinstance(f, dict):
            continue
        blob = f"{f.get('label') or ''} {f.get('caption') or ''} {f.get('title') or ''}"
        if typed.search(blob):
            return f
        if fallback is None and re.search(rf"\b{n}\b", str(f.get("label") or "")):
            fallback = f
    return fallback


def _figure_hit(asked: str, figures: list) -> dict | None:
    return _num_label_hit(asked, figures, _FIG_IN_Q, ("fig(?:ure)?", "图")) or _num_label_hit(
        asked, figures, _TAB_IN_Q, ("tab(?:le)?", "表")
    )


def _equation_hit(asked: str, items: list) -> dict | None:
    return _num_label_hit(asked, items, _EQ_IN_Q, ("eq(?:uation)?", "式", "公式"))


def _algorithm_hit(asked: str, items: list) -> dict | None:
    return _num_label_hit(asked, items, _ALG_IN_Q, ("alg(?:orithm)?", "算法"))


def _needles_in_question(asked: str) -> list[str]:
    """问句里的引文片段或小数指标。先剥 DOI / arXiv，避免把 1606.04615 当页内数字。"""
    text = _strip_ids(asked or "")
    out: list[str] = []
    for m in _QUOTE_IN_Q.finditer(text):
        frag = (m.group(1) or "").strip()
        if len(frag) >= 4:
            out.append(frag)
    for m in _METRIC_IN_Q.finditer(text):
        raw = m.group(0)
        if _looks_arxiv(raw):
            continue
        if raw not in out:
            out.append(raw)
    return out


def _page_from_needles(db, item: LiteratureItem, asked: str) -> int | None:
    needles = _needles_in_question(asked)
    if not needles or item is None:
        return None
    from fengsuan.app.services.pagecache import item_pages

    pages, _src = item_pages(db, item, parse_if_missing=False)
    if not pages:
        return None
    for needle in needles:
        low = needle.lower()
        for i, body in enumerate(pages, 1):
            if low and low in (body or "").lower():
                return i if 1 <= i <= 400 else None
    return None


def _author_tokens(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for a in raw:
        name = str(a or "").strip()
        if not name:
            continue
        out.append(name)
        parts = [p for p in name.replace(",", " ").split() if p]
        if parts:
            out.append(parts[-1])
    return out


def _lit_match_score(asked: str, item: LiteratureItem, *, citekeys: dict[str, str] | None = None) -> int:
    if not asked:
        return 0
    score = 0
    dois, arxivs, pmids, cnkis = _ids_in_question(asked)
    item_doi = _norm_doi(getattr(item, "doi", None) or "")
    if item_doi and any(item_doi == d or item_doi in d or d in item_doi for d in dois):
        score += 40
    item_ax = _norm_arxiv(getattr(item, "arxiv_id", None) or "")
    if item_ax and any(item_ax == a or item_ax in a or a in item_ax for a in arxivs):
        score += 40
    elif arxivs and item_doi and any(a in item_doi for a in arxivs):
        score += 40
    elif item_ax and any(item_ax in d for d in dois):
        score += 40
    item_pm = str(getattr(item, "pubmed_id", None) or "").strip()
    if item_pm and item_pm in pmids:
        score += 40
    item_cn = str(getattr(item, "cnki_id", None) or "").strip().lower()
    if item_cn and any(item_cn == c or item_cn in c or c in item_cn for c in cnkis):
        score += 40
    if citekeys:
        for key, iid in citekeys.items():
            if iid == item.id and _citekey_in_asked(asked, key):
                score += 36
                break
    title = item.title or ""
    if _mentioned(asked, title, min_len=4):
        score += min(len(title), 32)
        # 几千篇里前 12 个字相同的题名很多，整题都对上的要压过只对上开头的。
        if len(title.strip()) > 12 and title.strip().lower() in asked:
            score += 16
    else:
        for w in _LATIN_WORD.findall(title):
            if _is_distinct_name(w, min_len=5) and w.lower() in asked:
                score += min(len(w), 12)
                break
    year = item.publish_year
    if year and year in _years_in_question(asked):
        score += 8
    venue = (item.venue or "").strip()
    if venue and _mentioned(asked, venue, min_len=3):
        score += min(len(venue), 16)
    authors = _author_tokens(getattr(item, "authors_json", None) or "[]")
    for a in authors:
        min_len = 3 if all(ord(c) < 128 for c in a) else 2
        if _mentioned(asked, a, min_len=min_len):
            score += min(len(a), 16)
            break
    return score


def _lit_candidates(ctx: ToolContext, asked: str) -> list:
    """点名候选：最近 60 篇，加上编号或题名词对得上的老篇。几千篇时点名不会只看最近的。"""
    from sqlalchemy import func, or_

    from fengsuan.app.services.search_index import search_terms

    base = ctx.db.query(LiteratureItem).filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
    rows = base.order_by(LiteratureItem.updated_at.desc()).limit(60).all()
    seen = {r.id for r in rows}
    for quoted in _QUOTED_TITLE.findall(asked or "")[:3]:
        for r in base.filter(func.lower(LiteratureItem.title) == quoted.strip().lower()).limit(5).all():
            if r.id not in seen:
                rows.append(r)
                seen.add(r.id)
    conds = []
    dois, arxivs, pmids, _cnkis = _ids_in_question(asked)
    for d in dois[:3]:
        conds.append(LiteratureItem.doi.ilike(f"%{d}%"))
    for a in arxivs[:3]:
        conds.append(LiteratureItem.arxiv_id.ilike(f"%{a}%"))
        conds.append(LiteratureItem.doi.ilike(f"%{a}%"))
    for p in pmids[:3]:
        conds.append(LiteratureItem.pubmed_id == p)
    for term in search_terms(asked, cap=6):
        if len(term) >= 4:
            conds.append(LiteratureItem.title.ilike(f"%{term}%"))
    if conds:
        for r in base.filter(or_(*conds)).order_by(LiteratureItem.updated_at.desc()).limit(120).all():
            if r.id not in seen:
                rows.append(r)
                seen.add(r.id)
    return rows


def _lit_named_in_question(ctx: ToolContext, *, limit: int = 8, min_len: int = 4) -> list:
    asked = _ctx_question(ctx)
    if not asked:
        return []
    rows = _lit_candidates(ctx, asked)
    keys = _lab_citekeys(ctx)
    scored = [(s, r) for r in rows if (s := _lit_match_score(asked, r, citekeys=keys)) > 0]
    scored.sort(key=lambda x: -x[0])
    return [r for _s, r in scored[:limit]]


def _resolve_literature_item(ctx: ToolContext, args: dict | None = None):
    """工具参数 > 问句点名（DOI / arXiv / PMID / 引用键 / 作者题名）> 当前篇。对不上就空，不 400。"""
    raw = args or {}
    wanted = str(raw.get("item_id") or "").strip()
    if wanted:
        if ctx.scope == "paper" and ctx.item_id:
            return _item(ctx, ctx.item_id)
        return _item(ctx, wanted)
    asked = _ctx_question(ctx)
    if asked:
        rows = _lit_candidates(ctx, asked)
        keys = _lab_citekeys(ctx)
        scored = [(s, r) for r in rows if (s := _lit_match_score(asked, r, citekeys=keys)) > 0]
        if scored:
            scored.sort(key=lambda x: -x[0])
            top = scored[0][0]
            winners = [r for s, r in scored if s == top]
            if len(winners) == 1:
                return winners[0]
            if ctx.item_id:
                for row in winners:
                    if row.id == ctx.item_id:
                        return row
            return None
    if ctx.item_id:
        return _item(ctx, ctx.item_id)
    return None


def _resolve_page_no(ctx: ToolContext, item: LiteratureItem | None, args: dict | None = None) -> int | None:
    raw = args or {}
    try:
        n = int(raw.get("page_no") or 0)
    except (TypeError, ValueError):
        n = 0
    if 1 <= n <= 400:
        return n
    asked = _ctx_question(ctx)
    n = _page_in_question(asked)
    if n:
        return n
    try:
        extra = int((ctx.extra or {}).get("page_no") or 0)
    except (TypeError, ValueError):
        extra = 0
    extra = extra if 1 <= extra <= 400 else 0

    def _hit_page(hit) -> int | None:
        if not hit:
            return None
        try:
            page = int(hit.get("page") or 0)
        except (TypeError, ValueError):
            return None
        return page if 1 <= page <= 400 else None

    st = None
    if item is not None:
        from fengsuan.app.services.pagecache import item_structure

        st = item_structure(ctx.db, item.id)
        page = _hit_page(_figure_hit(asked, st.get("figures") or []))
        if page:
            return page
        page = _hit_page(_equation_hit(asked, st.get("equations") or []))
        if page:
            return page
        page = _hit_page(_algorithm_hit(asked, st.get("algorithms") or []))
        if page:
            return page
        idx = _SECTION_NO.search(asked or "")
        secs = st.get("sections") or []
        if idx:
            i = int(idx.group(1))
            if 1 <= i <= len(secs) and isinstance(secs[i - 1], dict):
                page = _hit_page(secs[i - 1])
                if page:
                    return page
        located = _page_from_needles(ctx.db, item, asked)
        if located:
            return located
    if extra and any(tok in (asked or "") for tok in ("这一页", "本页", "这页", "当前页")):
        return extra
    if item is not None and st is not None:
        page = _hit_page(_section_hit(asked, st.get("sections") or []))
        if page:
            return page
    if extra:
        return extra
    return None


def _literature_miss(ctx: ToolContext) -> ToolResult:
    href = f"/labs/{ctx.lab_id}/literature"
    nxt = {
        "title": "没有对上这篇",
        "text": "打开一篇再问，或先点名题名、DOI、arXiv、PMID 或稿里的引用键。",
        "steps": [{"key": "library", "label": "打开文献库", "href": href}],
    }
    return ToolResult(
        text="没有对上这篇文献。先点名题名、作者、年份、刊物、DOI、arXiv、PMID 或稿里的引用键，或打开一篇再问。",
        sources=[
            {
                "id": f"litmiss:{ctx.lab_id}",
                "kind": "literature",
                "title": "没有对上这篇",
                "page": None,
                "href": href,
                "excerpt": "不要凭记忆编书目或页正文。",
            }
        ],
        data={"empty": True, "miss": True, "empty_next": nxt},
    )


def _run_ids_in_question(asked: str) -> list[str]:
    """问句里的作业 UUID 或「作业/run + 前 8 位」，不把年份或 arXiv 当作业号。"""
    text = asked or ""
    out: list[str] = []
    seen: set[str] = set()
    for raw in [m.group(0) for m in _UUID_IN_Q.finditer(text)] + [m.group(1) for m in _RUN_SHORT.finditer(text)]:
        token = (raw or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _row_matches_run_id(row, tokens: list[str]) -> bool:
    rid = (getattr(row, "id", None) or "").lower()
    if not rid or not tokens:
        return False
    return any(rid == t or rid.startswith(t) or (len(t) >= 8 and t in rid) for t in tokens)


def _glossary_needles(asked: str) -> list[str]:
    """问句里像术语的词：引号片段或拉丁缩写。整句「组里术语怎么译」不算。"""
    out: list[str] = []
    for q in _QUOTE_IN_Q.findall(asked or ""):
        token = (q or "").strip()
        if _is_distinct_name(token, min_len=2) and not any(p in token for p in ("术语", "怎么译", "锁定不译")):
            out.append(token)
    for m in _LATIN_TERM.finditer(asked or ""):
        token = m.group(0)
        if _is_distinct_name(token, min_len=2):
            out.append(token)
    seen: set[str] = set()
    clean: list[str] = []
    for token in out:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(token)
    return clean


def _looks_term_lookup(asked: str) -> bool:
    return any(tok in (asked or "") for tok in ("怎么译", "这个词", "译成", "译名"))


def _sanitize_glossary_src(raw: str) -> str:
    token = (raw or "").strip()
    if not token or len(token) > 24:
        return ""
    if any(p in token for p in ("术语", "怎么译", "锁定不译", "组里")):
        return ""
    return token.lower() if _is_distinct_name(token, min_len=2) else ""


def _catalog_miss(ctx: ToolContext, *, kind: str, title: str, text: str, href: str, step_label: str) -> ToolResult:
    nxt = {
        "title": title,
        "text": text,
        "steps": [{"key": kind, "label": step_label, "href": href}],
    }
    return ToolResult(
        text=text,
        sources=[
            {
                "id": f"{kind}miss:{ctx.lab_id}",
                "kind": kind,
                "title": title,
                "page": None,
                "href": href,
                "excerpt": _clip(text, 240),
            }
        ],
        data={"empty": True, "miss": True, "empty_next": nxt},
    )


def _named_run_in_question(ctx: ToolContext):
    """问句点到的文献调研 / 复现作业。"""
    from fengsuan.app.models.runs import ResearchRun, ReproduceRun

    asked = _ctx_question(ctx)
    if not asked:
        return None
    mem = _member(ctx)
    teacher = (mem.role or "") == "teacher"
    rq = ctx.db.query(ResearchRun).filter(ResearchRun.lab_id == ctx.lab_id)
    pq = ctx.db.query(ReproduceRun).filter(ReproduceRun.lab_id == ctx.lab_id)
    if not teacher:
        rq = rq.filter(ResearchRun.requester_id == ctx.user_id)
        pq = pq.filter(ReproduceRun.requester_id == ctx.user_id)
    research = rq.order_by(ResearchRun.updated_at.desc()).limit(24).all()
    named = _best_named(asked, research, lambda r: r.question or "", min_len=4)
    if named is not None:
        return named
    reproduce = pq.order_by(ReproduceRun.updated_at.desc()).limit(24).all()
    lit_ids = [r.literature_item_id for r in reproduce if r.literature_item_id]
    titles = {
        x.id: x.title
        for x in (
            ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
            if lit_ids
            else []
        )
    }
    return _best_named(
        asked,
        reproduce,
        lambda r: titles.get(r.literature_item_id) or r.current_stage or "",
        min_len=4,
    )


def _asks_mine(asked: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in (asked or "") for tok in tokens)


def _due_label(due_at, *, done: bool = False) -> str:
    if due_at is None or due_at == "":
        return "未定截止"
    from datetime import datetime

    from fengsuan.app.db.base import utcnow

    due = due_at
    if isinstance(due, str):
        raw = due.replace("Z", "+00:00")
        try:
            due = datetime.fromisoformat(raw)
        except ValueError:
            return f"截止：{due[:10]}"
    now = utcnow()
    try:
        day = due.date().isoformat()
    except Exception:
        day = str(due)[:10]
    if done:
        return f"截止：{day}"
    try:
        if getattr(due, "tzinfo", None) is None and getattr(now, "tzinfo", None) is not None:
            due = due.replace(tzinfo=now.tzinfo)
        overdue = due < now
    except TypeError:
        overdue = False
    return f"{'已过期｜' if overdue else ''}截止：{day}"


def _lookup_member_id(ctx: ToolContext, name: str) -> str:
    token = (name or "").strip().lower()
    if not token:
        return ""
    if token in {"我", "本人", "我自己", "我的档案"}:
        return ctx.user_id
    rows = (
        ctx.db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == ctx.lab_id)
        .all()
    )
    exact = []
    for _m, u in rows:
        disp = (u.display_name or "").strip()
        email = (u.email or "").split("@", 1)[0]
        if disp.lower() == token or email.lower() == token:
            exact.append(u.id)
    return exact[0] if len(exact) == 1 else ""


def _member_named_in_question(ctx: ToolContext) -> str:
    """问句里点到的组员。重名且分数相同则不猜。"""
    asked = _ctx_question(ctx)
    if not asked:
        return ""
    rows = (
        ctx.db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == ctx.lab_id)
        .all()
    )
    named: list[tuple[int, str]] = []
    for _m, u in rows:
        disp = (u.display_name or "").strip()
        email = (u.email or "").split("@", 1)[0]
        score = 0
        if _mentioned(asked, disp, min_len=2):
            score = max(score, min(len(disp), 24))
        if _is_distinct_name(email, min_len=3) and _mentioned(asked, email, min_len=3):
            score = max(score, min(len(email), 16))
        if score:
            named.append((score, u.id))
    if not named:
        return ""
    named.sort(key=lambda x: -x[0])
    if len(named) > 1 and named[0][0] == named[1][0] and named[0][1] != named[1][1]:
        return ""
    return named[0][1]


def _direction_named_in_question(ctx: ToolContext) -> str:
    asked = _ctx_question(ctx)
    if not asked:
        return ""
    rows = (
        ctx.db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == ctx.lab_id, ResearchDirection.deleted_at.is_(None))
        .all()
    )
    def _dir_min(name: str) -> int:
        token = (name or "").strip()
        if token and all(ord(c) < 128 for c in token):
            return 4
        return 2

    named = []
    for d in rows:
        name = d.name or ""
        min_len = _dir_min(name)
        if _mentioned(asked, name, min_len=min_len):
            named.append((min(len(name), 32), d))
    if not named:
        return ""
    named.sort(key=lambda x: -x[0])
    if len(named) > 1 and named[0][0] == named[1][0] and named[0][1].id != named[1][1].id:
        return ""
    return named[0][1].id


def _direction_id(ctx: ToolContext, args: dict | None = None, *, inherit: str = "chip") -> str:
    """方向从哪来：工具参数 > 问句点名 > 芯片 > 入门主方向。never 仍认问句点名。"""
    raw = (args or {}).get("direction_id") if args else None
    if raw:
        return str(raw).strip()
    named = _direction_named_in_question(ctx)
    if named:
        return named
    if inherit == "never":
        return ""
    chip = str(ctx.direction_id or "").strip()
    if inherit == "chip" or chip:
        return chip
    if inherit == "default":
        return str((ctx.extra or {}).get("default_direction_id") or "").strip()
    return ""


def _tool_scoped(ctx: ToolContext, fetch, want: str) -> tuple[list, str]:
    """入门追问：该方向没有对象时回退全组，与入门卡同一口径。全库问答选了方向不回退。

    无效 / 已归档方向不得回退成全组，否则模型胡写一个 id 会漏出整库。
    """
    from fengsuan.app.services.directions import live_direction
    from fengsuan.app.services.onboarding import _scoped_or_all

    if want and live_direction(ctx.db, ctx.lab_id, want) is None:
        return [], "invalid"
    if ctx.scope == "onboarding":
        return _scoped_or_all(fetch, want)
    return fetch(want) or [], ("direction" if want else "lab")


# ---------------------------------------------------------------- T1 检索类


@tool(
    "search_lab_kb",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="检索本实验室知识库（论文、方向说明、组会纪要、入门手册）。返回带页码与可点链接的片段，回答里的每句话都应指回某个片段。",
    params={
        "properties": {
            "query": {"type": "string", "description": "检索词，用原文语言更准"},
            "top_k": {"type": "integer", "description": "返回片段数，默认 6，最多 12"},
            "direction_id": {"type": "string", "description": "只搜该方向（含小课题）已入库文档"},
        },
        "required": ["query"],
    },
    stub_keywords=("知识库", "组会", "纪要", "手册", "入门", "资料", "kb"),
)
def search_lab_kb(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.kb import query_kb

    q = str(args.get("query") or "").strip()
    if not q:
        return ToolResult(error="检索词为空")
    top_k = max(1, min(12, int(args.get("top_k") or 6)))
    kinds = args.get("source_kinds") if isinstance(args.get("source_kinds"), list) else None
    if not kinds and ctx.scope == "onboarding":
        kinds = ["lab_intro", "meeting_minutes", "manuscript"]
    hits, _fallback = query_kb(
        ctx.db,
        ctx.lab_id,
        q,
        top_k=top_k,
        user_id=ctx.user_id,
        direction_id=_direction_id(ctx, args, inherit="never" if ctx.scope == "onboarding" else "chip") or None,
        source_kinds=kinds,
    )
    sources = [
        {
            "id": f"kb:{h.get('chunk_id')}",
            "kind": "kb_chunk",
            "title": h.get("filename") or h.get("heading_path") or "知识库片段",
            "page": h.get("page_no"),
            "href": h.get("open_href") or "",
            "item_id": h.get("literature_item_id") or "",
            "excerpt": _clip(h.get("content") or ""),
        }
        for h in hits
    ]
    return ToolResult(
        text=f"知识库命中 {len(sources)} 段" if sources else "知识库没有命中，可能还没入库",
        sources=sources,
        data={"retrieval_mode": (hits[0].get("retrieval_mode") if hits else "")},
    )


@tool(
    "search_lab_literature",
    scopes=["paper", "lab", "onboarding", "writing"],
    description="在本实验室文献库里按标题与摘要找篇目。想知道「组里读过哪些相关文献」时用这个，而不是去开放源检索。query 传关键词（如 graph convolution），不要把整句问题传进来。",
    params={
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "direction_id": {"type": "string", "description": "只搜挂在该方向（含小课题）的篇目"},
            "lab_wide": {"type": "boolean", "description": "为真时搜全组，不限当前方向芯片。问句点名了某个方向时仍只搜那个方向。"},
        },
        "required": ["query"],
    },
    stub_keywords=("文献", "篇目", "读过", "库里", "相关工作", "综述"),
)
def search_lab_literature(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.literature import search_local

    q = str(args.get("query") or "").strip()
    if not q:
        return ToolResult(error="检索词为空")
    limit = max(1, min(20, int(args.get("limit") or 8)))
    wide = bool(args.get("lab_wide"))
    inherit = "never" if wide else ("default" if ctx.scope == "onboarding" else "chip")
    want = _direction_id(ctx, args, inherit=inherit)
    hits, _scope = _tool_scoped(
        ctx, lambda d: search_local(ctx.db, ctx.lab_id, q, direction_id=d or None, limit=limit), want
    )
    sources = []
    for h in hits:
        authors = h.get("authors") or []
        if isinstance(authors, list):
            author_text = "、".join(str(a) for a in authors if a)
        else:
            author_text = str(authors or "")
        sources.append(
            {
                "id": f"lit:{h.get('id')}",
                "kind": "literature",
                "title": h.get("title") or "",
                "authors": author_text,
                "year": h.get("publish_year"),
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{h.get('id')}",
                "item_id": h.get("id") or "",
                "excerpt": _clip(
                    f"{h.get('publish_year') or '未登记年份'}｜{h.get('venue') or '未登记期刊/会议'}｜"
                    f"{author_text}｜{h.get('abstract') or h.get('title') or ''}",
                    400,
                ),
            }
        )
    return ToolResult(text=f"文献库命中 {len(sources)} 篇", sources=sources)


@tool(
    "search_open_sources",
    scopes=["lab", "writing"],
    description="检索 arXiv / PubMed 等开放学术源。只在实验室内部找不到时才用，且必须说明是外部结果、尚未入库。",
    params={
        "properties": {
            "query": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query"],
    },
    stub_keywords=("arxiv", "pubmed", "最新进展", "外部", "开放源"),
)
def search_open_sources(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    q = str(args.get("query") or "").strip()
    if not q:
        return ToolResult(error="检索词为空")
    raw = args.get("sources")
    sources_in = [str(s) for s in raw] if isinstance(raw, list) and raw else ["arxiv"]
    try:
        from fengsuan.app.services.literature import cached_search_sources

        hits, cached = cached_search_sources(q, sources_in, max_per_source=5)
    except Exception as exc:  # 外部源不可用不该让整段回答失败
        return ToolResult(error=f"开放源不可用：{str(exc)[:120]}")
    out = []
    for h in hits[:10]:
        key = h.get("doi") or h.get("arxiv_id") or h.get("url") or h.get("title") or ""
        out.append(
            {
                "id": f"ext:{key}",
                "kind": "external",
                "title": h.get("title") or "",
                "page": None,
                "href": h.get("url") or "",
                "excerpt": _clip(h.get("abstract") or "", 400),
            }
        )
    if not out:
        return ToolResult(
            text="开放源没有命中（尚未入库，不要编外部书目）。",
            data={"empty": True, "cached": cached},
        )
    note = "回放缓存，尚未入库" if cached else "尚未入库"
    return ToolResult(text=f"开放源命中 {len(out)} 篇（{note}）", sources=out, data={"cached": cached})


# ---------------------------------------------------------------- T2 事实类


@tool(
    "get_direction_tree",
    scopes=["lab", "onboarding", "writing"],
    description="取本实验室的研究方向两级树（大方向与子课题），含负责人、关键词、文献与手稿数量。回答「这个组在做什么」必须先调这个。",
    params={"properties": {}, "required": []},
    stub_keywords=("方向", "在做什么", "课题", "研究什么", "组里做"),
)
def get_direction_tree(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.directions import member_direction_maps, owner_names

    rows = (
        ctx.db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == ctx.lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    kids: dict[str, list[str]] = {}
    for d in rows:
        if d.parent_id:
            kids.setdefault(d.parent_id, []).append(d.id)
    mine = {d.get("id") for d in (member_direction_maps(ctx.db, ctx.lab_id, [ctx.user_id]).get(ctx.user_id) or [])}
    owners = owner_names(ctx.db, [d.owner_user_id for d in rows])
    from sqlalchemy import func

    from fengsuan.app.models.literature import LiteratureDirectionLink
    from fengsuan.app.models.manuscript import ManuscriptDirectionLink

    dir_ids = [d.id for d in rows]
    lit_n: dict[str, int] = {}
    ms_n: dict[str, int] = {}
    if dir_ids:
        lit_n = {
            did: int(n)
            for did, n in ctx.db.query(LiteratureDirectionLink.direction_id, func.count())
            .filter(LiteratureDirectionLink.direction_id.in_(dir_ids))
            .group_by(LiteratureDirectionLink.direction_id)
            .all()
        }
        ms_n = {
            did: int(n)
            for did, n in ctx.db.query(ManuscriptDirectionLink.direction_id, func.count())
            .filter(ManuscriptDirectionLink.direction_id.in_(dir_ids))
            .group_by(ManuscriptDirectionLink.direction_id)
            .all()
        }
    named_id = _direction_named_in_question(ctx)
    sources = []
    for d in rows:
        kw = []
        try:
            kw = json.loads(d.keywords_json or "[]")
        except json.JSONDecodeError:
            kw = []
        mine_mark = "你的方向。" if d.id in mine else ""
        pin_mark = "问句点名。" if named_id and d.id == named_id else ""
        sources.append(
            {
                "id": f"dir:{d.id}",
                "kind": "direction",
                "title": d.name,
                "page": None,
                "href": f"/labs/{ctx.lab_id}/directions/{d.id}",
                "excerpt": _clip(
                    f"{pin_mark}{mine_mark}{d.description or ''} 关键词：{'、'.join(str(k) for k in kw[:8])} "
                    f"负责人：{owners.get(d.owner_user_id) or ''} 子课题：{len(kids.get(d.id) or [])} "
                    f"文献 {lit_n.get(d.id) or 0} 篇｜手稿 {ms_n.get(d.id) or 0} 篇",
                    400,
                ),
                "parent_id": d.parent_id or "",
                "status": d.status or "",
                "_pin": 0 if named_id and d.id == named_id else 1,
            }
        )
    sources.sort(key=lambda s: s.get("_pin") or 0)
    for s in sources:
        s.pop("_pin", None)
    extra = ""
    if sources:
        from fengsuan.app.services.agent.understand import (
            _extra_direction_query,
            direction_followup_query,
            understand,
        )

        turn = (ctx.extra or {}).get("turn") if isinstance((ctx.extra or {}).get("turn"), dict) else {}
        if turn.get("from_model"):
            asked = {
                "intent": turn.get("intent") or "",
                "topic": turn.get("topic_zh") or "",
                "person": turn.get("person_name") or "",
            }
        else:
            asked = understand(_ctx_question(ctx))
        topic = str(asked.get("topic") or "")
        person = str(asked.get("person") or "")
        keyword = ""
        if asked.get("intent") in {"frontier", "field_people", "web_person"}:
            for d in rows:
                kws = []
                try:
                    kws = json.loads(d.keywords_json or "[]")
                except json.JSONDecodeError:
                    kws = []
                keyword = _extra_direction_query(topic, d.name or "", d.description or "", [str(k) for k in kws])
                if keyword:
                    break
        extra = direction_followup_query(str(asked.get("intent") or ""), person, keyword)
    if not sources:
        return ToolResult(text="这个组还没有研究方向。", sources=[])
    return ToolResult(
        text=f"组里有 {len(sources)} 个方向",
        sources=sources,
        data={"extra_queries": [extra]} if extra else {},
    )


@tool(
    "get_lab_members",
    scopes=["lab", "onboarding"],
    description="取实验室成员名册：角色（导师 / 学生）、职称年级、挂靠方向。本人导师与同方向的人排在前面并标出，但不藏其他人。回答「谁做什么、谁能帮我」用这个，不要凭检索猜。",
    params={
        "properties": {"direction_id": {"type": "string", "description": "只看某个方向的成员"}},
        "required": [],
    },
    stub_keywords=("成员", "谁", "导师", "同学", "师兄", "师姐", "分工", "负责", "找谁", "帮我"),
)
def get_lab_members(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.directions import (
        member_direction_maps,
        membership_status,
        resolve_direction_scope,
    )
    from fengsuan.app.services.profiles import _list

    rows = (
        ctx.db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == ctx.lab_id)
        .all()
    )
    dir_map = member_direction_maps(ctx.db, ctx.lab_id, [u.id for _m, u in rows])
    advisor_id = ""
    my_dirs: set[str] = set()
    for m, u in rows:
        if u.id == ctx.user_id:
            advisor_id = m.advisor_user_id or ""
            my_dirs = {str(d.get("id") or "") for d in (dir_map.get(u.id) or []) if d.get("id")}
            break
    unique_dirs = set(my_dirs)
    for dirs in dir_map.values():
        unique_dirs.update(str(d.get("id") or "") for d in dirs if d.get("id"))
    expanded = {did: set(resolve_direction_scope(ctx.db, ctx.lab_id, did)) for did in unique_dirs if did}
    my_scope: set[str] = set()
    for did in my_dirs:
        my_scope |= expanded.get(did) or set()
    want_dir = _direction_id(ctx, args, inherit="never")
    want_scope = set(resolve_direction_scope(ctx.db, ctx.lab_id, want_dir) or []) if want_dir else set()
    sources = []
    for m, u in rows:
        dirs = dir_map.get(u.id) or []
        their_ids = {str(d.get("id") or "") for d in dirs if d.get("id")}
        if want_dir and not (their_ids & want_scope):
            continue
        names = "、".join(str(d.get("name") or "") for d in dirs[:4])
        interests = _list(getattr(m, "interests_json", None))
        role_cn = "导师" if m.role == "teacher" else "学生"
        tags = []
        if u.id == advisor_id:
            tags.append("我的导师")
            bucket = 0
        elif m.role == "teacher":
            tags.append("导师")
            bucket = 1
        elif u.id == ctx.user_id:
            tags.append("本人")
            bucket = 4
        elif my_scope and ({sid for did in their_ids for sid in (expanded.get(did) or set())} & my_scope):
            tags.append("同方向")
            bucket = 2
        else:
            bucket = 3
        tag = ("｜".join(tags) + "｜") if tags else ""
        sources.append(
            {
                "id": f"member:{u.id}",
                "kind": "member",
                "title": u.display_name or u.email,
                "page": None,
                "href": f"/labs/{ctx.lab_id}/cockpit/members/{u.id}",
                "excerpt": _clip(
                    f"{tag}{role_cn}｜{m.title or ''}｜方向：{names or '未挂靠'}｜状态：{membership_status(m)}"
                    + (f"｜研究兴趣：{'、'.join(interests)}" if interests else ""),
                    400,
                ),
                "role": m.role,
                "member_title": m.title or "",
                "member_dirs": names,
                "_rank": (bucket, (u.display_name or u.email or "")),
            }
        )
    named_uid = _member_named_in_question(ctx)
    if named_uid:
        for s in sources:
            if str(s.get("id") or "").endswith(named_uid):
                s["excerpt"] = _clip(f"问句点名｜{s.get('excerpt') or ''}", 400)
                s["_rank"] = (-1, s["_rank"][1] if isinstance(s.get("_rank"), tuple) else "")
    sources.sort(key=lambda s: s["_rank"])
    for s in sources:
        s.pop("_rank", None)
    return ToolResult(text=f"成员 {len(sources)} 人", sources=sources)


@tool(
    "get_member_profile",
    scopes=["lab", "onboarding"],
    description="取某位成员的档案：自我介绍、研究兴趣、技能、导师、入学与预计毕业时间、主页。可点名或看本人。",
    params={
        "properties": {
            "user_id": {"type": "string"},
            "name": {"type": "string", "description": "成员显示名或邮箱前缀，与 user_id 二选一"},
        },
        "required": [],
    },
    stub_keywords=("档案", "介绍一下", "兴趣", "技能", "简介", "我的档案", "的档案"),
)
def get_member_profile(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.profiles import member_profile

    uid = str(args.get("user_id") or "").strip()
    if not uid and args.get("name"):
        uid = _lookup_member_id(ctx, str(args.get("name") or ""))
    if not uid:
        uid = _member_named_in_question(ctx)
    if not uid and _asks_mine(_ctx_question(ctx), ("我的档案", "本人", "我自己")):
        uid = ctx.user_id
    if not uid:
        return ToolResult(error="请说要看哪位成员的档案，或问「我的档案」")
    prof = member_profile(ctx.db, ctx.lab_id, uid)
    if prof is None:
        return ToolResult(error="这个人不是本实验室成员")
    bits = [
        prof.get("bio") or "",
        "研究兴趣：" + "、".join(prof.get("interests") or []) if prof.get("interests") else "",
        "技能：" + "、".join(prof.get("skills") or []) if prof.get("skills") else "",
        f"导师：{prof.get('advisor_name')}" if prof.get("advisor_name") else "",
        f"在读方向：{'、'.join(d.get('name') or '' for d in prof.get('directions') or [])}",
        ("单位：" + (prof.get("academic") or {}).get("affiliation")) if (prof.get("academic") or {}).get("affiliation") else "",
        ("给助手的说明：" + (prof.get("academic") or {}).get("ai_note")) if (prof.get("academic") or {}).get("ai_note") else "",
        "公开学术身份已确认" if (prof.get("identity") or {}).get("confirmed") else "",
    ]
    read_pages = [
        str(row.get("summary") or "").strip()
        for row in (prof.get("sources_status") or [])
        if isinstance(row, dict) and row.get("state") == "read" and str(row.get("summary") or "").strip()
    ]
    if read_pages:
        bits.append("留给助手的网页：" + "；".join(read_pages[:2]))
    return ToolResult(
        text=prof.get("display_name") or "",
        sources=[
            {
                "id": f"profile:{uid}",
                "kind": "profile",
                "title": prof.get("display_name") or "",
                "page": None,
                "href": prof.get("href") or "",
                "excerpt": _clip("；".join(b for b in bits if b), 600),
            }
        ],
        data=prof,
    )


@tool(
    "get_milestones",
    scopes=["lab", "onboarding"],
    description="取方向下的学位节点与时间安排。回答「接下来要交什么、什么时候」用这个。",
    params={"properties": {"direction_id": {"type": "string"}}, "required": []},
    stub_keywords=("节点", "里程碑", "开题", "中期", "毕业", "时间", "deadline"),
)
def get_milestones(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.onboarding import lab_milestones

    want = _direction_id(ctx, args, inherit="default")
    rows, scope = _tool_scoped(
        ctx, lambda d: lab_milestones(ctx.db, ctx.lab_id, direction_id=d or None, limit=40), want
    )
    asked = _ctx_question(ctx)
    named = _best_named(asked, rows, lambda r: r.get("title") or "", min_len=2)
    if named is None:
        for toks, kind in _MS_KIND_ALIASES:
            if any(tok in asked for tok in toks):
                named = next((r for r in rows if (r.get("kind") or "") == kind), None)
                if named is None:
                    named = next((r for r in rows if any(tok in (r.get("title") or "") for tok in toks)), None)
                if named is not None:
                    break
    if named is not None:
        nid = named.get("id")
        rows = [named] + [r for r in rows if r.get("id") != nid]
    sources = [
        {
            "id": f"ms:{r.get('id')}",
            "kind": "milestone",
            "title": r.get("title") or r.get("kind") or "",
            "page": None,
            "href": r.get("href") or "",
            "excerpt": _clip(
                f"状态：{r.get('status') or ''}｜{_due_label(r.get('due_at'), done=(r.get('status') or '') in {'done', 'passed', 'submitted'})}",
                200,
            ),
        }
        for r in rows
    ]
    if not sources:
        href = f"/labs/{ctx.lab_id}/directions"
        nxt = {
            "title": "还没有学位节点",
            "text": "先在方向上登记开题 / 中期 / 答辩节点。",
            "steps": [{"key": "dirs", "label": "打开方向", "href": href}],
        }
        return ToolResult(
            text=nxt["title"],
            sources=[{"id": f"msmiss:{ctx.lab_id}", "kind": "milestone", "title": nxt["title"], "page": None, "href": href, "excerpt": nxt["text"]}],
            data={"empty": True, "empty_next": nxt},
        )
    return ToolResult(text=f"学位节点 {len(sources)} 个（{scope}）", sources=sources)


@tool(
    "get_my_next_step",
    scopes=["onboarding", "lab"],
    description="取入门卡上那一条下一步：还没选方向、先读哪篇、或文献库还空。回答「我该做什么」用这个，不要另开待办清单。",
    params={"properties": {}, "required": []},
    stub_keywords=("我该做", "该做什么", "下一步做什么", "先做什么", "从哪开始"),
)
def get_my_next_step(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.onboarding import onboarding_next_step

    step = onboarding_next_step(ctx.db, ctx.lab_id, ctx.user_id) or {}
    chips = step.get("steps") or []
    href = (chips[0] or {}).get("href") if chips else ""
    labels = "、".join(str(s.get("label") or "") for s in chips[:2] if s.get("label"))
    return ToolResult(
        text=step.get("title") or "还没有下一步",
        sources=[
            {
                "id": f"next:{ctx.user_id}",
                "kind": "next_step",
                "title": step.get("title") or "",
                "page": None,
                "href": href or f"/labs/{ctx.lab_id}",
                "excerpt": _clip(f"{step.get('text') or ''} {labels}".strip(), 400),
            }
        ],
        data=step,
    )


@tool(
    "get_recent_meetings",
    scopes=["lab", "onboarding"],
    description="取最近组会的时间、题名与已入账的行动项。回答「什么时候开会、组会定了什么」用这个。纪要正文仍检索知识库，不要在这里编纪要。",
    params={"properties": {}, "required": []},
    stub_keywords=("最近组会", "什么时候开", "开会", "例会", "组会时间", "组会", "组会定了", "组会行动"),
)
def get_recent_meetings(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.collab import ActionItem
    from fengsuan.app.services.insight import meeting_list_empty_next
    from fengsuan.app.services.onboarding import recent_meetings

    rows = recent_meetings(ctx.db, ctx.lab_id, limit=12)
    asked = _ctx_question(ctx)
    ids = [r.get("id") for r in rows if r.get("id")]
    actions_by: dict[str, list[str]] = {i: [] for i in ids}
    if ids:
        for a in ctx.db.query(ActionItem).filter(ActionItem.meeting_id.in_(ids)).all():
            if a.meeting_id and a.title and len(actions_by.get(a.meeting_id) or []) < 4:
                actions_by.setdefault(a.meeting_id, []).append(a.title)
    named = _best_named(asked, rows, lambda r: r.get("title") or "")
    if named is None:
        for r in rows:
            if any(_mentioned(asked, t, min_len=4) for t in actions_by.get(r.get("id") or "") or []):
                named = r
                break
    if named is not None:
        nid = named.get("id")
        rows = [named] + [r for r in rows if r.get("id") != nid]
    sources = []
    for r in rows:
        acts = actions_by.get(r.get("id") or "") or []
        act_txt = f"｜行动：{'；'.join(acts)}" if acts else "｜还没有入账行动"
        sources.append(
            {
                "id": f"meet:{r.get('id')}",
                "kind": "meeting",
                "title": r.get("title") or "组会",
                "held_at": (r.get("held_at") or "")[:16],
                "has_minutes": bool(r.get("has_minutes")),
                "actions": "；".join(acts),
                "page": None,
                "href": r.get("href") or "",
                "excerpt": _clip(
                    f"时间：{(r.get('held_at') or '未定')[:16]}｜{'有纪要' if r.get('has_minutes') else '还没有纪要'}"
                    f"{act_txt}。{r.get('summary') or ''}",
                    400,
                ),
            }
        )
    if not sources:
        nxt = meeting_list_empty_next(ctx.lab_id)
        return ToolResult(
            text=nxt.get("title") or "还没有组会。",
            sources=[
                {
                    "id": f"meetings:{ctx.lab_id}",
                    "kind": "meeting",
                    "title": nxt.get("title") or "组会",
                    "page": None,
                    "href": f"/labs/{ctx.lab_id}/meetings",
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    return ToolResult(text=f"最近组会 {len(sources)} 次", sources=sources)


@tool(
    "get_lab_activity",
    scopes=["lab", "onboarding"],
    description="取实验室最近工作日志：谁做了什么、点进现有对象。回答「组里最近干什么了」必须先调这个，不要编造动态，也不要当成排名。",
    params={"properties": {"direction_id": {"type": "string"}, "mine": {"type": "boolean"}}, "required": []},
    stub_keywords=("工作日志", "组里动态", "最近干什么", "谁交了作业", "最近做了什么", "我的周志", "我写的日志"),
)
def get_lab_activity(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.identity import User as U
    from fengsuan.app.services.activity import recent_events

    want = _direction_id(ctx, args, inherit="chip")
    mine = args.get("mine")
    if isinstance(mine, str):
        mine = mine.strip().lower() in {"1", "true", "yes", "self", "me"}
    if mine is None and _asks_mine(
        _ctx_question(ctx), ("我的周志", "我的日志", "我写的日志", "我的工作日志", "我写的周志")
    ):
        mine = True
    named_uid = "" if mine else _member_named_in_question(ctx)
    actor_id = ctx.user_id if mine else (named_uid or None)
    events = recent_events(
        ctx.db,
        ctx.lab_id,
        actor_id=actor_id,
        limit=12,
        direction_id=want or None,
    )
    from fengsuan.app.models.activity import WorkLog

    logs_q = ctx.db.query(WorkLog).filter(WorkLog.lab_id == ctx.lab_id, WorkLog.deleted_at.is_(None))
    if mine:
        logs_q = logs_q.filter(WorkLog.user_id == ctx.user_id)
    elif named_uid:
        logs_q = logs_q.filter(WorkLog.user_id == named_uid)
    logs = logs_q.order_by(WorkLog.created_at.desc()).limit(6).all()
    titled = _best_named(_ctx_question(ctx), logs, lambda w: w.content or "", min_len=4)
    if titled is not None:
        logs = [titled] + [w for w in logs if w.id != titled.id]
    uids = {e.get("actor_id") for e in events if e.get("actor_id")} | {w.user_id for w in logs if w.user_id}
    names = {
        u.id: u.display_name or u.email
        for u in (ctx.db.query(U).filter(U.id.in_(uids)).all() if uids else [])
    }
    sources = []
    for w in logs:
        sources.append(
            {
                "id": f"wlog:{w.id}",
                "kind": "work_log",
                "title": (w.content or "周志")[:40],
                "actor": names.get(w.user_id) or "",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/work-logs?open={w.id}",
                "excerpt": _clip(f"{names.get(w.user_id) or ''}｜{w.content or ''}", 400),
            }
        )
    for e in events:
        hid = e.get("id") or ""
        sources.append(
            {
                "id": f"act:{hid}",
                "kind": "activity",
                "title": (e.get("summary") or e.get("verb") or "动态")[:80],
                "actor": names.get(e.get("actor_id")) or "",
                "page": None,
                "href": e.get("href") or f"/labs/{ctx.lab_id}/work-logs",
                "excerpt": _clip(
                    f"{names.get(e.get('actor_id')) or ''}｜{e.get('verb') or ''}｜{e.get('created_at') or ''}",
                    300,
                ),
            }
        )
    if not sources:
        from fengsuan.app.services.insight import work_log_list_empty_next

        nxt = work_log_list_empty_next(ctx.lab_id)
        return ToolResult(text="最近没有可引用的工作日志。", data={"empty": True, "empty_next": nxt})
    n_logs = len(logs)
    n_act = len(events)
    text = f"周志 {n_logs} 条，动态 {n_act} 条（不是排名）" if n_logs else f"最近动态 {n_act} 条（不是排名）"
    return ToolResult(text=text, sources=sources)


@tool(
    "get_lab_glossary",
    scopes=["lab", "writing", "onboarding"],
    description="取实验室术语表：已定译法与锁定不译。回答「组里这个词怎么译、哪些锁定不译」必须先调这个，不要另编译名。问句里点到的词排在前面。",
    params={"properties": {"src": {"type": "string"}}, "required": []},
    stub_keywords=("术语表", "怎么译", "锁定不译", "组里术语", "这个词译"),
)
def get_lab_glossary(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.kb import list_glossary

    rows = list_glossary(ctx.db, ctx.lab_id)
    href = f"/labs/{ctx.lab_id}/kb?tab=terms"
    asked = _ctx_question(ctx)
    want_src = _sanitize_glossary_src(str(args.get("src") or args.get("term") or ""))
    needles = _glossary_needles(asked)
    pinned = []
    rest = []
    for t in rows:
        src = str(t.get("src") or "")
        hit = bool(want_src and want_src in src.lower()) or _mentioned(asked, src, min_len=2)
        if not hit and needles:
            low = src.lower()
            hit = any(n.lower() == low or n.lower() in low or low in n.lower() for n in needles)
        if hit:
            pinned.append(t)
        else:
            rest.append(t)
    if _looks_term_lookup(asked) and (want_src or needles) and not pinned:
        return _catalog_miss(
            ctx,
            kind="glossary",
            title="术语表没有这个词",
            text="组里还没定这个译名。去知识库术语栏补上，不要另编译法。",
            href=href,
            step_label="打开术语表",
        )
    ordered = pinned if (want_src or (_looks_term_lookup(asked) and pinned)) else pinned + rest
    sources = []
    locked = 0
    for t in ordered[:40]:
        if t.get("do_not_translate"):
            locked += 1
        mark = "锁定不译" if t.get("do_not_translate") else (t.get("tgt") or "未定译")
        sources.append(
            {
                "id": f"term:{t.get('id')}",
                "kind": "glossary",
                "title": t.get("src") or "",
                "term_tgt": "" if t.get("do_not_translate") else (t.get("tgt") or ""),
                "locked": bool(t.get("do_not_translate")),
                "page": None,
                "href": href,
                "excerpt": _clip(f"{mark}｜{t.get('lang_pair') or ''}｜{t.get('domain') or ''}", 200),
            }
        )
    if not sources:
        nxt = {
            "title": "还没有术语表",
            "text": "去知识库术语栏添加译名，不要另编译法。",
            "steps": [{"key": "terms", "label": "打开术语表", "href": href}],
        }
        return ToolResult(
            text="还没有术语表。",
            sources=[
                {
                    "id": f"glossary:{ctx.lab_id}",
                    "kind": "glossary",
                    "title": "术语表",
                    "page": None,
                    "href": href,
                    "excerpt": "实验室还没有锁定译名。去知识库术语栏添加。",
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    pin_bit = f"，问句点名 {len(pinned)} 条" if pinned else ""
    return ToolResult(
        text=f"术语 {len(sources)} 条，其中锁定不译 {locked} 条{pin_bit}",
        sources=sources,
        data={"n": len(rows), "locked": locked, "pinned": len(pinned)},
    )


@tool(
    "get_lab_tasks",
    scopes=["lab", "onboarding"],
    description="取看板任务：题名、状态、指派人。学生只看指派给自己或自己建的。问句点到的人只看那个人被指派的（学生仍不能看同学的）。不要和入门下一步或作业失败混在一起。",
    params={
        "properties": {
            "mine": {"type": "boolean"},
            "assignee_id": {"type": "string"},
            "name": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("看板任务", "我的任务", "待办任务", "派给我", "组会派了", "过期任务", "任务到期", "看板截止", "的任务", "谁的任务"),
)
def get_lab_tasks(ctx: ToolContext, args: dict) -> ToolResult:
    mem = _member(ctx)
    from sqlalchemy import or_

    from fengsuan.app.models.activity import LabTask
    from fengsuan.app.models.collab import ActionItem
    from fengsuan.app.models.identity import User as U
    from fengsuan.app.services.insight import task_list_empty_next

    mine = args.get("mine")
    if isinstance(mine, str):
        mine = mine.strip().lower() in {"1", "true", "yes", "self", "me"}
    if mine is None and _asks_mine(_ctx_question(ctx), ("我的任务", "派给我", "我的待办")):
        mine = True
    teacher = (mem.role or "") == "teacher"
    named_uid = str(args.get("assignee_id") or "").strip()
    if not named_uid:
        named_uid = _lookup_member_id(ctx, str(args.get("name") or "")) if args.get("name") else ""
    if not named_uid and not mine:
        named_uid = _member_named_in_question(ctx)
    q = ctx.db.query(LabTask).filter(LabTask.lab_id == ctx.lab_id, LabTask.deleted_at.is_(None))
    if mine:
        q = q.filter(LabTask.assignee_id == ctx.user_id)
    elif not teacher:
        q = q.filter(or_(LabTask.assignee_id == ctx.user_id, LabTask.created_by == ctx.user_id))
    if named_uid and not mine:
        q = q.filter(LabTask.assignee_id == named_uid)
    rows = q.order_by(LabTask.created_at.desc()).limit(20).all()
    asked = _ctx_question(ctx)
    titled = _best_named(asked, rows, lambda t: t.title or "")
    if titled is not None:
        rows = [titled] + [t for t in rows if t.id != titled.id]
    ids = [t.id for t in rows]
    links: dict[str, ActionItem] = {}
    if ids:
        for a in ctx.db.query(ActionItem).filter(ActionItem.task_id.in_(ids)).all():
            if a.task_id and a.task_id not in links:
                links[a.task_id] = a
    uids = {t.assignee_id for t in rows if t.assignee_id}
    names = {
        u.id: u.display_name or u.email
        for u in (ctx.db.query(U).filter(U.id.in_(uids)).all() if uids else [])
    }
    sources = []
    for t in rows:
        action = links.get(t.id)
        extra = "｜来自组会" if action and action.meeting_id else ""
        due = _due_label(t.due_at, done=(t.status or "") == "done")
        sources.append(
            {
                "id": f"task:{t.id}",
                "kind": "task",
                "title": t.title or "",
                "task_status": t.status or "",
                "assignee": names.get(t.assignee_id) or "",
                "due_label": due,
                "page": None,
                "href": f"/labs/{ctx.lab_id}/tasks?open={t.id}",
                "excerpt": _clip(
                    f"{t.status or ''}｜{names.get(t.assignee_id) or '未指派'}｜{t.priority or ''}｜{due}{extra}",
                    300,
                ),
            }
        )
    if not sources:
        lab_n = ctx.db.query(LabTask).filter(LabTask.lab_id == ctx.lab_id, LabTask.deleted_at.is_(None)).count()
        kind = "library" if lab_n == 0 else "filter"
        nxt = task_list_empty_next(ctx.lab_id, empty_kind=kind)
        href = ((nxt.get("steps") or [{}])[0] or {}).get("href") or f"/labs/{ctx.lab_id}/tasks"
        return ToolResult(
            text=nxt.get("title") or "还没有任务。",
            sources=[
                {
                    "id": f"tasks:{ctx.lab_id}",
                    "kind": "task",
                    "title": nxt.get("title") or "看板",
                    "page": None,
                    "href": href,
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_kind": kind, "empty_next": nxt},
        )
    return ToolResult(text=f"看板任务 {len(sources)} 条", sources=sources)


@tool(
    "get_lab_kb_docs",
    scopes=["lab", "onboarding", "writing"],
    description="取知识库文档目录：文件名、索引状态、切块数。回答「知识库有哪些文档、索引好了吗」用这个，不要用检索片段冒充目录。",
    params={"properties": {"direction_id": {"type": "string"}, "name": {"type": "string"}}, "required": []},
    stub_keywords=("知识库有哪些", "知识库文档", "上传了什么文档", "索引好了", "哪些文档入库", "这份文档写了", "文档正文"),
)
def get_lab_kb_docs(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.kb import KbDocument, KnowledgeBase
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.insight import kb_ask_empty_next

    href = f"/labs/{ctx.lab_id}/kb?tab=docs"
    kb = ctx.db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == ctx.lab_id).first()
    if kb is None:
        nxt = kb_ask_empty_next(ctx.lab_id, "no_indexed")
        return ToolResult(
            text=nxt.get("title") or "还没有知识库文档。",
            sources=[
                {
                    "id": f"kbdocs:{ctx.lab_id}",
                    "kind": "kb_doc",
                    "title": nxt.get("title") or "知识库文档",
                    "page": None,
                    "href": href,
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    q = ctx.db.query(KbDocument).filter(KbDocument.kb_id == kb.id, KbDocument.deleted_at.is_(None))
    want = _direction_id(ctx, args, inherit="chip")
    if want:
        from fengsuan.app.services.directions import resolve_direction_scope
        from fengsuan.app.services.kb import kb_doc_ids_for_query

        allowed = set(kb_doc_ids_for_query(ctx.db, kb, want))
        scope = resolve_direction_scope(ctx.db, ctx.lab_id, want) or [want]
        pending = [
            d.id
            for d in ctx.db.query(KbDocument)
            .filter(KbDocument.kb_id == kb.id, KbDocument.deleted_at.is_(None), KbDocument.direction_id.in_(scope))
            .all()
            if d.status != "indexed"
        ]
        keep = allowed | set(pending)
        if keep:
            q = q.filter(KbDocument.id.in_(list(keep)))
        else:
            nxt = kb_ask_empty_next(ctx.lab_id, "no_indexed", direction_id=want)
            return ToolResult(
                text=nxt.get("title") or "这个方向还没有已入库文档。",
                sources=[
                    {
                        "id": f"kbdocs:{ctx.lab_id}:{want}",
                        "kind": "kb_doc",
                        "title": nxt.get("title") or "知识库文档",
                        "page": None,
                        "href": href,
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    rows = q.order_by(KbDocument.created_at.desc()).limit(20).all()
    lit_ids = [d.literature_item_id for d in rows if d.literature_item_id]
    titles = {
        x.id: x.title
        for x in (
            ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
            if lit_ids
            else []
        )
    }
    sources = []
    indexed = 0
    for d in rows:
        if d.status == "indexed":
            indexed += 1
        lit_title = titles.get(d.literature_item_id) if d.literature_item_id else ""
        sources.append(
            {
                "id": f"kbdoc:{d.id}",
                "kind": "kb_doc",
                "title": d.original_filename or "文档",
                "page": None,
                "href": (
                    f"/labs/{ctx.lab_id}/literature/{d.literature_item_id}"
                    if d.literature_item_id
                    else href
                ),
                "item_id": d.literature_item_id or "",
                "excerpt": _clip(
                    f"{d.status or ''}｜切块 {d.chunk_count or 0}｜{d.source_kind or ''}"
                    + (f"｜文献：{lit_title}" if lit_title else "")
                    + (f"｜{d.parse_error}" if d.parse_error else ""),
                    300,
                ),
            }
        )
    want_name = str(args.get("name") or "").strip().lower()
    asked = _ctx_question(ctx)
    one = None
    if want_name:
        one = next((d for d in rows if want_name in (d.original_filename or "").lower()), None)
    if one is None and asked:
        one = _best_named(asked, rows, lambda d: d.original_filename or "", min_len=4)
        if one is None:
            one = next(
                (d for d in rows if (d.original_filename or "").lower() and (d.original_filename or "").lower() in asked),
                None,
            )
    if (
        one is None
        and any(tok in asked for tok in ("这份文档", "文档正文"))
        and _glossary_needles(asked)
    ):
        nxt = kb_ask_empty_next(ctx.lab_id, "no_hits", direction_id=want or None)
        nxt = {**nxt, "title": "没有对上这份文档", "text": "打开知识库文档栏核对文件名。"}
        return ToolResult(
            text=nxt["text"],
            sources=[
                {
                    "id": f"kbdocmiss:{ctx.lab_id}",
                    "kind": "kb_doc",
                    "title": nxt["title"],
                    "page": None,
                    "href": href,
                    "excerpt": _clip(nxt["text"], 240),
                }
            ],
            data={"empty": True, "miss": True, "empty_next": nxt},
        )
    if one:
        from fengsuan.app.models.kb import KbChunk

        chunks = (
            ctx.db.query(KbChunk)
            .filter(KbChunk.doc_id == one.id)
            .order_by(KbChunk.seq.asc())
            .limit(3)
            .all()
        )
        if chunks:
            for ch in chunks:
                sources.append(
                    {
                        "id": f"kbchunk:{ch.id}",
                        "kind": "kb",
                        "title": one.original_filename or "文档",
                        "page": ch.page_no,
                        "href": href,
                        "excerpt": _clip(ch.content or "", 500),
                    }
                )
        else:
            sources.append(
                {
                    "id": f"kbdoc-body:{one.id}",
                    "kind": "kb_doc",
                    "title": one.original_filename or "文档",
                    "page": None,
                    "href": href,
                    "excerpt": "这份还没有切块正文。",
                }
            )
    if not sources:
        nxt = kb_ask_empty_next(ctx.lab_id, "no_indexed", direction_id=want or None)
        return ToolResult(
            text=nxt.get("title") or "还没有知识库文档。",
            sources=[
                {
                    "id": f"kbdocs:{ctx.lab_id}",
                    "kind": "kb_doc",
                    "title": nxt.get("title") or "知识库文档",
                    "page": None,
                    "href": href,
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    return ToolResult(
        text=f"知识库文档 {len(sources)} 份，已索引 {indexed} 份",
        sources=sources,
        data={"n": len(rows), "indexed": indexed},
    )


def _lab_file_text_clip(row) -> str:
    from pathlib import Path

    from fengsuan.app.core.config import get_settings
    from fengsuan.app.core.safepath import ensure_under

    name = (getattr(row, "filename", None) or "").lower()
    if not name.endswith((".md", ".txt", ".csv", ".json", ".bib", ".tex", ".rst")):
        return ""
    raw = getattr(row, "file_path", None) or ""
    if not raw:
        return ""
    try:
        path = ensure_under(Path(get_settings().file_root), Path(raw))
    except (OSError, ValueError):
        return ""
    try:
        if path.stat().st_size > 200_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


@tool(
    "get_lab_files",
    scopes=["lab", "onboarding"],
    description="取实验室共享资料柜：文件名、版本、谁上传的；点名一份文本文件时夹带正文摘录。回答「组里共享文件在哪、这份文件写了什么」用这个，不要和作业产物或知识库文档混在一起。",
    params={"properties": {"name": {"type": "string"}}, "required": []},
    stub_keywords=("共享文件", "组里文件", "实验室文件", "文件柜", "这份文件写了", "文件里写了"),
)
def get_lab_files(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.collab import LabFile
    from fengsuan.app.models.identity import User as U
    from fengsuan.app.services.insight import file_list_empty_next

    rows = (
        ctx.db.query(LabFile)
        .filter(LabFile.lab_id == ctx.lab_id)
        .order_by(LabFile.filename, LabFile.version.desc())
        .all()
    )
    latest: list = []
    seen: set[str] = set()
    for f in rows:
        if f.filename in seen:
            continue
        seen.add(f.filename)
        latest.append(f)
        if len(latest) >= 20:
            break
    uids = {f.uploaded_by for f in latest if f.uploaded_by}
    names = {
        u.id: u.display_name or u.email
        for u in (ctx.db.query(U).filter(U.id.in_(uids)).all() if uids else [])
    }
    want_name = str(args.get("name") or "").strip().lower()
    asked = _ctx_question(ctx)
    picked = None
    if want_name:
        picked = next((f for f in latest if want_name in (f.filename or "").lower()), None)
    if picked is None and asked:
        picked = _best_named(asked, latest, lambda f: f.filename or "", min_len=4)
        if picked is None:
            picked = next((f for f in latest if (f.filename or "").lower() and (f.filename or "").lower() in asked), None)
    if (
        picked is None
        and any(tok in asked for tok in ("这份文件", "文件里写了", "这份资料"))
        and _glossary_needles(asked)
    ):
        nxt = file_list_empty_next(ctx.lab_id)
        nxt = {
            **nxt,
            "title": "没有对上这份文件",
            "text": "打开资料柜核对文件名，不要凭文件名编正文。",
        }
        return ToolResult(
            text=nxt["text"],
            sources=[
                {
                    "id": f"filemiss:{ctx.lab_id}",
                    "kind": "lab_file",
                    "title": nxt["title"],
                    "page": None,
                    "href": f"/labs/{ctx.lab_id}/files",
                    "excerpt": _clip(nxt["text"], 240),
                }
            ],
            data={"empty": True, "miss": True, "empty_next": nxt},
        )
    sources = [
        {
            "id": f"file:{f.id}",
            "kind": "lab_file",
            "title": f.filename or "文件",
            "page": None,
            "href": f"/labs/{ctx.lab_id}/files?open={f.id}",
            "excerpt": _clip(
                f"v{f.version or 1}｜{f.size or 0} 字节｜{names.get(f.uploaded_by) or ''}｜{f.description or ''}",
                300,
            ),
        }
        for f in latest
    ]
    if picked:
        clip_txt = _lab_file_text_clip(picked)
        if clip_txt:
            sources.append(
                {
                    "id": f"file-body:{picked.id}",
                    "kind": "lab_file",
                    "title": picked.filename or "文件正文",
                    "page": None,
                    "href": f"/labs/{ctx.lab_id}/files?open={picked.id}",
                    "excerpt": _clip(clip_txt, 800),
                }
            )
        else:
            sources.append(
                {
                    "id": f"file-body:{picked.id}",
                    "kind": "lab_file",
                    "title": picked.filename or "文件",
                    "page": None,
                    "href": f"/labs/{ctx.lab_id}/files?open={picked.id}",
                    "excerpt": "这份不是可摘录的文本，请打开资料柜查看。",
                }
            )
    if not sources:
        nxt = file_list_empty_next(ctx.lab_id)
        return ToolResult(
            text=nxt.get("title") or "还没有共享文件。",
            sources=[
                {
                    "id": f"files:{ctx.lab_id}",
                    "kind": "lab_file",
                    "title": nxt.get("title") or "资料柜",
                    "page": None,
                    "href": f"/labs/{ctx.lab_id}/files",
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    return ToolResult(text=f"共享文件 {len(sources)} 份（每份最新版本）", sources=sources)


@tool(
    "get_starter_reading",
    scopes=["lab", "onboarding"],
    description="取组里建议先读的文献：在读的人多、且已经有方法卡的优先。回答「先读哪几篇」用这个。问「谁在读」用 get_paper_readers。",
    params={"properties": {"direction_id": {"type": "string"}}, "required": []},
    stub_keywords=("先读", "读哪", "读起", "哪几篇", "入门文献", "该读"),
)
def get_starter_reading(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.onboarding import starter_reading

    want = _direction_id(ctx, args, inherit="default")
    rows, _scope = _tool_scoped(
        ctx, lambda d: starter_reading(ctx.db, ctx.lab_id, direction_id=d or None), want
    )
    sources = []
    for r in rows:
        names = "、".join(str(n) for n in (r.get("reader_names") or [])[:4])
        who = f"：{names}" if names else ""
        n = r.get("readers") or 0
        sources.append(
            {
                "id": f"read:{r.get('id')}",
                "kind": "literature",
                "title": r.get("title") or "",
                "page": None,
                "href": r.get("href") or "",
                "item_id": r.get("id") or "",
                "excerpt": _clip(f"读过 {n} 人{who}。{r.get('why') or ''}", 400),
            }
        )
    return ToolResult(text=f"建议先读 {len(sources)} 篇", sources=sources)


@tool(
    "get_lab_literature",
    scopes=["lab", "onboarding", "writing"],
    description="取实验室文献目录（不是检索）：题名、年份、是否核心、本人是否在读、有没有 PDF。回答「组里有哪些文献、核心文献有哪些、我还没读哪些」必须先调这个，不要拿空检索词去搜。",
    params={
        "properties": {
            "direction_id": {"type": "string"},
            "core": {"type": "boolean"},
            "unread": {"type": "boolean"},
            "has_pdf": {"type": "boolean"},
        },
        "required": [],
    },
    stub_keywords=("组里有哪些文献", "库里有哪些", "核心文献有哪些", "我还没读", "没有pdf的篇", "文献目录", "年有哪些", "年的文献", "会上有哪些"),
)
def get_lab_literature(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from sqlalchemy import exists, func

    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureDirectionLink, LiteratureFile, LiteratureReading
    from fengsuan.app.services.insight import literature_list_empty_next
    from fengsuan.app.services.literature import paper_dto, reading_fields

    q = ctx.db.query(LiteratureItem).filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
    want = _direction_id(ctx, args, inherit="chip")
    dir_scope: list[str] = []
    if want:
        from fengsuan.app.services.directions import resolve_direction_scope

        dir_scope = resolve_direction_scope(ctx.db, ctx.lab_id, want)
        if not dir_scope:
            nxt = literature_list_empty_next(ctx.lab_id, direction_id=want, empty_kind="filter") or {}
            return ToolResult(
                text=nxt.get("title") or "这个方向没有可看的文献。",
                data={"empty": True, "empty_next": nxt},
            )
        q = q.filter(
            exists().where(
                LiteratureDirectionLink.item_id == LiteratureItem.id,
                LiteratureDirectionLink.direction_id.in_(dir_scope),
            )
        )
    core = args.get("core")
    if isinstance(core, str):
        core = core.strip().lower() in {"1", "true", "yes", "core"}
    unread = args.get("unread")
    if isinstance(unread, str):
        unread = unread.strip().lower() in {"1", "true", "yes", "unread"}
    has_pdf = args.get("has_pdf")
    if isinstance(has_pdf, str):
        has_pdf = has_pdf.strip().lower() in {"1", "true", "yes"}
    asked = _ctx_question(ctx)
    if args.get("core") is None and any(tok in asked for tok in ("核心文献有哪些", "哪些是核心", "核心篇目")):
        core = True
    if args.get("unread") is None and any(tok in asked for tok in ("我还没读", "还没开读", "未开读")):
        unread = True
    if args.get("has_pdf") is None and any(tok in asked for tok in ("没有pdf", "没pdf", "没有 pdf")):
        has_pdf = False
    if core:
        q = q.filter(
            exists().where(
                LiteratureDirectionLink.item_id == LiteratureItem.id,
                LiteratureDirectionLink.is_core.is_(True),
                *([LiteratureDirectionLink.direction_id.in_(dir_scope)] if dir_scope else []),
            )
        )
    if unread:
        q = q.filter(
            ~exists().where(
                LiteratureReading.item_id == LiteratureItem.id,
                LiteratureReading.user_id == ctx.user_id,
                LiteratureReading.status.in_(("reading", "read", "finished")),
            )
        )
    if has_pdf is True:
        q = q.filter(exists().where(LiteratureFile.item_id == LiteratureItem.id))
    elif has_pdf is False:
        q = q.filter(~exists().where(LiteratureFile.item_id == LiteratureItem.id))
    years = _years_in_question(asked)
    if years:
        q = q.filter(LiteratureItem.publish_year.in_(list(years)))
    rows = q.order_by(LiteratureItem.created_at.desc()).limit(40).all()
    from sqlalchemy import or_

    from fengsuan.app.services.search_index import search_terms

    ask_terms = search_terms(asked)
    if ask_terms:
        named_rows = (
            q.filter(or_(*[LiteratureItem.title.ilike(f"%{t}%") for t in ask_terms]))
            .order_by(LiteratureItem.created_at.desc())
            .limit(60)
            .all()
        )
        have = {r.id for r in rows}
        rows = rows + [r for r in named_rows if r.id not in have]
    scored = [(_lit_match_score(asked, r, citekeys=_lab_citekeys(ctx)), r) for r in rows]
    if years:
        rows = [r for _s, r in sorted(scored, key=lambda x: -x[0])]
    elif any(s > 0 for s, _r in scored):
        pinned = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s > 0]
        rest = [r for s, r in scored if s <= 0]
        rows = (pinned + rest)[:16]
    else:
        rows = rows[:16]
    ids = [x.id for x in rows]
    mine = {}
    core_by: dict[str, bool] = {}
    files_by: set[str] = set()
    cards: set[str] = set()
    if ids:
        from fengsuan.app.models.literature import LiteratureReading as LR

        for rd in ctx.db.query(LR).filter(LR.user_id == ctx.user_id, LR.item_id.in_(ids)).all():
            mine[rd.item_id] = rd
        for link in ctx.db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(ids)).all():
            if getattr(link, "is_core", False):
                core_by[link.item_id] = True
        files_by = {
            iid
            for (iid,) in ctx.db.query(LiteratureFile.item_id).filter(LiteratureFile.item_id.in_(ids)).all()
        }
        cards = {
            iid
            for (iid,) in ctx.db.query(LiteratureAnalysis.item_id)
            .filter(LiteratureAnalysis.item_id.in_(ids), LiteratureAnalysis.analysis_type == "method_card")
            .all()
        }
    sources = []
    for x in rows:
        dto = paper_dto(x)
        read = reading_fields(mine.get(x.id))
        bits = [
            "核心" if core_by.get(x.id) else "非核心",
            str(dto.get("publish_year") or "未登记年份"),
            dto.get("venue") or "未登记期刊/会议",
            f"本人：{read.get('my_reading') or '未开读'}",
            "有 PDF" if x.id in files_by else "没有 PDF",
            "有方法卡" if x.id in cards else "还没方法卡",
        ]
        sources.append(
            {
                "id": f"litcat:{x.id}",
                "kind": "literature",
                "title": x.title or "",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{x.id}",
                "item_id": x.id,
                "year": dto.get("publish_year") or "",
                "my_reading": read.get("my_reading") or "",
                "excerpt": _clip("｜".join(bits), 400),
            }
        )
    if not sources:
        lab_n = (
            ctx.db.query(func.count(LiteratureItem.id))
            .filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
            .scalar()
            or 0
        )
        kind = "library" if not lab_n else "filter"
        nxt = literature_list_empty_next(
            ctx.lab_id,
            direction_id=want or None,
            reading_status="unread" if unread else None,
            reader_id=ctx.user_id if unread else None,
            empty_kind=kind,
        ) or {}
        href = ((nxt.get("steps") or [{}])[0] or {}).get("href") or f"/labs/{ctx.lab_id}/literature"
        return ToolResult(
            text=nxt.get("title") or "还没有文献。",
            sources=[
                {
                    "id": f"litcat:{ctx.lab_id}",
                    "kind": "literature",
                    "title": nxt.get("title") or "文献库",
                    "page": None,
                    "href": href,
                    "excerpt": _clip(nxt.get("text") or "", 300),
                }
            ],
            data={"empty": True, "empty_kind": kind, "empty_next": nxt},
        )
    return ToolResult(text=f"文献目录 {len(sources)} 篇", sources=sources)


@tool(
    "get_paper_readers",
    scopes=["paper", "lab", "onboarding"],
    description="取这篇或组里谁在读、读到哪一页。人名来自在读记录，不要猜。精读默认看当前这篇。",
    params={
        "properties": {
            "item_id": {"type": "string", "description": "指定篇目；精读不填就是当前这篇"},
            "direction_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("谁在读", "读这篇", "有谁读", "谁读过", "在读这篇", "这篇谁"),
)
def get_paper_readers(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.literature import reading_roster

    item = _resolve_literature_item(ctx, args)
    explicit = bool(str(args.get("item_id") or "").strip())
    named = bool(
        item is not None
        and _lit_match_score(_ctx_question(ctx), item, citekeys=_lab_citekeys(ctx)) > 0
    )
    if ctx.scope != "paper" and item is not None and not explicit and not named:
        item = None
    item_id = item.id if item is not None else ""
    if item is not None:
        rows = reading_roster(ctx.db, ctx.lab_id, item_id=item.id)
    else:
        want = _direction_id(ctx, args, inherit="default" if ctx.scope == "onboarding" else "chip")
        rows, _scope = _tool_scoped(
            ctx,
            lambda d: reading_roster(ctx.db, ctx.lab_id, direction_id=d or None),
            want,
        )

    def _reader_source(r, named=False):
        page = r.get("last_page_no")
        excerpt = ("问句点名｜" if named else "") + f"{r.get('status_label') or ''}《{r.get('title') or ''}》"
        if page:
            excerpt += f" 第 {page} 页"
        return {
            "id": f"reader:{r.get('item_id')}:{r.get('user_id')}",
            "kind": "reading",
            "title": r.get("display_name") or "",
            "paper_title": r.get("title") or "",
            "page": page,
            "href": r.get("href") or r.get("member_href") or "",
            "item_id": r.get("item_id") or "",
            "excerpt": _clip(excerpt, 300),
        }

    sources = [_reader_source(r) for r in rows]
    named_uid = _member_named_in_question(ctx)
    if named_uid:
        pinned = [r for r in rows if r.get("user_id") == named_uid]
        rest = [r for r in rows if r.get("user_id") != named_uid]
        rows = pinned + rest
        sources = [_reader_source(r, named=r.get("user_id") == named_uid) for r in rows]
        if not pinned:
            href = sources[0]["href"] if sources else f"/labs/{ctx.lab_id}/literature"
            sources.insert(
                0,
                {
                    "id": f"reader-miss:{named_uid}",
                    "kind": "reading",
                    "title": "这个人还没开读",
                    "page": None,
                    "href": href,
                    "excerpt": "问句点到的人没有在读记录，不要把别人的进度算成他的。",
                },
            )
    if not sources:
        if item is not None:
            return ToolResult(
                text=f"《{item.title}》还没有人在读",
                sources=[
                    {
                        "id": f"reader-empty:{item.id}",
                        "kind": "reading",
                        "title": item.title or "",
                        "page": None,
                        "href": f"/labs/{ctx.lab_id}/literature/{item.id}",
                        "item_id": item.id,
                        "excerpt": "这篇还没有在读记录。不要把别人的进度算进来。",
                    }
                ],
                data={"empty": True, "item_id": item.id},
            )
        href = f"/labs/{ctx.lab_id}/literature"
        nxt = {
            "title": "组里还没有在读记录",
            "text": "打开文献库把一篇标成在读。",
            "steps": [{"key": "library", "label": "打开文献库", "href": href}],
        }
        return ToolResult(
            text=nxt["title"],
            sources=[
                {
                    "id": f"reader-empty:{ctx.lab_id}",
                    "kind": "reading",
                    "title": nxt["title"],
                    "page": None,
                    "href": href,
                    "excerpt": nxt["text"],
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    bits = []
    papers = []
    for r in rows:
        name = r.get("display_name") or "这位"
        try:
            page = int(r.get("last_page_no") or 0)
        except (TypeError, ValueError):
            page = 0
        if r.get("status") == "reading" and page >= 2:
            bit = f"{name}读到第 {page} 页"
        elif r.get("status") == "reading":
            bit = f"{name}在读"
        else:
            bit = f"{name}{r.get('status_label') or '已读'}"
        paper = r.get("title") or ""
        if paper and paper not in papers:
            papers.append(paper)
        bits.append((paper, bit))
    if len(papers) > 1:
        text = "。".join(f"《{paper}》{bit}" for paper, bit in bits if bit)
    else:
        text = "，".join(bit for _paper, bit in bits if bit)
    if text and not text.endswith("。"):
        text += "。"
    return ToolResult(text=text or "还没有人在读。", sources=sources)


@tool(
    "get_my_reading",
    scopes=["paper", "lab", "onboarding"],
    description="取提问者自己的在读进度：读到哪一页、百分之几、是否读完。回答「我读到哪了」用这个，不要把别人的在读当成自己的。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    stub_keywords=("我读到哪", "我读完了", "我的进度", "这篇我读", "读到第几页", "下一步读什么", "下一步该读"),
)
def _next_read_text(ctx: ToolContext) -> ToolResult | None:
    """对照本人想读和导师写在方向上的路线，回答下一步读什么。"""
    question = _ctx_question(ctx)
    if "下一步" not in question and "该读" not in question:
        return None
    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.models.literature import LiteratureItem, LiteratureReading

    mine = (
        ctx.db.query(LiteratureReading, LiteratureItem)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(
            LiteratureReading.user_id == ctx.user_id,
            LiteratureReading.status == "want_read",
            LiteratureItem.lab_id == ctx.lab_id,
            LiteratureItem.deleted_at.is_(None),
        )
        .limit(5)
        .all()
    )
    sources = []
    for reading, item in mine:
        sources.append(
            {
                "id": f"lit:{item.id}",
                "kind": "literature",
                "title": item.title or "",
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}",
                "item_id": item.id,
                "excerpt": "在你的待读里",
            }
        )
    from fengsuan.app.services.reading_route import next_on_route

    readings = (
        ctx.db.query(LiteratureReading, LiteratureItem)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(
            LiteratureReading.user_id == ctx.user_id,
            LiteratureItem.lab_id == ctx.lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureReading.status.in_(["reading", "finished"]),
        )
        .all()
    )
    progress_rows = [
        {"title": item.title or "", "item_id": item.id, "status": reading.status}
        for reading, item in readings
    ]
    route_bits = []
    directions = (
        ctx.db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == ctx.lab_id, ResearchDirection.deleted_at.is_(None))
        .limit(12)
        .all()
    )
    for direction in directions:
        try:
            payload = json.loads(direction.frontier_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        route = payload.get("route") if isinstance(payload, dict) else None
        if not isinstance(route, list) or not route:
            from fengsuan.app.services.reading_route import draft_reading_route

            drafted = draft_reading_route(payload.get("papers") or [])
            progress = next_on_route(drafted, progress_rows) if drafted else {}
            nxt = progress.get("next") or {}
            if nxt.get("title"):
                route_bits.append(
                    f"导师还没记下路线。按外面已有的前沿，下一篇可以是{nxt.get('stage') or '入门'}《{nxt['title']}》，不是任务"
                )
            continue
        progress = next_on_route(route, progress_rows)
        nxt = progress.get("next") or {}
        if nxt.get("title"):
            route_bits.append(f"下一篇是{nxt.get('stage') or '入门'}《{nxt['title']}》，{nxt.get('state') or '还没读'}")
            sources.append(
                {
                    "id": f"route:{direction.id}:{nxt.get('item_id') or nxt['title']}",
                    "kind": "literature",
                    "title": nxt["title"],
                    "href": f"/labs/{ctx.lab_id}/literature/{nxt['item_id']}" if nxt.get("item_id") else f"/labs/{ctx.lab_id}/directions/{direction.id}",
                    "item_id": nxt.get("item_id") or "",
                    "excerpt": f"路线上的{nxt.get('stage') or '入门'}，{nxt.get('state') or '还没读'}",
                }
            )
        done = progress.get("finished") or []
        if done:
            route_bits.append("已经读完：" + "、".join(f"《{title}》" for title in done[:3]))
        if len(route_bits) >= 3:
            break
    if not sources and not route_bits:
        return ToolResult(text="还没有待读，方向上也还没有导师整理的路线。", sources=[])
    lines = []
    if sources and any(row.get("excerpt") == "在你的待读里" for row in sources):
        waiting = [row for row in sources if row.get("excerpt") == "在你的待读里"]
        lines.append("你的待读：" + "、".join(f"《{s['title']}》" for s in waiting[:3]))
    if route_bits:
        lines.append("方向路线：" + "；".join(route_bits[:3]))
    return ToolResult(text="。".join(lines) + "。", sources=sources)


def get_my_reading(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    nxt = _next_read_text(ctx)
    if nxt is not None and not str(args.get("item_id") or "").strip() and ctx.scope != "paper":
        return nxt
    from fengsuan.app.models.literature import LiteratureReading
    from fengsuan.app.services.literature import literature_open_href, reading_fields

    labels = {
        "reading": "在读",
        "finished": "已读完",
        "want_read": "想读",
        "dropped": "已放弃",
        "read": "已读完",
    }
    item = _resolve_literature_item(ctx, args)
    explicit = bool(str(args.get("item_id") or "").strip())
    named = bool(
        item is not None
        and _lit_match_score(_ctx_question(ctx), item, citekeys=_lab_citekeys(ctx)) > 0
    )
    if ctx.scope != "paper" and item is not None and not explicit and not named:
        item = None
    item_id = item.id if item is not None else ""
    rows: list[tuple] = []
    if item_id:
        mine = (
            ctx.db.query(LiteratureReading)
            .filter(LiteratureReading.item_id == item.id, LiteratureReading.user_id == ctx.user_id)
            .first()
        )
        rows = [(item, mine)]
    else:
        found = (
            ctx.db.query(LiteratureReading, LiteratureItem)
            .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
            .filter(
                LiteratureReading.user_id == ctx.user_id,
                LiteratureItem.lab_id == ctx.lab_id,
                LiteratureItem.deleted_at.is_(None),
            )
            .order_by(LiteratureReading.last_opened_at.desc().nullslast())
            .limit(8)
            .all()
        )
        rows = [(item, mine) for mine, item in found]
    sources = []
    for item, mine in rows:
        fields = reading_fields(mine)
        status = fields.get("my_reading") or ""
        href = f"/labs/{ctx.lab_id}{literature_open_href(item.id, fields.get('last_page_no'))}"
        if not status:
            sources.append(
                {
                    "id": f"myread-miss:{item.id}",
                    "kind": "reading",
                    "title": item.title or "",
                    "page": None,
                    "href": href,
                    "item_id": item.id,
                    "excerpt": "你还没有这篇的在读记录。",
                }
            )
            continue
        sources.append(
            {
                "id": f"myread:{item.id}:{ctx.user_id}",
                "kind": "reading",
                "title": item.title or "",
                "page": fields.get("last_page_no"),
                "href": href,
                "item_id": item.id,
                "excerpt": _clip(
                    f"{labels.get(status) or status}｜{fields.get('progress_pct') or 0}%"
                    + (f"｜第 {fields.get('last_page_no')} 页" if fields.get("last_page_no") else ""),
                    200,
                ),
            }
        )
    if not sources:
        href = f"/labs/{ctx.lab_id}/literature"
        nxt = {
            "title": "你还没有在读记录",
            "text": "文献库里把一篇标成在读后会出现在这里。",
            "steps": [{"key": "library", "label": "打开文献库", "href": href}],
        }
        return ToolResult(
            text=nxt["title"],
            sources=[
                {
                    "id": f"myread:{ctx.lab_id}",
                    "kind": "reading",
                    "title": nxt["title"],
                    "page": None,
                    "href": href,
                    "excerpt": nxt["text"],
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    return ToolResult(text=f"你的在读 {len([s for s in sources if not str(s.get('id') or '').startswith('myread-miss')])} 篇", sources=sources)


@tool(
    "get_my_tutor_history",
    scopes=["paper", "lab"],
    description="取本人在这篇上的精读辅导记录（只读已有会话，不另开聊天）。回答「上次辅导说了什么、我问过这篇什么」必须先调这个，不要把别人的辅导或笔记当成自己的。",
    params={"properties": {"item_id": {"type": "string"}, "session_id": {"type": "string"}}, "required": []},
    stub_keywords=("上次辅导", "我问过这篇", "辅导记录", "上次问过", "辅导说过"),
)
def get_my_tutor_history(ctx: ToolContext, args: dict) -> ToolResult:
    from fengsuan.app.models.literature import LiteratureAnalysis
    from fengsuan.app.services.literature import paginate_tutor_history, parse_tutor_turn

    _member(ctx)
    item = _resolve_literature_item(ctx, args)
    sid = str(args.get("session_id") or "").strip() or None
    if item is None:
        rows = (
            ctx.db.query(LiteratureAnalysis, LiteratureItem)
            .join(LiteratureItem, LiteratureItem.id == LiteratureAnalysis.item_id)
            .filter(
                LiteratureAnalysis.analysis_type == "tutor",
                LiteratureAnalysis.created_by == ctx.user_id,
                LiteratureItem.lab_id == ctx.lab_id,
                LiteratureItem.deleted_at.is_(None),
            )
            .order_by(LiteratureAnalysis.created_at.desc())
            .limit(6)
            .all()
        )
        sources = []
        for row, lit in rows:
            turn = parse_tutor_turn(row)
            href = f"/labs/{ctx.lab_id}/literature/{lit.id}?panel=tutor"
            sess = turn.get("session_id") or ""
            sources.append(
                {
                    "id": f"tutor:{turn.get('id') or row.id}",
                    "kind": "tutor",
                    "title": (turn.get("question") or lit.title or "辅导")[:80],
                    "page": turn.get("page_no"),
                    "href": href + (f"&session={sess}" if sess else ""),
                    "item_id": lit.id,
                    "excerpt": _clip(
                        f"《{lit.title}》｜问：{turn.get('question') or ''}｜答：{turn.get('answer') or ''}",
                        500,
                    ),
                }
            )
        if not sources:
            return ToolResult(
                text="还没有你的辅导记录。先点名一篇再问，或打开一篇精读。",
                sources=[
                    {
                        "id": f"tutor:{ctx.lab_id}",
                        "kind": "tutor",
                        "title": "辅导记录",
                        "page": None,
                        "href": f"/labs/{ctx.lab_id}/literature",
                        "excerpt": "只看本人记录。不要把笔记或别人的提问当成自己的。",
                    }
                ],
                data={"empty": True},
            )
        return ToolResult(text=f"你的辅导 {len(sources)} 轮（只看本人）", sources=sources)
    pack = paginate_tutor_history(ctx.db, item.id, ctx.user_id, session_id=sid, page=1, page_size=6)
    href = f"/labs/{ctx.lab_id}/literature/{item.id}?panel=tutor"
    sources = []
    for t in pack.get("items") or []:
        sess = t.get("session_id") or ""
        sources.append(
            {
                "id": f"tutor:{t.get('id')}",
                "kind": "tutor",
                "title": (t.get("question") or "辅导")[:80],
                "page": t.get("page_no"),
                "href": href + (f"&session={sess}" if sess else ""),
                "item_id": item.id,
                "excerpt": _clip(
                    f"问：{t.get('question') or ''}｜答：{t.get('answer') or ''}",
                    500,
                ),
            }
        )
    if not sources:
        return ToolResult(
            text="这篇还没有你的辅导记录。",
            sources=[
                {
                    "id": f"tutor:{item.id}",
                    "kind": "tutor",
                    "title": item.title or "辅导",
                    "page": None,
                    "href": href,
                    "item_id": item.id,
                    "excerpt": "在现有精读辅导栏提问后会出现在这里。",
                }
            ],
            data={"empty": True},
        )
    return ToolResult(text=f"你的辅导 {len(sources)} 轮（只看本人）", sources=sources, data={"total": pack.get("total") or 0})


@tool(
    "get_lab_manuscripts",
    scopes=["lab", "onboarding"],
    description="取实验室手稿清单：题名、作者、状态。回答「组里在写什么、谁的稿、我的稿」用这个，不要和「这篇稿引用了谁」混。",
    params={"properties": {"direction_id": {"type": "string"}, "mine": {"type": "boolean"}}, "required": []},
    stub_keywords=("手稿", "在写", "论文稿", "谁在写", "我的稿", "我在写的稿"),
)
def get_lab_manuscripts(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.insight import manuscript_list_empty_next
    from fengsuan.app.services.onboarding import lab_manuscripts_brief

    want = _direction_id(ctx, args, inherit="default")
    rows, _scope = _tool_scoped(
        ctx, lambda d: lab_manuscripts_brief(ctx.db, ctx.lab_id, direction_id=d or None, limit=20), want
    )
    asked = _ctx_question(ctx)
    mine = args.get("mine")
    if isinstance(mine, str):
        mine = mine.strip().lower() in {"1", "true", "yes", "self", "me"}
    if mine is None and _asks_mine(asked, ("我的稿", "我在写的稿", "我在写")):
        mine = True
    named_uid = "" if mine else _member_named_in_question(ctx)
    owner_id = ctx.user_id if mine else named_uid
    if owner_id:
        owner_ids = {
            mid
            for (mid,) in ctx.db.query(Manuscript.id)
            .filter(Manuscript.lab_id == ctx.lab_id, Manuscript.owner_id == owner_id, Manuscript.deleted_at.is_(None))
            .all()
        }
        rows = [r for r in rows if r.get("id") in owner_ids]
    from fengsuan.app.services.reproduce import stamp_manuscript_honesty

    stamp_manuscript_honesty(ctx.db, rows)
    sources = [
        {
            "id": f"mscript:{r.get('id')}",
            "kind": "manuscript",
            "title": r.get("title") or "",
            "page": None,
            "href": r.get("href") or "",
            "excerpt": _clip(
                f"作者：{r.get('owner') or ''}｜状态：{r.get('status') or 'draft'}｜{r.get('kind') or ''}"
                + ("｜正文还没复现就写成成功" if r.get("ungrounded_reproduce") else ""),
                300,
            ),
        }
        for r in rows
    ]
    if not sources:
        nxt = manuscript_list_empty_next(
            ctx.lab_id,
            empty_kind="library" if not owner_id and not want else "filter",
            owner_id=owner_id or None,
            direction_id=want or None,
        )
        step = (nxt.get("steps") or [{}])[0]
        return ToolResult(
            text=nxt.get("text") or "手稿 0 篇",
            sources=[
                {
                    "id": f"mscripts:{ctx.lab_id}:empty",
                    "kind": "manuscript",
                    "title": nxt.get("title") or "还没有手稿",
                    "page": None,
                    "href": step.get("href") or f"/labs/{ctx.lab_id}/manuscripts",
                    "excerpt": _clip(nxt.get("text") or "", 240),
                }
            ],
            data={"empty": True, "mine": bool(mine), "empty_next": nxt},
        )
    return ToolResult(text=f"手稿 {len(sources)} 篇", sources=sources, data={"mine": bool(mine)})


def _resolve_manuscript(ctx: ToolContext, args: dict | None = None):
    """按 id / 上下文 / 题名 / 本人最近一篇取手稿。只读。"""
    from fengsuan.app.models.manuscript import Manuscript

    _member(ctx)
    raw = args or {}
    mid = str(raw.get("manuscript_id") or ctx.manuscript_id or "").strip()
    if mid:
        row = ctx.db.get(Manuscript, mid)
        if row is not None and row.deleted_at is None and row.lab_id == ctx.lab_id:
            return row
        return None
    title = str(raw.get("title") or raw.get("name") or "").strip()
    q = ctx.db.query(Manuscript).filter(Manuscript.lab_id == ctx.lab_id, Manuscript.deleted_at.is_(None))
    if title:
        hit = q.filter(Manuscript.title.ilike(f"%{title[:80]}%")).order_by(Manuscript.updated_at.desc()).first()
        if hit is not None:
            return hit
    asked = _ctx_question(ctx)
    if asked:
        named = _best_named(asked, q.order_by(Manuscript.updated_at.desc()).limit(40).all(), lambda r: r.title or "")
        if named is not None:
            return named
    named_uid = _member_named_in_question(ctx)
    if named_uid:
        theirs = q.filter(Manuscript.owner_id == named_uid).order_by(Manuscript.updated_at.desc()).first()
        if theirs is not None:
            return theirs
    mine = q.filter(Manuscript.owner_id == ctx.user_id).order_by(Manuscript.updated_at.desc()).first()
    return mine or q.order_by(Manuscript.updated_at.desc()).first()


def _item_ids_from_args(ctx: ToolContext, args: dict | None) -> list[str]:
    raw = args or {}
    ids: list[str] = []
    got = raw.get("item_ids")
    if isinstance(got, list):
        ids.extend(str(x).strip() for x in got if str(x).strip())
    elif isinstance(got, str) and got.strip():
        ids.extend(p.strip() for p in got.split(",") if p.strip())
    one = str(raw.get("item_id") or "").strip()
    if one:
        ids.append(one)
    if not ids:
        ids.extend(r.id for r in _lit_named_in_question(ctx, limit=8))
    if ctx.scope == "paper" and ctx.item_id:
        ids = [ctx.item_id] + [i for i in ids if i != ctx.item_id]
    elif ctx.item_id and not ids:
        ids.append(ctx.item_id)
    seen: set[str] = set()
    out: list[str] = []
    for iid in ids:
        if iid in seen:
            continue
        seen.add(iid)
        out.append(iid)
    return out[:8]


@tool(
    "get_lab_runs",
    scopes=["lab", "onboarding"],
    description="取本人或组内最近的文献调研 / 复现作业状态，以及工人未开、等算力、缺数据的排队。回答「综述好了吗、还在排队吗、还在等算力吗」必须先调这个，不要把等待写成失败。",
    params={"properties": {}, "required": []},
    stub_keywords=("综述好了", "跑完了", "还在排队", "作业状态", "调研作业", "排队吗", "等算力", "等gpu", "缺数据停", "算力排队"),
)
def get_lab_runs(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.runs import ResearchRun, ReproduceRun
    from fengsuan.app.services.reproduce import pending_without_worker_counts, pending_without_worker_items

    mem = _member(ctx)
    rq = ctx.db.query(ResearchRun).filter(ResearchRun.lab_id == ctx.lab_id)
    pq = ctx.db.query(ReproduceRun).filter(ReproduceRun.lab_id == ctx.lab_id)
    if (mem.role or "") != "teacher":
        rq = rq.filter(ResearchRun.requester_id == ctx.user_id)
        pq = pq.filter(ReproduceRun.requester_id == ctx.user_id)
    research = rq.order_by(ResearchRun.updated_at.desc()).limit(24).all()
    reproduce = pq.order_by(ReproduceRun.updated_at.desc()).limit(24).all()
    asked = _ctx_question(ctx)
    named_uid = _member_named_in_question(ctx)
    if named_uid:
        research = [r for r in research if r.requester_id == named_uid]
        reproduce = [r for r in reproduce if r.requester_id == named_uid]
    tokens = _run_ids_in_question(asked)
    if tokens:
        id_r = [r for r in research if _row_matches_run_id(r, tokens)]
        id_p = [r for r in reproduce if _row_matches_run_id(r, tokens)]
        if id_r or id_p:
            research, reproduce = id_r, id_p
    named_r = _best_named(asked, research, lambda r: r.question or "", min_len=4)
    if named_r is not None:
        research = [named_r] + [r for r in research if r.id != named_r.id]
    lit_ids_all = [r.literature_item_id for r in reproduce if r.literature_item_id]
    titles_all = {
        x.id: x.title
        for x in (
            ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids_all), LiteratureItem.deleted_at.is_(None)).all()
            if lit_ids_all
            else []
        )
    }
    named_p = _best_named(
        asked,
        reproduce,
        lambda r: titles_all.get(r.literature_item_id) or r.current_stage or "",
        min_len=4,
    )
    if named_p is None:
        lit_named = _lit_named_in_question(ctx, limit=4)
        if lit_named:
            want_lits = {x.id for x in lit_named}
            linked_p = [r for r in reproduce if r.literature_item_id in want_lits]
            if linked_p:
                reproduce = linked_p
    elif named_p is not None:
        reproduce = [named_p] + [r for r in reproduce if r.id != named_p.id]
    research = research[:8]
    reproduce = reproduce[:8]
    waiting = pending_without_worker_items(ctx.db, ctx.lab_id, limit=8)
    counts = pending_without_worker_counts(ctx.db, ctx.lab_id)
    sources = []
    for r in research:
        sources.append(
            {
                "id": f"run:{r.id}",
                "kind": "research_run",
                "title": (r.question or "文献调研")[:80],
                "page": None,
                "href": f"/labs/{ctx.lab_id}/runs/{r.id}",
                "excerpt": _clip(f"文献调研｜状态：{r.status}｜{r.error or ''}", 300),
            }
        )
    lit_ids = [r.literature_item_id for r in reproduce if r.literature_item_id]
    titles = {
        x.id: x.title
        for x in (
            ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
            if lit_ids
            else []
        )
    }
    for r in reproduce:
        sources.append(
            {
                "id": f"repro:{r.id}",
                "kind": "reproduce_run",
                "title": titles.get(r.literature_item_id) or r.current_stage or "复现",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/reproduce/{r.id}",
                "excerpt": _clip(f"复现｜状态：{r.status}｜阶段：{r.current_stage or ''}", 300),
            }
        )
    for w in waiting:
        wid = str(w.get("id") or "")
        if any(s["id"].endswith(wid) for s in sources):
            continue
        sources.append(
            {
                "id": f"wait:{w.get('kind')}:{wid}",
                "kind": "queued",
                "title": w.get("title") or "排队作业",
                "page": None,
                "href": w.get("href") or f"/labs/{ctx.lab_id}/runs",
                "excerpt": _clip(f"工人未开，已入队：{w.get('kind') or ''}", 200),
            }
        )
    from fengsuan.app.services.reproduce import lab_queue

    queue = lab_queue(ctx.db, ctx.lab_id)
    teacher = (mem.role or "") == "teacher"
    for it in queue.get("items") or []:
        if not teacher and it.get("requester_id") and it.get("requester_id") != ctx.user_id:
            continue
        wid = str(it.get("id") or "")
        if any(str(s.get("id") or "").endswith(wid) for s in sources):
            continue
        st = it.get("status") or ""
        sources.append(
            {
                "id": f"queue:{it.get('kind')}:{wid}",
                "kind": "queued",
                "title": it.get("title") or "排队作业",
                "page": None,
                "href": it.get("href") or f"/labs/{ctx.lab_id}/runs",
                "excerpt": _clip(f"{st}｜{it.get('next_step') or ''}｜等算力或缺数据不是失败", 240),
            }
        )
    wait_n = int(counts.get("pending_research") or 0) + int(counts.get("pending_reproduce") or 0)
    gpu_n = int(queue.get("awaiting_gpu") or 0) + int(queue.get("research_awaiting_gpu") or 0)
    blocked_n = int(queue.get("blocked") or 0)
    if not sources:
        from fengsuan.app.services.insight import research_list_empty_next

        nxt = research_list_empty_next(ctx.lab_id, empty_kind="library")
        return ToolResult(
            text="还没有文献调研或复现作业。" + (f"另有 {wait_n} 个已入队。" if wait_n else ""),
            data={"empty": True, "queued": wait_n, "awaiting_gpu": gpu_n, "blocked": blocked_n, "empty_next": nxt},
        )
    bits = [f"最近作业 {len(sources)} 条"]
    if wait_n:
        bits.append(f"工人未开排队 {wait_n} 个")
    if gpu_n:
        bits.append(f"等算力 {gpu_n} 个")
    if blocked_n:
        bits.append(f"缺数据停住 {blocked_n} 个")
    return ToolResult(
        text="；".join(bits),
        sources=sources,
        data={
            "queued": wait_n,
            "awaiting_gpu": gpu_n,
            "blocked": blocked_n,
            "awaiting_gpu_href": queue.get("awaiting_gpu_href"),
            "blocked_href": queue.get("blocked_href"),
        },
    )


@tool(
    "get_job_preflight",
    scopes=["lab", "onboarding", "writing", "paper"],
    description="取提交前已算好的诊断：工人、算力、引擎、Docker、方法卡、数据、进行中作业与 token 预算。回答「还能跑吗、工人开了吗、提交前缺什么」必须先调这个。缺项是事实，不拦提问，也不假装能跑完。",
    params={
        "properties": {
            "kind": {"type": "string"},
            "dataset_id": {"type": "string"},
            "item_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("还能跑", "能提交吗", "工人开了", "提交前", "还缺数据", "算力关了", "引擎装了", "还剩多少token", "token预算", "这个月token"),
)
def get_job_preflight(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.core.config import get_settings
    from fengsuan.app.services.insight import research_preflight
    from fengsuan.app.services.llm import lab_token_budget, lab_tokens_this_month, quota_warning
    from fengsuan.app.services.reproduce import reproduce_preflight
    from fengsuan.app.services.research_engine import count_heavy, hung_scope_literature_ids
    from fengsuan.app.services.worker import worker_snapshot

    kind = str(args.get("kind") or "").strip().lower()
    if kind not in {"research", "reproduce"}:
        kind = "reproduce" if ctx.scope == "paper" else "research"
    item_id = str(args.get("item_id") or ctx.item_id or "").strip() or None
    dataset_id = str(args.get("dataset_id") or "").strip() or None
    if not dataset_id:
        from fengsuan.app.models.dataset import Dataset

        ds_rows = (
            ctx.db.query(Dataset)
            .filter(Dataset.lab_id == ctx.lab_id, Dataset.deleted_at.is_(None))
            .order_by(Dataset.created_at.desc())
            .limit(20)
            .all()
        )
        named_ds = _best_named(_ctx_question(ctx), ds_rows, lambda d: d.name or "", min_len=2)
        if named_ds is not None:
            dataset_id = named_ds.id
    if kind == "reproduce":
        pack = reproduce_preflight(ctx.db, ctx.lab_id, literature_item_id=item_id, dataset_id=dataset_id)
        href = f"/labs/{ctx.lab_id}/reproduces"
        label = "复现"
    else:
        pack = research_preflight(ctx.db, ctx.lab_id, dataset_id=dataset_id, user_id=ctx.user_id)
        href = f"/labs/{ctx.lab_id}/runs"
        label = "文献调研"
    settings = get_settings()
    user_n, lab_n = count_heavy(ctx.db, ctx.user_id, ctx.lab_id)
    user_cap = int(settings.heavy_job_per_user)
    lab_cap = int(settings.heavy_job_per_lab)
    used_tok = lab_tokens_this_month(ctx.db, ctx.lab_id)
    budget = int(lab_token_budget(ctx.db, ctx.lab_id))
    remain = max(0, budget - used_tok)
    snap = worker_snapshot()
    worker_on = bool(pack.get("worker_on") if "worker_on" in pack else snap.get("enabled"))
    warn = quota_warning(ctx.db, ctx.lab_id)
    missing = [m for m in (pack.get("missing") or []) if isinstance(m, dict)]
    quota_full = user_n >= user_cap or lab_n >= lab_cap
    bits = [
        f"{label}提交前：缺 {len(missing)} 项" if missing else f"{label}现在可以排队提交",
        f"工人：{'开' if worker_on else '关'}",
        f"进行中作业 本人 {user_n}/{user_cap}，组内 {lab_n}/{lab_cap}",
        f"本月 token {used_tok}/{budget}（剩 {remain}）",
    ]
    if warn:
        bits.append(warn)
    elif quota_full:
        bits.append("进行中作业已达上限，新提交会被拒绝。")
    hung = pack.get("hung_inject_text")
    if hung:
        bits.append(str(hung))
    sources = [
        {
            "id": f"preflight:{kind}:{ctx.lab_id}",
            "kind": "job_preflight",
            "title": f"{label}提交前诊断",
            "page": None,
            "href": href,
            "excerpt": _clip("｜".join(bits), 500),
        }
    ]
    for m in missing[:8]:
        key = str(m.get("key") or "miss")
        sources.append(
            {
                "id": f"preflight-miss:{ctx.lab_id}:{key}",
                "kind": "job_preflight",
                "title": str(m.get("label") or key),
                "page": None,
                "href": str(m.get("href") or href),
                "excerpt": _clip(str(m.get("text") or ""), 300),
            }
        )
    if kind == "research":
        hung_ids, _kids = hung_scope_literature_ids(ctx.db, ctx.lab_id, ctx.user_id)
        if hung_ids:
            hung_titles = {
                x.id: x.title
                for x in ctx.db.query(LiteratureItem)
                .filter(LiteratureItem.id.in_(hung_ids[:4]), LiteratureItem.deleted_at.is_(None))
                .all()
            }
            for iid in hung_ids[:4]:
                sources.append(
                    {
                        "id": f"preflight-hung:{iid}",
                        "kind": "literature",
                        "title": hung_titles.get(iid) or iid,
                        "page": None,
                        "href": f"/labs/{ctx.lab_id}/literature/{iid}",
                        "item_id": iid,
                        "excerpt": "未选文献时会注入的已挂方向篇目",
                    }
                )
    data = {
        "kind": kind,
        "missing": missing,
        "worker_on": worker_on,
        "gpu_mode": pack.get("gpu_mode"),
        "docker_hint": pack.get("docker_hint"),
        "engine": pack.get("engine"),
        "heavy": {"user": user_n, "user_cap": user_cap, "lab": lab_n, "lab_cap": lab_cap},
        "quota_warning": warn,
        "tokens": {"used": used_tok, "budget": budget, "remaining": remain},
        "worker": {
            "enabled": bool(snap.get("enabled")),
            "started": bool(snap.get("started")),
            "lag_seconds": snap.get("lag_seconds"),
            "next_step": snap.get("next_step") or "",
        },
        "empty": not missing,
    }
    return ToolResult(text=_clip("。".join(bits), 400), sources=sources, data=data)


@tool(
    "get_unused_research",
    scopes=["lab", "writing"],
    description="取上次综述没写到、手稿也还没点名的篇名。回答「还有没用上的结果」用这个，点进现有 /runs?unused=1。",
    params={"properties": {}, "required": []},
    stub_keywords=("没用上", "没写到", "未写进综述", "未使用的结果"),
)
def get_unused_research(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.writing import user_unused_research_note

    note = user_unused_research_note(ctx.db, ctx.lab_id, ctx.user_id)
    if not note:
        return ToolResult(text="没有未写进综述的结果。", data={"empty": True})
    titles = [str(t) for t in (note.get("titles") or []) if t]
    href = note.get("href") or f"/labs/{ctx.lab_id}/runs?unused=1"
    sources = [
        {
            "id": f"unused:{note.get('run_id') or ctx.user_id}:{i}",
            "kind": "unused_research",
            "title": title,
            "page": None,
            "href": href,
            "excerpt": _clip(note.get("text") or "", 400),
        }
        for i, title in enumerate(titles[:8])
    ]
    if not sources:
        sources = [
            {
                "id": f"unused:{note.get('run_id') or ctx.user_id}",
                "kind": "unused_research",
                "title": "未写进综述",
                "page": None,
                "href": href,
                "excerpt": _clip(note.get("text") or "", 400),
            }
        ]
    return ToolResult(text=note.get("text") or f"未写进综述 {len(titles)} 篇", sources=sources, data=note)


@tool(
    "get_manuscript_brief",
    scopes=["lab", "writing"],
    description="取一篇手稿的题名、主张摘录、引用列表，以及稿内复现差距 sidecar（只读，不写回）。回答「这篇稿引用了谁、稿里哪些还没实测」用这个。",
    params={
        "properties": {
            "manuscript_id": {"type": "string"},
            "title": {"type": "string", "description": "手稿题名，与 manuscript_id 二选一"},
        },
        "required": [],
    },
    stub_keywords=("这篇稿", "手稿引用", "引用了谁", "稿子引用", "稿里还没实测", "引用还没实测", "稿里哪些", "稿的复现差距"),
)
def get_manuscript_brief(ctx: ToolContext, args: dict) -> ToolResult:
    from fengsuan.app.models.identity import User as U
    from fengsuan.app.models.manuscript import ManuscriptCitation

    row = _resolve_manuscript(ctx, args)
    if row is None:
        return ToolResult(text="没有找到手稿。", data={"empty": True})
    cites = (
        ctx.db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == row.id).all()
    )
    asked = _ctx_question(ctx)
    named_iids = {r.id for r in _lit_named_in_question(ctx, limit=8)}
    named_keys = {k for k in _lab_citekeys(ctx) if _citekey_in_asked(asked, k)}
    cites = sorted(
        cites,
        key=lambda c: (
            0 if (c.citation_key or "").strip().lower() in named_keys else 1,
            0 if c.literature_item_id in named_iids else 1,
        ),
    )
    lit_ids = [c.literature_item_id for c in cites if c.literature_item_id]
    titles = {
        x.id: x.title
        for x in (
            ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
            if lit_ids
            else []
        )
    }
    owner = ctx.db.get(U, row.owner_id)
    owner_name = (owner.display_name or owner.email) if owner else ""
    claim = _clip(row.content_md or "", 400)
    honesty = {"id": row.id}
    from fengsuan.app.models.manuscript import ManuscriptComment
    from fengsuan.app.services.reproduce import stamp_manuscript_honesty

    stamp_manuscript_honesty(ctx.db, [honesty])
    n_comments = (
        ctx.db.query(ManuscriptComment)
        .filter(ManuscriptComment.manuscript_id == row.id, ManuscriptComment.deleted_at.is_(None))
        .count()
    )
    honest_bit = "｜正文还没复现就写成成功" if honesty.get("ungrounded_reproduce") else ""
    unused_bit = honesty.get("unused_research") or {}
    unused_txt = str((unused_bit.get("text") if isinstance(unused_bit, dict) else unused_bit) or "")
    sources = [
        {
            "id": f"mscript:{row.id}",
            "kind": "manuscript",
            "title": row.title or "",
            "page": None,
            "href": f"/manuscripts/{row.id}",
            "excerpt": _clip(
                f"作者：{owner_name}｜状态：{row.status or 'draft'}｜评论 {n_comments} 条{honest_bit}"
                + (f"｜{unused_txt}" if unused_txt else "")
                + f"｜主张：{claim}",
                600,
            ),
        }
    ]
    for c in cites[:12]:
        title = titles.get(c.literature_item_id) or c.citation_key or c.literature_item_id
        pinned = (c.citation_key or "").strip().lower() in named_keys or c.literature_item_id in named_iids
        sources.append(
            {
                "id": f"cite:{row.id}:{c.literature_item_id}",
                "kind": "citation",
                "title": title,
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{c.literature_item_id}",
                "item_id": c.literature_item_id,
                "excerpt": _clip(
                    ("问句点名｜" if pinned else "") + f"引用键：{c.citation_key or ''}｜手稿《{row.title}》",
                    200,
                ),
            }
        )
    from fengsuan.app.services.reproduce import manuscript_reproduce_gaps_sidecar

    gap_md = manuscript_reproduce_gaps_sidecar(ctx.db, row)
    if gap_md:
        sources.append(
            {
                "id": f"mscript-gaps:{row.id}",
                "kind": "reproduce_gaps",
                "title": "稿内复现差距",
                "page": None,
                "href": f"/manuscripts/{row.id}",
                "excerpt": _clip(gap_md, 800),
            }
        )
    return ToolResult(
        text=f"《{row.title}》引用 {len(cites)} 篇",
        sources=sources,
        data={"manuscript_id": row.id, "citation_count": len(cites), "comment_count": n_comments},
    )


@tool(
    "get_manuscript_comments",
    scopes=["lab", "writing"],
    description="取一篇手稿上已有的评论正文（只读）。回答「这篇稿导师批了什么、稿上有什么评论」用这个，不要和精读批注或引用列表混在一起。",
    params={
        "properties": {
            "manuscript_id": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("稿子评论", "手稿评论", "导师批了", "这篇稿批", "稿上的评论", "有什么评论"),
)
def get_manuscript_comments(ctx: ToolContext, args: dict) -> ToolResult:
    from fengsuan.app.models.identity import User as U
    from fengsuan.app.models.manuscript import ManuscriptComment

    row = _resolve_manuscript(ctx, args)
    if row is None:
        return ToolResult(text="没有找到手稿。", data={"empty": True})
    comments = (
        ctx.db.query(ManuscriptComment)
        .filter(ManuscriptComment.manuscript_id == row.id, ManuscriptComment.deleted_at.is_(None))
        .order_by(ManuscriptComment.created_at.desc())
        .limit(20)
        .all()
    )
    uids = {c.user_id for c in comments if c.user_id}
    names = {
        u.id: u.display_name or u.email
        for u in (ctx.db.query(U).filter(U.id.in_(uids)).all() if uids else [])
    }
    href = f"/manuscripts/{row.id}?tab=comments"
    sources = []
    for c in comments:
        sources.append(
            {
                "id": f"ms-cmt:{c.id}",
                "kind": "manuscript_comment",
                "title": (c.body or "")[:40] or "评论",
                "page": None,
                "href": f"/manuscripts/{row.id}?comment={c.id}&tab=comments",
                "excerpt": _clip(f"{names.get(c.user_id) or ''}｜{c.body or ''}", 500),
            }
        )
    if not sources:
        return ToolResult(
            text=f"《{row.title}》还没有评论。",
            sources=[
                {
                    "id": f"ms-cmt:{row.id}",
                    "kind": "manuscript_comment",
                    "title": row.title or "手稿评论",
                    "page": None,
                    "href": href,
                    "excerpt": "这篇稿还没有评论。",
                }
            ],
            data={"empty": True, "manuscript_id": row.id},
        )
    return ToolResult(
        text=f"《{row.title}》评论 {len(sources)} 条",
        sources=sources,
        data={"manuscript_id": row.id, "n": len(comments)},
    )


@tool(
    "get_manuscript_versions",
    scopes=["lab", "writing"],
    description="取一篇手稿的版本清单：版本号、时间、最近一版摘录。回答「稿子改了几版、上次改了什么」用这个，不要把全文每一版都读进来。",
    params={
        "properties": {
            "manuscript_id": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("改了几版", "上次改了", "版本历史", "稿子版本", "这篇稿改了"),
)
def get_manuscript_versions(ctx: ToolContext, args: dict) -> ToolResult:
    from fengsuan.app.models.manuscript import ManuscriptVersion

    row = _resolve_manuscript(ctx, args)
    if row is None:
        return ToolResult(text="没有找到手稿。", data={"empty": True})
    vers = (
        ctx.db.query(ManuscriptVersion)
        .filter(ManuscriptVersion.manuscript_id == row.id)
        .order_by(ManuscriptVersion.version.desc())
        .limit(12)
        .all()
    )
    href = f"/manuscripts/{row.id}?tab=versions"
    sources = []
    for i, v in enumerate(vers):
        preview = ""
        if i == 0:
            preview = _clip(v.content_md or "", 400)
        sources.append(
            {
                "id": f"ms-ver:{v.id}",
                "kind": "manuscript_version",
                "title": f"v{v.version} · {row.title or ''}",
                "page": None,
                "href": href,
                "excerpt": _clip(
                    f"{(v.created_at.isoformat() if v.created_at else '')}｜{len(v.content_md or '')} 字"
                    + (f"｜{preview}" if preview else ""),
                    500,
                ),
            }
        )
    if not sources:
        return ToolResult(
            text=f"《{row.title}》还没有版本快照。",
            sources=[
                {
                    "id": f"ms-ver:{row.id}",
                    "kind": "manuscript_version",
                    "title": row.title or "手稿版本",
                    "page": None,
                    "href": href,
                    "excerpt": "写回或保存后会出现版本。",
                }
            ],
            data={"empty": True, "manuscript_id": row.id},
        )
    return ToolResult(
        text=f"《{row.title}》共 {len(sources)} 个版本",
        sources=sources,
        data={"manuscript_id": row.id, "n": len(vers)},
    )


@tool(
    "get_method_compare",
    scopes=["paper", "lab", "writing"],
    description="对照已有方法卡与复现简报，不新生成对比文章。精读默认含当前篇。回答「这几篇方法差在哪」用这个。",
    params={
        "properties": {
            "item_ids": {"type": "array", "items": {"type": "string"}, "description": "要对照的文献 id"},
            "item_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("方法差", "方法对照", "这几篇方法", "方法对比"),
)
def get_method_compare(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.writing import literature_card_hints

    ids = _item_ids_from_args(ctx, args)
    if not ids:
        named = _lit_named_in_question(ctx, limit=8)
        if named:
            ids = [r.id for r in named]
        else:
            rows = (
                ctx.db.query(LiteratureItem)
                .filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
                .order_by(LiteratureItem.updated_at.desc())
                .limit(3)
                .all()
            )
            ids = [r.id for r in rows]
    if not ids:
        return ToolResult(text="没有可对照的文献。", data={"empty": True})
    hints = literature_card_hints(ctx.db, ctx.lab_id, ids)
    if not hints:
        return ToolResult(text="这几篇还没有方法卡可对照。", data={"empty": True})
    sources = []
    for h in hints:
        bits = [
            f"问题：{h.get('problem') or '未写'}",
            f"方法：{h.get('methods') or '未写'}",
            f"局限：{h.get('limitations') or '未写'}",
            f"复现：{h.get('reproduce_brief') or '尚未实测'}",
        ]
        sources.append(
            {
                "id": f"compare:{h.get('id')}",
                "kind": "method_card",
                "title": h.get("title") or "",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{h.get('id')}?panel=card",
                "item_id": h.get("id") or "",
                "excerpt": _clip("；".join(bits), 800),
            }
        )
    return ToolResult(text=f"对照 {len(sources)} 篇已有方法卡", sources=sources, data={"hints": hints})


@tool(
    "get_core_coverage",
    scopes=["lab", "writing"],
    description="看手稿正文有没有点名本方向核心文献。回答「核心文献写进正文了吗」用这个。",
    params={"properties": {"manuscript_id": {"type": "string"}}, "required": []},
    stub_keywords=("核心文献", "写进正文", "点名核心"),
)
def get_core_coverage(ctx: ToolContext, args: dict) -> ToolResult:
    from fengsuan.app.services.writing import core_literature_coverage

    row = _resolve_manuscript(ctx, args)
    if row is None:
        return ToolResult(text="没有找到手稿，无法核对核心文献。", data={"empty": True})
    cov = core_literature_coverage(ctx.db, row)
    missing = cov.get("core_missing") or []
    mentioned = cov.get("core_mentioned") or []
    sources = []
    for lit in mentioned[:12]:
        sources.append(
            {
                "id": f"core-in:{row.id}:{lit.get('id')}",
                "kind": "core_mentioned",
                "title": lit.get("title") or "",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{lit.get('id')}",
                "item_id": lit.get("id") or "",
                "excerpt": _clip(f"已在《{row.title}》正文点名", 200),
            }
        )
    for lit in missing[:12]:
        sources.append(
            {
                "id": f"core-miss:{row.id}:{lit.get('id')}",
                "kind": "core_missing",
                "title": lit.get("title") or "",
                "page": None,
                "href": f"/manuscripts/{row.id}?tab=claims",
                "item_id": lit.get("id") or "",
                "excerpt": _clip(f"还没写进《{row.title}》正文", 200),
            }
        )
    if not sources:
        return ToolResult(
            text="这个方向还没有可核对的核心文献。",
            data={**cov, "empty": True},
        )
    text = (
        f"核心文献 {cov.get('core_total') or 0} 篇，已点名 {cov.get('core_mentioned_count') or 0}，"
        f"未写进正文 {cov.get('core_missing_count') or 0}"
    )
    return ToolResult(text=text, sources=sources, data=cov)


@tool(
    "get_lab_datasets",
    scopes=["lab", "onboarding", "writing"],
    description="取实验室已登记的数据集：名称、是否有文件、挂了哪些文献、被作业用过几次。点名一份时带上 usages（哪次调研/复现用过）。回答「组里有什么数据、这份数据被谁用」必须先调这个。",
    params={
        "properties": {
            "direction_id": {"type": "string"},
            "dataset_id": {"type": "string"},
            "name": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("数据集", "什么数据", "哪份数据", "用哪份", "有没有数据", "这份数据", "被哪次作业用", "挂了哪份", "用了哪份数据"),
)
def get_lab_datasets(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.dataset import Dataset, DatasetLiteratureLink
    from fengsuan.app.services.datasets import dataset_dto, usage_counts, usages

    rows = (
        ctx.db.query(Dataset)
        .filter(Dataset.lab_id == ctx.lab_id, Dataset.deleted_at.is_(None))
        .order_by(Dataset.created_at.desc())
        .limit(20)
        .all()
    )
    ids = [d.id for d in rows]
    links: dict[str, list[str]] = {i: [] for i in ids}
    if ids:
        for row in ctx.db.query(DatasetLiteratureLink).filter(DatasetLiteratureLink.dataset_id.in_(ids)).all():
            links.setdefault(row.dataset_id, []).append(row.literature_item_id)
    want = _direction_id(ctx, args, inherit="chip")
    if want:
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(ctx.db, ctx.lab_id, want)
        if not scope:
            from fengsuan.app.services.insight import dataset_list_empty_next

            nxt = dataset_list_empty_next(ctx.lab_id, direction_id=want, empty_kind="filter")
            return ToolResult(text="这个方向没有可看的数据集。", data={"empty": True, "empty_next": nxt})
        scoped = {
            iid
            for (iid,) in ctx.db.query(LiteratureDirectionLink.item_id)
            .filter(LiteratureDirectionLink.direction_id.in_(scope))
            .all()
        }
        rows = [d for d in rows if any(iid in scoped for iid in links.get(d.id) or [])]
        ids = [d.id for d in rows]
    want_id = str(args.get("dataset_id") or "").strip()
    want_name = str(args.get("name") or "").strip().lower()
    asked = _ctx_question(ctx)
    if not want_id and not want_name:
        named = _best_named(asked, rows, lambda d: d.name or "", min_len=2)
        if named is not None:
            want_name = (named.name or "").lower()
        else:
            run = _named_run_in_question(ctx)
            if run is not None:
                dsid = getattr(run, "dataset_id", None)
                if dsid:
                    want_id = str(dsid)
                else:
                    from fengsuan.app.services.insight import dataset_list_empty_next

                    nxt = dataset_list_empty_next(ctx.lab_id, empty_kind="library")
                    nxt = {
                        **nxt,
                        "title": "这次作业没有登记数据集",
                        "text": "打开数据集页登记后再挂到作业上，不要凭题名编一份数据。",
                    }
                    href = ((nxt.get("steps") or [{}])[0] or {}).get("href") or f"/labs/{ctx.lab_id}/datasets"
                    return ToolResult(
                        text="这次作业没有登记数据集。",
                        sources=[
                            {
                                "id": f"dsmiss:{ctx.lab_id}:run",
                                "kind": "dataset",
                                "title": nxt["title"],
                                "page": None,
                                "href": href,
                                "excerpt": _clip(nxt["text"], 240),
                            }
                        ],
                        data={"empty": True, "miss": True, "empty_next": nxt},
                    )
    if want_id:
        rows = [d for d in rows if d.id == want_id]
        ids = [d.id for d in rows]
    elif want_name:
        rows = [d for d in rows if want_name in (d.name or "").lower()]
        ids = [d.id for d in rows]
    elif not want_id:
        lit_rows = _lit_named_in_question(ctx, limit=4)
        if lit_rows:
            want_lits = {x.id for x in lit_rows}
            linked = [d for d in rows if any(iid in want_lits for iid in links.get(d.id) or [])]
            if linked:
                rows = linked
                ids = [d.id for d in rows]
            else:
                from fengsuan.app.services.insight import dataset_list_empty_next

                nxt = dataset_list_empty_next(ctx.lab_id, direction_id=want or None, empty_kind="filter")
                nxt = {
                    **nxt,
                    "title": "这篇没有挂数据集",
                    "text": "打开数据集页登记，或换一篇再问。不要凭题名编一份数据。",
                }
                href = ((nxt.get("steps") or [{}])[0] or {}).get("href") or f"/labs/{ctx.lab_id}/datasets"
                return ToolResult(
                    text="这篇文献还没有挂数据集。",
                    sources=[
                        {
                            "id": f"dsmiss:{ctx.lab_id}",
                            "kind": "dataset",
                            "title": nxt.get("title") or "没有挂数据集",
                            "page": None,
                            "href": href,
                            "excerpt": _clip(nxt.get("text") or "", 240),
                        }
                    ],
                    data={"empty": True, "miss": True, "empty_next": nxt},
                )
        elif any(tok in asked for tok in ("这份数据", "哪份数据", "数据集", "用哪份", "有没有数据")):
            for needle in _glossary_needles(asked):
                low = needle.lower()
                if not _is_distinct_name(needle, min_len=3):
                    continue
                if any(low in (d.name or "").lower() or (d.name or "").lower() in low for d in rows):
                    rows = [d for d in rows if low in (d.name or "").lower() or (d.name or "").lower() in low]
                    ids = [d.id for d in rows]
                    want_name = low
                    break
                want_name = low
                rows = []
                ids = []
                break
    if (want_id or want_name) and not rows:
        from fengsuan.app.services.insight import dataset_list_empty_next

        nxt = dataset_list_empty_next(ctx.lab_id, direction_id=want or None, empty_kind="filter")
        nxt = {
            **nxt,
            "title": "没有对上这份数据集",
            "text": "打开数据集页核对名称，或先登记再问。",
        }
        href = ((nxt.get("steps") or [{}])[0] or {}).get("href") or f"/labs/{ctx.lab_id}/datasets"
        return ToolResult(
            text="没有找到这份数据集。",
            sources=[
                {
                    "id": f"dsmiss:{ctx.lab_id}",
                    "kind": "dataset",
                    "title": nxt.get("title") or "没有对上这份数据集",
                    "page": None,
                    "href": href,
                    "excerpt": _clip(nxt.get("text") or "", 240),
                }
            ],
            data={"empty": True, "miss": True, "empty_next": nxt},
        )
    used = usage_counts(ctx.db, ids)
    titles = {}
    lit_ids = [iid for dsid in ids for iid in links.get(dsid) or []]
    if lit_ids:
        titles = {
            x.id: x.title
            for x in ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    teacher = (_member(ctx).role or "") == "teacher"
    sources = []
    detail = usages(ctx.db, rows[0].id) if len(rows) == 1 else {}
    for d in rows:
        lids = links.get(d.id) or []
        names = "、".join(titles.get(i) or "" for i in lids[:4] if titles.get(i))
        dto = dataset_dto(d, teacher=teacher, literature_ids=lids, usage_count=used.get(d.id, 0))
        sources.append(
            {
                "id": f"ds:{d.id}",
                "kind": "dataset",
                "title": d.name or "",
                "page": None,
                "href": f"/datasets/{d.id}",
                "excerpt": _clip(
                    f"{'有文件' if dto.get('has_file') else '还没上传文件'}｜作业用过 {dto.get('usage_count') or 0} 次"
                    + (f"｜挂了：{names}" if names else ""),
                    400,
                ),
            }
        )
    if detail:
        for r in (detail.get("research_runs") or [])[:4]:
            sources.append(
                {
                    "id": f"ds-use-r:{r.get('id')}",
                    "kind": "research_run",
                    "title": (r.get("question") or "文献调研")[:80],
                    "page": None,
                    "href": r.get("href") or f"/runs/{r.get('id')}",
                    "excerpt": _clip(f"调研用过这份数据｜状态：{r.get('status') or ''}", 240),
                }
            )
        for r in (detail.get("reproduce_runs") or [])[:4]:
            sources.append(
                {
                    "id": f"ds-use-p:{r.get('id')}",
                    "kind": "reproduce_run",
                    "title": "复现作业",
                    "page": None,
                    "href": r.get("href") or f"/reproduces/{r.get('id')}",
                    "excerpt": _clip(f"复现用过这份数据｜状态：{r.get('status') or ''}", 240),
                }
            )
    if not sources:
        from fengsuan.app.services.insight import dataset_list_empty_next

        nxt = dataset_list_empty_next(ctx.lab_id, direction_id=want or None, empty_kind="filter" if want else "library")
        return ToolResult(text="实验室还没有登记数据集。", data={"empty": True, "empty_next": nxt})
    extra = ""
    if detail:
        n_use = len(detail.get("research_runs") or []) + len(detail.get("reproduce_runs") or [])
        extra = f"；被作业用过 {n_use} 次"
    return ToolResult(text=f"数据集 {len(rows)} 份{extra}", sources=sources)


_KEY_ARTIFACTS = {
    "literature_review.md",
    "literature_evidence.md",
    "actual_metrics.json",
    "search_hits.jsonl",
    "plan.md",
    "latest_state.json",
    "data_report.md",
    "reproduce_gaps.md",
}


@tool(
    "get_run_detail",
    scopes=["lab", "onboarding", "writing"],
    description="取某次文献调研或复现作业的状态、失败原因、下一步，以及关键产物文件。回答「那个作业为什么失败、卡在哪、产出了什么」用这个，不要猜。",
    params={
        "properties": {
            "run_id": {"type": "string"},
            "kind": {"type": "string", "description": "research 或 reproduce"},
        },
        "required": [],
    },
    stub_keywords=("为什么失败", "卡在哪", "任务详情", "作业失败", "那个任务", "作业产出", "产物文件", "工件", "那个失败", "失败的作业"),
)
def get_run_detail(ctx: ToolContext, args: dict) -> ToolResult:
    mem = _member(ctx)
    from fengsuan.app.models.runs import ResearchRun, ReproduceRun
    from fengsuan.app.services.reproduce import job_can_resume, job_feedback, job_next_step

    teacher = (mem.role or "") == "teacher"
    rid = str(args.get("run_id") or args.get("id") or "").strip()
    kind = str(args.get("kind") or "").strip().lower()
    research = None
    reproduce = None

    def _own_research(row):
        return row is not None and row.lab_id == ctx.lab_id and (teacher or row.requester_id == ctx.user_id)

    def _own_repro(row):
        return row is not None and row.lab_id == ctx.lab_id and (teacher or row.requester_id == ctx.user_id)

    if rid:
        if kind != "reproduce":
            got = ctx.db.get(ResearchRun, rid)
            research = got if _own_research(got) else None
        if research is None:
            got = ctx.db.get(ReproduceRun, rid)
            reproduce = got if _own_repro(got) else None
    if research is None and reproduce is None:
        tokens = _run_ids_in_question(_ctx_question(ctx))
        if tokens:
            rq0 = ctx.db.query(ResearchRun).filter(ResearchRun.lab_id == ctx.lab_id)
            pq0 = ctx.db.query(ReproduceRun).filter(ReproduceRun.lab_id == ctx.lab_id)
            if not teacher:
                rq0 = rq0.filter(ResearchRun.requester_id == ctx.user_id)
                pq0 = pq0.filter(ReproduceRun.requester_id == ctx.user_id)
            for row in rq0.order_by(ResearchRun.updated_at.desc()).limit(40).all():
                if _row_matches_run_id(row, tokens):
                    research = row
                    break
            if research is None:
                for row in pq0.order_by(ReproduceRun.updated_at.desc()).limit(40).all():
                    if _row_matches_run_id(row, tokens):
                        reproduce = row
                        break
            if research is None and reproduce is None:
                from fengsuan.app.services.insight import research_list_empty_next

                nxt = research_list_empty_next(ctx.lab_id, empty_kind="library")
                nxt = {**nxt, "title": "没有对上这次作业", "text": "打开作业页核对编号，不要把别的任务说成这一次。"}
                href = ((nxt.get("steps") or [{}])[0] or {}).get("href") or f"/labs/{ctx.lab_id}/runs"
                return ToolResult(
                    text="没有对上这次作业。先打开作业页核对编号。",
                    sources=[
                        {
                            "id": f"runmiss:{ctx.lab_id}",
                            "kind": "research_run",
                            "title": "没有对上这次作业",
                            "page": None,
                            "href": href,
                            "excerpt": _clip(nxt.get("text") or "", 240),
                        }
                    ],
                    data={"empty": True, "miss": True, "empty_next": nxt},
                )
    if research is None and reproduce is None:
        rq = ctx.db.query(ResearchRun).filter(ResearchRun.lab_id == ctx.lab_id)
        pq = ctx.db.query(ReproduceRun).filter(ReproduceRun.lab_id == ctx.lab_id)
        if not teacher:
            rq = rq.filter(ResearchRun.requester_id == ctx.user_id)
            pq = pq.filter(ReproduceRun.requester_id == ctx.user_id)
        asked = _ctx_question(ctx)
        research_rows = rq.order_by(ResearchRun.updated_at.desc()).limit(24).all()
        repro_rows = pq.order_by(ReproduceRun.updated_at.desc()).limit(24).all()
        lit_ids = [r.literature_item_id for r in repro_rows if r.literature_item_id]
        titles = {
            x.id: x.title
            for x in (
                ctx.db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
                if lit_ids
                else []
            )
        }
        prefer_fail = any(tok in asked for tok in ("失败", "卡在", "出错"))

        def _is_fail(row) -> bool:
            return bool(
                prefer_fail
                and (
                    (row.status or "") in {"failed", "error", "blocked"}
                    or (getattr(row, "error", None) or "").strip()
                    or (getattr(row, "error_class", None) or "")
                )
            )

        named_r = _best_named(asked, research_rows, lambda r: r.question or "", prefer=_is_fail if prefer_fail else None)
        named_p = _best_named(
            asked,
            repro_rows,
            lambda r: titles.get(r.literature_item_id) or r.current_stage or "",
            prefer=_is_fail if prefer_fail else None,
        )
        want_repro = kind == "reproduce" or ("复现" in asked and "调研" not in asked)
        want_research = kind == "research" or any(tok in asked for tok in ("调研", "综述作业"))
        if want_repro and named_p is not None:
            reproduce = named_p
        elif want_research and named_r is not None:
            research = named_r
        elif named_r is not None:
            research = named_r
        elif named_p is not None:
            reproduce = named_p
        if research is None and reproduce is None:
            if kind == "reproduce":
                reproduce = pq.order_by(ReproduceRun.updated_at.desc()).first()
            else:
                research = rq.order_by(ResearchRun.updated_at.desc()).first()
                if research is None:
                    reproduce = pq.order_by(ReproduceRun.updated_at.desc()).first()
    row = research or reproduce
    if row is None:
        from fengsuan.app.services.insight import research_list_empty_next

        nxt = research_list_empty_next(ctx.lab_id, empty_kind="library")
        return ToolResult(text="还没有文献调研或复现作业。", data={"empty": True, "empty_next": nxt})
    is_repro = reproduce is not None and research is None
    fb = job_feedback(row.status, getattr(row, "error_class", None), kind="reproduce" if is_repro else "research")
    nxt = job_next_step(row.status, getattr(row, "error_class", None), kind="reproduce" if is_repro else "research")
    href = f"/labs/{ctx.lab_id}/reproduce/{row.id}" if is_repro else f"/labs/{ctx.lab_id}/runs/{row.id}"
    title = (getattr(row, "question", None) or getattr(row, "current_stage", None) or "作业")[:80]
    excerpt = _clip(
        f"{fb.get('title') or row.status}｜{nxt or ''}｜{row.error or ''}｜"
        f"{'可恢复' if job_can_resume(row.status, kind='reproduce' if is_repro else 'research') else '现在不能恢复'}",
        500,
    )
    sources = [
        {
            "id": f"{'repro' if is_repro else 'run'}:{row.id}",
            "kind": "reproduce_run" if is_repro else "research_run",
            "title": title,
            "page": None,
            "href": href,
            "excerpt": excerpt,
        }
    ]
    from fengsuan.app.services.research_engine import list_artifacts, resolve_artifact

    arts = list_artifacts(getattr(row, "save_path", "") or "", limit=80)
    asked = _ctx_question(ctx)
    named_art = None
    for a in arts:
        name = (a.get("name") or a.get("path") or "").strip()
        stem = name.rsplit(".", 1)[0] if name else ""
        if name and (name.lower() in asked or (len(stem) >= 6 and stem.lower() in asked)):
            named_art = a
            break
    if named_art is not None:
        picked = [named_art]
    else:
        picked = [a for a in arts if (a.get("name") or "") in _KEY_ARTIFACTS]
        if not picked:
            picked = arts[:4]
        if any(tok in asked for tok in ("actual_metrics", "literature_evidence", "data_report", "产物")) and arts:
            wanted = next((tok for tok in ("actual_metrics.json", "literature_evidence.md", "data_report.md") if tok.split(".", 1)[0] in asked), "")
            if wanted and not any((a.get("name") or "") == wanted for a in arts):
                sources.append(
                    {
                        "id": f"artmiss:{row.id}:{wanted}",
                        "kind": "artifact",
                        "title": f"没有 {wanted}",
                        "page": None,
                        "href": href,
                        "excerpt": "这次作业没有这份产物，不要凭文件名编内容。",
                    }
                )
    clip_by = {
        "actual_metrics.json": 800,
        "literature_evidence.md": 1200,
        "data_report.md": 800,
        "literature_review.md": 800,
    }
    save_path = getattr(row, "save_path", "") or ""
    for a in picked[:8]:
        name = a.get("name") or ""
        body = ""
        limit = clip_by.get(name) or 0
        if limit and save_path:
            try:
                path = resolve_artifact(save_path, a.get("path") or name)
                if path.is_file() and path.stat().st_size < 200_000:
                    body = path.read_text(encoding="utf-8", errors="ignore")[:limit]
            except (OSError, ValueError):
                body = ""
        sources.append(
            {
                "id": f"art:{row.id}:{a.get('path')}",
                "kind": "artifact",
                "title": name or a.get("path") or "产物",
                "page": None,
                "href": href,
                "excerpt": _clip(
                    f"{a.get('path') or ''}｜{a.get('size') or 0} 字节" + (f"｜{body}" if body else ""),
                    800,
                ),
            }
        )
    return ToolResult(
        text=f"{title}：{fb.get('title') or row.status}",
        sources=sources,
        data={"run_id": row.id, "status": row.status, "feedback": fb, "artifact_n": len(arts)},
    )


@tool(
    "get_related_work",
    scopes=["lab", "writing"],
    description="取相关工作该点名的已有方法卡与复现简报（只读，不新写一节）。回答「相关工作该写谁」用这个。",
    params={
        "properties": {
            "item_ids": {"type": "array", "items": {"type": "string"}},
            "item_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("相关工作该", "相关工作点名", "该写谁", "该引用哪些方法"),
)
def get_related_work(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.services.writing import related_work_context

    ids = _item_ids_from_args(ctx, args)
    if not ids:
        named = _lit_named_in_question(ctx, limit=8)
        if named:
            ids = [r.id for r in named]
        else:
            rows = (
                ctx.db.query(LiteratureItem)
                .filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
                .order_by(LiteratureItem.updated_at.desc())
                .limit(4)
                .all()
            )
            ids = [r.id for r in rows]
    if not ids:
        return ToolResult(text="没有可点名的文献。", data={"empty": True})
    ctx_pack = related_work_context(ctx.db, ctx.lab_id, ids)
    hints = ctx_pack.get("hints") or []
    sources = []
    for h in hints:
        sources.append(
            {
                "id": f"rw:{h.get('id')}",
                "kind": "method_card" if h.get("has_card") else "literature",
                "title": h.get("title") or "",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{h.get('id')}?panel=card",
                "item_id": h.get("id") or "",
                "excerpt": _clip(
                    f"{'有方法卡' if h.get('has_card') else '还没写方法'}｜"
                    f"{h.get('problem') or ''}｜复现：{h.get('reproduce_brief') or '尚未实测'}",
                    500,
                ),
            }
        )
    missing = ctx_pack.get("no_fulltext_titles") or []
    if missing:
        sources.append(
            {
                "id": f"rw-nofull:{ctx.lab_id}",
                "kind": "note",
                "title": "无全文仅摘要",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature",
                "excerpt": _clip("；".join(str(t) for t in missing[:8]), 300),
            }
        )
    if not sources:
        return ToolResult(text="还没有可对照的方法卡。", data={"empty": True})
    return ToolResult(text=f"相关工作可点名 {len(hints)} 篇", sources=sources, data=ctx_pack)


@tool(
    "get_honesty_brief",
    scopes=["lab", "onboarding", "writing"],
    description="取实验室已算好的诚实口径：还没做完什么、哪篇把没复现写成成功、综述没写到。只读现有统计，不是排名，不要另编催促。",
    params={"properties": {"direction_id": {"type": "string"}}, "required": []},
    stub_keywords=("还没做完", "写成成功", "没复现就写", "无人在读", "还没写方法", "诚实口径", "卡在哪", "缺哪类", "缺什么证据"),
)
def get_honesty_brief(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.insight import build_lab_stuck_brief
    from fengsuan.app.services.reproduce import lab_reproduce_spine, ungrounded_manuscript_ids

    want = _direction_id(ctx, args, inherit="chip")
    spine = lab_reproduce_spine(ctx.db, ctx.lab_id, direction_id=want or None)
    ug_ids = ungrounded_manuscript_ids(ctx.db, ctx.lab_id)
    if want:
        from fengsuan.app.models.manuscript import ManuscriptDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(ctx.db, ctx.lab_id, want)
        if not scope:
            return ToolResult(text="这个方向无效，没有可核对的诚实口径。", data={"empty": True})
        keep = {
            mid
            for (mid,) in ctx.db.query(ManuscriptDirectionLink.manuscript_id)
            .filter(ManuscriptDirectionLink.direction_id.in_(scope))
            .all()
        }
        ug_ids = {i for i in ug_ids if i in keep}
    named_uid = _member_named_in_question(ctx)
    lab_ug_n = len(ug_ids)
    if named_uid and ug_ids:
        keep_owner = {
            mid
            for (mid,) in ctx.db.query(Manuscript.id)
            .filter(Manuscript.id.in_(list(ug_ids)), Manuscript.owner_id == named_uid, Manuscript.deleted_at.is_(None))
            .all()
        }
        ug_ids = {i for i in ug_ids if i in keep_owner}
    asked = _ctx_question(ctx)
    if any(tok in asked for tok in ("卡在", "缺哪", "缺什么证据", "缺的证据")):
        target = named_uid or ctx.user_id
        if target != ctx.user_id and (ctx.role or "") != "teacher":
            return ToolResult(text="同学的卡点不在这里。", data={"empty": True})
        from fengsuan.app.models.lab import LabMembership
        from fengsuan.app.services.insight import build_member_stuck, enrich_member_stuck, live_direction_id

        mem = (
            ctx.db.query(LabMembership)
            .filter(LabMembership.lab_id == ctx.lab_id, LabMembership.user_id == target)
            .first()
        )
        from fengsuan.app.models.literature import LiteratureItem, LiteratureReading
        from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

        reading_count = (
            ctx.db.query(LiteratureReading)
            .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
            .filter(
                LiteratureReading.user_id == target,
                LiteratureReading.status == "reading",
                LiteratureItem.lab_id == ctx.lab_id,
                LiteratureItem.deleted_at.is_(None),
            )
            .count()
        )
        owned = (
            ctx.db.query(Manuscript)
            .filter(Manuscript.lab_id == ctx.lab_id, Manuscript.owner_id == target, Manuscript.deleted_at.is_(None))
            .limit(8)
            .all()
        )
        manuscripts = [
            {
                "id": row.id,
                "title": row.title,
                "related_work_written": is_related_work_heading(row.content_md or ""),
                "core_missing_count": manuscript_core_missing_count(ctx.db, row),
            }
            for row in owned
        ]
        direction_id = live_direction_id(ctx.db, ctx.lab_id, mem.direction_id if mem else None)
        stuck = enrich_member_stuck(
            ctx.db,
            ctx.lab_id,
            target,
            build_member_stuck(
                ctx.lab_id,
                role=(mem.role if mem else ""),
                direction_id=direction_id,
                reading_count=reading_count,
                manuscripts=manuscripts,
                user_id=target,
                membership_status=(mem.status if mem else "active"),
            ),
            direction_id=direction_id,
        )
        sources = []
        bits = []
        for step in stuck.get("steps") or []:
            if step.get("evidence_text"):
                bits.append(step["evidence_text"])
            for ev in (step.get("evidence") or [])[:3]:
                sources.append(
                    {
                        "id": f"stuck:{ev.get('item_id') or ev.get('title')}",
                        "kind": "literature",
                        "title": ev.get("title") or "",
                        "href": ev.get("href") or "",
                        "item_id": ev.get("item_id") or "",
                        "excerpt": _clip(f"{ev.get('gap') or ''} {ev.get('one_line') or ''}".strip(), 200),
                    }
                )
        text = " ".join(bits) or "当前没有还没做完的事。"
        return ToolResult(text=text[:800], sources=sources[:8], data={"stuck": stuck})
    brief = build_lab_stuck_brief(
        ctx.db,
        ctx.lab_id,
        spine=spine,
        ungrounded_n=lab_ug_n,
        direction_id=want or None,
    )
    if named_uid:
        who = ctx.db.get(User, named_uid)
        who_name = (who.display_name or who.email) if who else "这个人"
        href = f"/labs/{ctx.lab_id}/manuscripts?owner_id={named_uid}"
        sources = [
            {
                "id": f"honesty-who:{named_uid}",
                "kind": "honesty",
                "title": f"{who_name}还没复现就写成成功（不是组里合计）",
                "page": None,
                "href": href,
                "excerpt": _clip(
                    f"只列{who_name}还没复现就写成成功的稿 {len(ug_ids)} 篇。"
                    "组里无人在读 / 还没写方法仍看方向或全组口径，不要算成他的。",
                    400,
                ),
            }
        ]
        if not ug_ids:
            return ToolResult(
                text=f"{who_name}没有还没复现就写成成功的手稿。不是排名。",
                sources=sources,
                data={"empty": True, "named_user_id": named_uid},
            )
    else:
        sources = [
            {
                "id": f"honesty:{ctx.lab_id}:{want or 'lab'}",
                "kind": "honesty",
                "title": "还没做完（不是排名）",
                "page": None,
                "href": brief.get("href") or f"/labs/{ctx.lab_id}/directions",
                "excerpt": _clip(brief.get("text") or "", 500),
            }
        ]
    if ug_ids:
        rows = (
            ctx.db.query(Manuscript)
            .filter(Manuscript.id.in_(list(ug_ids)), Manuscript.deleted_at.is_(None))
            .order_by(Manuscript.updated_at.desc())
            .limit(8)
            .all()
        )
        for m in rows:
            sources.append(
                {
                    "id": f"honesty-ug:{m.id}",
                    "kind": "manuscript",
                    "title": m.title or "",
                    "page": None,
                    "href": f"/manuscripts/{m.id}",
                    "excerpt": _clip("正文还没复现就写成成功。禁止把声称当组内成功。", 200),
                }
            )
    if not named_uid:
        for key, kind, href_key in (
            ("unused_titles", "unused_research", "unused_research_href"),
            ("no_card_titles", "no_card", "no_card_href"),
            ("related_work_titles", "related_work_missing", "related_work_href"),
        ):
            href = brief.get(href_key) or ""
            for row in (brief.get(key) or [])[:4]:
                if isinstance(row, str):
                    title, row_href = row, href
                elif isinstance(row, dict):
                    title, row_href = row.get("title") or "", row.get("href") or href
                else:
                    continue
                if not title:
                    continue
                sources.append(
                    {
                        "id": f"honesty-{kind}:{title[:24]}",
                        "kind": kind,
                        "title": title,
                        "page": None,
                        "href": row_href or href,
                        "excerpt": _clip(brief.get("text") or title, 200),
                    }
                )
        return ToolResult(text=brief.get("text") or "没有还没做完的事。不是排名。", sources=sources, data=brief)
    return ToolResult(
        text=f"{who_name}还没复现就写成成功 {len(ug_ids)} 篇。不是组里合计，也不是排名。",
        sources=sources,
        data={"named_user_id": named_uid, "ungrounded_n": len(ug_ids)},
    )


@tool(
    "get_citation_links",
    scopes=["paper", "lab", "writing"],
    description="取这篇在组内被哪些手稿引用、方法卡标了哪些组内相关篇。只读已有引用与方法卡，不新开图谱。",
    params={
        "properties": {
            "item_ids": {"type": "array", "items": {"type": "string"}},
            "item_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("被哪篇稿引用", "被谁引用", "引用关系", "谁连着这篇", "图谱里谁"),
)
def get_citation_links(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation
    from fengsuan.app.services.analysis import best_cards_for_items

    if ctx.scope == "paper" and ctx.item_id:
        named = _lit_named_in_question(ctx, limit=1)
        ids = [named[0].id] if named else [ctx.item_id]
    else:
        ids = _item_ids_from_args(ctx, args)
    if not ids:
        return _literature_miss(ctx)
    live = {
        x.id: x
        for x in ctx.db.query(LiteratureItem)
        .filter(LiteratureItem.id.in_(ids), LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
        .all()
    }
    ids = [i for i in ids if i in live]
    if not ids:
        return ToolResult(text="这些文献不属于本实验室。", data={"empty": True})
    cites = (
        ctx.db.query(ManuscriptCitation)
        .filter(ManuscriptCitation.literature_item_id.in_(ids))
        .all()
    )
    ms_ids = [c.manuscript_id for c in cites]
    manuscripts = {
        m.id: m
        for m in (
            ctx.db.query(Manuscript)
            .filter(Manuscript.id.in_(ms_ids), Manuscript.lab_id == ctx.lab_id, Manuscript.deleted_at.is_(None))
            .all()
            if ms_ids
            else []
        )
    }
    sources = []
    for c in cites[:12]:
        ms = manuscripts.get(c.manuscript_id)
        if ms is None:
            continue
        lit = live.get(c.literature_item_id)
        sources.append(
            {
                "id": f"cite-link:{ms.id}:{c.literature_item_id}",
                "kind": "citation",
                "title": ms.title or "",
                "page": None,
                "href": f"/manuscripts/{ms.id}",
                "item_id": c.literature_item_id,
                "excerpt": _clip(f"《{ms.title}》引用了《{(lit.title if lit else '')}》｜键：{c.citation_key or ''}", 300),
            }
        )
    cards = best_cards_for_items(ctx.db, ids)
    for iid, card in cards.items():
        for rel in (card.get("related_in_lab") or [])[:6]:
            if not isinstance(rel, dict):
                continue
            rid = str(rel.get("literature_item_id") or rel.get("id") or "").strip()
            if not rid or rid == iid:
                continue
            other = ctx.db.get(LiteratureItem, rid)
            if other is None or other.deleted_at is not None or other.lab_id != ctx.lab_id:
                continue
            src_title = (live.get(iid).title if live.get(iid) else "") or ""
            sources.append(
                {
                    "id": f"related-lab:{iid}:{rid}",
                    "kind": "related",
                    "title": other.title or "",
                    "page": None,
                    "href": f"/labs/{ctx.lab_id}/literature/{rid}",
                    "item_id": rid,
                    "excerpt": _clip(f"方法卡标组内相关：{src_title} → {other.title}", 300),
                }
            )
    if not sources:
        return ToolResult(text="库里还没有手稿引用这篇，方法卡也没标组内相关篇。", data={"empty": True, "item_ids": ids})
    return ToolResult(text=f"组内引用/相关 {len(sources)} 条", sources=sources)


# ---------------------------------------------------------------- T3 文档类


def _file_parse_bits(ctx: ToolContext, item: LiteratureItem) -> dict:
    from fengsuan.app.models.literature import LiteratureFile

    files = ctx.db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).all()
    if not files:
        return {"parse_status": [], "parse_hint": "没有登记 PDF"}
    statuses = [(f.parse_status or "pending") for f in files]
    err = next((str(f.parse_error or "").strip() for f in files if (f.parse_error or "").strip()), "")
    hint = "解析状态：" + "、".join(statuses)
    if err:
        hint += "｜" + err[:80]
    return {"parse_status": statuses, "parse_hint": hint}


@tool(
    "get_paper_page_text",
    scopes=["paper", "lab", "writing"],
    description="取当前文献某一页的正文。页码从 1 起。这是「对着这一页回答」的唯一正文来源，扫描件没有可复制文字时会明确告诉你。",
    params={
        "properties": {
            "page_no": {"type": "integer"},
            "item_id": {"type": "string", "description": "跨篇提问时指定，不填就是当前这篇"},
        },
        "required": [],
    },
    stub_keywords=("这一页", "本页", "原文", "正文", "怎么说", "第几页", "哪一页", "哪页"),
)
def get_paper_page_text(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.pagecache import item_single_page

    page_no = _resolve_page_no(ctx, item, args)
    pack = item_single_page(ctx.db, item, page_no)
    text = pack.get("page_text") or ""
    if not text:
        bits = _file_parse_bits(ctx, item)
        hint = str(bits.get("parse_hint") or "")
        # 没有 PDF 时摘要就是唯一正文，照实标成 abstract 而不是假装有页。
        abstract = (item.abstract or "").strip()
        if abstract:
            return ToolResult(
                text=_clip((hint + "。" if hint else "") + "这篇没有可翻页的正文，只有摘要。", 240),
                sources=[
                    {
                        "id": f"abstract:{item.id}",
                        "kind": "abstract",
                        "title": item.title,
                        "page": None,
                        "href": f"/labs/{ctx.lab_id}/literature/{item.id}",
                        "item_id": item.id,
                        "excerpt": _clip(abstract, 3500),
                    }
                ],
                data={"page_text_missing": True, "abstract_only": True, "parse_status": bits.get("parse_status") or []},
            )
        return ToolResult(
            text=_clip((hint + "。" if hint else "") + "这一页没有可复制文字（可能是扫描件）。不要凭标题编造内容。", 240),
            data={
                "page_text_missing": True,
                "page_count": pack.get("page_count") or 0,
                "parse_status": bits.get("parse_status") or [],
            },
        )
    actual = page_no or 1
    return ToolResult(
        text=f"第 {actual} 页正文已取到",
        sources=[
            {
                "id": f"page:{item.id}:{actual}",
                "kind": "page",
                "title": item.title,
                "page": actual,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?page={actual}",
                "item_id": item.id,
                "excerpt": _clip(text, 3500),
            }
        ],
        data={"page_count": pack.get("page_count") or 0},
    )


@tool(
    "get_page_translation",
    scopes=["paper", "lab", "writing"],
    description="取这一页已经缓存的译文，不新烧一次翻译。回答「这一页中文怎么说、有没有页译文」用这个；没有缓存就照实说，不要当场另译。",
    params={
        "properties": {
            "page_no": {"type": "integer"},
            "lang": {"type": "string", "description": "默认 zh"},
            "item_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("页译文", "这一页中文", "这页中文", "这页译", "译文缓存"),
)
def get_page_translation(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.pagecache import item_single_page, page_fingerprint, read_page_translation

    page_no = _resolve_page_no(ctx, item, args) or 1
    lang = str(args.get("lang") or "zh").strip() or "zh"
    pack = item_single_page(ctx.db, item, page_no)
    fp = page_fingerprint(pack.get("page_text") or "") if pack.get("has_fulltext") else ""
    text = read_page_translation(ctx.db, item.id, page_no, lang, fingerprint=fp) if fp else None
    lang_href = f"/labs/{ctx.lab_id}/language?lit={item.id}&page={page_no}"
    if not text:
        return ToolResult(
            text="这一页还没有缓存译文。不要当场另译，去译润页生成。",
            sources=[
                {
                    "id": f"tr-miss:{item.id}:{page_no}:{lang}",
                    "kind": "page_translation",
                    "title": item.title or "",
                    "page": page_no,
                    "href": lang_href,
                    "item_id": item.id,
                    "excerpt": "还没有页译文缓存。",
                }
            ],
            data={"empty": True, "hit": False, "page_no": page_no, "lang": lang},
        )
    return ToolResult(
        text=f"第 {page_no} 页已有{lang}译文（缓存，不是新译）",
        sources=[
            {
                "id": f"tr:{item.id}:{page_no}:{lang}",
                "kind": "page_translation",
                "title": item.title or "",
                "page": page_no,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?page={page_no}",
                "item_id": item.id,
                "excerpt": _clip(text, 3500),
            }
        ],
        data={"hit": True, "page_no": page_no, "lang": lang},
    )


@tool(
    "search_in_paper",
    scopes=["paper", "lab", "writing"],
    description="在当前这一篇正文里找和问题相关的页，返回带页码的摘录。问方法、实验、消融在哪时用这个，不要猜页码。",
    params={
        "properties": {
            "query": {"type": "string"},
            "item_id": {"type": "string", "description": "不填就是当前这篇"},
        },
        "required": [],
    },
    stub_keywords=("消融", "实验在哪", "哪一节", "全文里", "这篇里", "哪里写了"),
)
def search_in_paper(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    query = str(args.get("query") or _ctx_question(ctx) or "").strip()
    if len(query) < 2:
        return ToolResult(error="检索词为空")
    from fengsuan.app.models.literature import LiteratureFilePage
    from fengsuan.app.services.kb import query_kb
    from fengsuan.app.services.sweep import _topic_tokens

    tokens = [tok for tok in _topic_tokens(query) if len(tok) >= 2][:12] or [query[:40]]
    pages = (
        ctx.db.query(LiteratureFilePage)
        .filter(LiteratureFilePage.item_id == item.id)
        .order_by(LiteratureFilePage.page_no.asc())
        .all()
    )
    scored = []
    for page in pages:
        blob = (page.text or "").lower()
        hits = [tok for tok in tokens if tok.lower() in blob]
        if hits:
            scored.append((len(hits), int(page.page_no or 0), page.text or "", hits))
    scored.sort(key=lambda row: (-row[0], row[1]))
    sources = []
    seen_pages: set[int] = set()
    for _n, page_no, text, hits in scored[:4]:
        seen_pages.add(page_no)
        needle = hits[0].lower()
        idx = text.lower().find(needle)
        start = max(0, (idx if idx >= 0 else 0) - 40)
        sources.append(
            {
                "id": f"page:{item.id}:{page_no}",
                "kind": "page",
                "title": item.title or "",
                "page": page_no,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?page={page_no}",
                "item_id": item.id,
                "excerpt": _clip(text[start : start + 280], 280),
            }
        )
    try:
        kb_hits, _mode = query_kb(ctx.db, ctx.lab_id, query, top_k=4, user_id=ctx.user_id, item_id=item.id)
    except Exception:
        kb_hits = []
    for hit in kb_hits:
        try:
            page_no = int(hit.get("page_no") or 0)
        except (TypeError, ValueError):
            page_no = 0
        if page_no and page_no in seen_pages:
            continue
        if page_no:
            seen_pages.add(page_no)
        sources.append(
            {
                "id": f"kb:{hit.get('chunk_id')}",
                "kind": "kb_chunk",
                "title": item.title or hit.get("filename") or "",
                "page": page_no or None,
                "href": hit.get("open_href") or f"/labs/{ctx.lab_id}/literature/{item.id}",
                "item_id": item.id,
                "excerpt": _clip(hit.get("content") or "", 280),
            }
        )
        if len(sources) >= 6:
            break
    if not sources:
        return ToolResult(text="这一篇里没有对上这段话的页。", sources=[], data={"empty": True})
    return ToolResult(text=f"在这一篇里对上 {len(sources)} 处", sources=sources)


@tool(
    "get_paper_section",
    scopes=["paper", "lab", "writing"],
    description="按节名或页范围取这一节的正文。讲某一节时用这个，不要只猜一页。",
    params={
        "properties": {
            "title": {"type": "string", "description": "节名，如方法、实验"},
            "page_from": {"type": "integer"},
            "page_to": {"type": "integer"},
            "item_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("讲这一节", "这一节", "方法节", "实验节"),
)
def get_paper_section(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.pagecache import item_pages, item_structure

    pages, _src = item_pages(ctx.db, item)
    structure = item_structure(ctx.db, item.id)
    sections = [sec for sec in (structure.get("sections") or []) if isinstance(sec, dict)]
    title = str(args.get("title") or "").strip()
    start = 0
    end = 0
    picked = ""
    if title:
        for i, sec in enumerate(sections):
            name = str(sec.get("title") or sec.get("heading") or "")
            if title.lower() not in name.lower() and name.lower() not in title.lower():
                continue
            try:
                start = int(sec.get("page_no") or sec.get("page") or 0)
            except (TypeError, ValueError):
                start = 0
            end = start
            for nxt in sections[i + 1 :]:
                try:
                    nxt_page = int(nxt.get("page_no") or nxt.get("page") or 0)
                except (TypeError, ValueError):
                    nxt_page = 0
                if nxt_page > start:
                    end = nxt_page - 1
                    break
            if end <= start:
                end = start + 2
            picked = name or title
            break
    if not start:
        try:
            start = int(args.get("page_from") or 0)
            end = int(args.get("page_to") or 0)
        except (TypeError, ValueError):
            start, end = 0, 0
    if start < 1 or not pages:
        return ToolResult(text="这一节还对不上页码。", data={"empty": True})
    end = max(start, min(end or start, start + 3, len(pages)))
    sources = []
    for page_no in range(start, end + 1):
        text = pages[page_no - 1] if page_no - 1 < len(pages) else ""
        if not (text or "").strip():
            continue
        sources.append(
            {
                "id": f"page:{item.id}:{page_no}",
                "kind": "page",
                "title": picked or item.title or "",
                "page": page_no,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?page={page_no}",
                "item_id": item.id,
                "excerpt": _clip(text, 900),
            }
        )
    if not sources:
        return ToolResult(text="这一节没有可复制文字。", data={"empty": True})
    label = picked or f"第 {start}–{end} 页"
    return ToolResult(text=f"「{label}」取到 {len(sources)} 页", sources=sources)


@tool(
    "get_paper_structure",
    scopes=["paper", "lab", "writing"],
    description="取当前文献的章节与图表结构树（含页码）。想定位「方法在第几页」「有几张图」时先调这个，再去取对应页正文。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    stub_keywords=("章节", "结构", "目录", "图表", "在第几页", "定位", "方法节", "实验节", "哪张图", "图1", "公式", "算法", "式1"),
)
def get_paper_structure(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.pagecache import item_structure

    st = item_structure(ctx.db, item.id)
    secs = st.get("sections") or []
    figs = st.get("figures") or []
    eqs = st.get("equations") or []
    algs = st.get("algorithms") or []
    if not secs and not figs and not eqs and not algs:
        bits = _file_parse_bits(ctx, item)
        hint = str(bits.get("parse_hint") or "")
        return ToolResult(
            text=_clip((hint + "。" if hint else "") + "这篇还没有结构树（GROBID 未解析或是扫描件）", 240),
            data={"empty": True, "parse_status": bits.get("parse_status") or []},
        )
    asked = _ctx_question(ctx)
    fig_hit = _figure_hit(asked, figs) or _equation_hit(asked, eqs) or _algorithm_hit(asked, algs)
    hit = fig_hit or _section_hit(asked, secs)
    lines = [f"{s.get('title') or ''}（p.{s.get('page') or '?'}）" for s in secs[:40]]
    fig_lines = [f"{f.get('label') or f.get('caption') or ''}（p.{f.get('page') or '?'}）" for f in figs[:30]]
    fig_lines += [f"{f.get('label') or f.get('caption') or ''}（p.{f.get('page') or '?'}）" for f in (eqs + algs)[:20]]
    sources = []
    if hit:
        page = hit.get("page")
        href = f"/labs/{ctx.lab_id}/literature/{item.id}" + (f"?page={page}" if page else "")
        label = hit.get("title") or hit.get("label") or hit.get("caption") or ("图/表" if fig_hit else "章节")
        sources.append(
            {
                "id": f"struct-sec:{item.id}:{label or page}",
                "kind": "structure",
                "title": label,
                "page": page,
                "href": href,
                "item_id": item.id,
                "excerpt": _clip(f"问句点名｜{label}在第 {page or '?'} 页", 240),
            }
        )
    sources.append(
        {
            "id": f"struct:{item.id}",
            "kind": "structure",
            "title": item.title,
            "page": (hit or {}).get("page"),
            "href": f"/labs/{ctx.lab_id}/literature/{item.id}",
            "item_id": item.id,
            "excerpt": _clip("；".join(lines + fig_lines), 1200),
        }
    )
    lead_name = (hit.get("title") or hit.get("label") or hit.get("caption")) if hit else None
    lead = (
        f"{lead_name}在第 {hit.get('page') or '?'} 页"
        if hit
        else "章节：" + "；".join(lines)
    )
    if fig_lines and not hit:
        lead += "｜图表：" + "；".join(fig_lines)
    return ToolResult(text=lead, sources=sources, data=st)


@tool(
    "get_paper_biblio",
    scopes=["paper", "lab", "writing"],
    description="取这篇已登记的书目：作者、年份、期刊/会议、DOI、arXiv、PDF 解析状态。回答「这篇哪年发的、DOI 是什么、有没有 PDF」必须先调这个，缺项照实说，不要编。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    stub_keywords=("这篇doi", "哪年发", "发在哪个", "这篇作者", "有没有pdf", "这篇书目", "arxiv号", "什么期刊", "发表年份", "年那篇", "会上那篇", "期刊那篇", "那篇是哪篇", "pmid"),
)
def get_paper_biblio(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.literature import item_to_bibtex, paper_dto

    dto = paper_dto(item)
    bits = _file_parse_bits(ctx, item)
    authors = dto.get("authors") or []
    if isinstance(authors, list):
        author_txt = "、".join(str(a) for a in authors[:8] if a)
    else:
        author_txt = str(authors or "")
    year = dto.get("publish_year") or "未登记年份"
    venue = dto.get("venue") or "未登记期刊/会议"
    doi = dto.get("doi") or "未登记 DOI"
    arxiv = dto.get("arxiv_id") or ""
    pmid = dto.get("pubmed_id") or ""
    pdf = bits.get("parse_hint") or "没有登记 PDF"
    excerpt = (
        f"{author_txt or '未登记作者'}｜{year}｜{venue}｜DOI {doi}"
        + (f"｜arXiv {arxiv}" if arxiv else "")
        + (f"｜PMID {pmid}" if pmid else "")
        + f"｜{pdf}"
    )
    bib = item_to_bibtex(item)
    return ToolResult(
        text=_clip(excerpt, 300),
        sources=[
            {
                "id": f"biblio:{item.id}",
                "kind": "biblio",
                "title": item.title,
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}",
                "item_id": item.id,
                "excerpt": _clip(excerpt + "｜" + bib, 1200),
            }
        ],
        data={
            "doi": dto.get("doi"),
            "arxiv_id": dto.get("arxiv_id"),
            "venue": dto.get("venue"),
            "publish_year": dto.get("publish_year"),
            "parse_status": bits.get("parse_status") or [],
        },
    )


@tool(
    "get_method_card",
    scopes=["paper", "lab", "writing"],
    description="取这篇已生成的方法卡（问题、主张、方法、数据集、指标、基线、局限）。每条都带页码，未接地的主张会单独列出原文。回答「哪些主张没依据」必须先调这个。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    stub_keywords=("方法卡", "主张", "指标", "数据集", "基线", "局限", "贡献", "没依据", "无依据"),
)
def get_method_card(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.analysis import card_quality, claim_rows, load_best_method_card

    row, card = load_best_method_card(ctx.db, item.id)
    if not card:
        href = f"/labs/{ctx.lab_id}/literature/{item.id}?panel=card"
        nxt = {
            "title": "这篇还没有方法卡",
            "text": "先生成方法卡再问主张和指标，不要凭标题编。",
            "steps": [{"key": "card", "label": "打开方法卡", "href": href}],
        }
        return ToolResult(
            text="这篇还没有方法卡。可以先生成方法卡再回答结构化问题。",
            sources=[
                {
                    "id": f"cardmiss:{item.id}",
                    "kind": "method_card",
                    "title": item.title or "方法卡",
                    "page": None,
                    "href": href,
                    "excerpt": nxt["text"],
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )

    problem = card.get("problem")
    if isinstance(problem, dict):  # 接地后的块是 {text, pages, ungrounded}
        problem = problem.get("text") or ""
    quality = card_quality(card)
    origin = "仅摘要卡" if quality.get("abstract_only") else ("解析失败" if quality.get("source") == "parse_failed" else "有全文")
    ung = int(quality.get("ungrounded_count") or 0)
    href = f"/labs/{ctx.lab_id}/literature/{item.id}?panel=card"
    sources = [
        {
            "id": f"card:{item.id}",
            "kind": "method_card",
            "title": item.title,
            "page": None,
            "href": href,
            "item_id": item.id,
            "excerpt": _clip(
                f"{origin}｜无依据 {ung} 条｜" + json.dumps(card, ensure_ascii=False),
                2500,
            ),
        }
    ]
    for i, claim in enumerate(claim_rows(card)):
        if not claim.get("ungrounded"):
            continue
        if len(sources) >= 8:
            break
        page = claim.get("page_no")
        claim_href = href + (f"&page={page}" if page else "")
        sources.append(
            {
                "id": f"card-ug:{item.id}:{i}",
                "kind": "method_card_claim",
                "title": "无依据主张",
                "page": page,
                "href": claim_href,
                "item_id": item.id,
                "excerpt": _clip(str(claim.get("text") or ""), 400),
            }
        )
    related = card.get("related_in_lab") if isinstance(card.get("related_in_lab"), list) else []
    for rel in related[:4]:
        if not isinstance(rel, dict):
            continue
        rid = str(rel.get("literature_item_id") or "").strip()
        if not rid:
            continue
        sources.append(
            {
                "id": f"card-rel:{item.id}:{rid}",
                "kind": "literature",
                "title": rel.get("title") or "组内相关",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{rid}",
                "item_id": rid,
                "excerpt": _clip(f"方法卡标组内相关：{rel.get('reason') or ''}", 240),
            }
        )
    return ToolResult(
        text=_clip(f"{problem or ''}｜{origin}｜无依据 {ung} 条", 300),
        sources=sources,
        data={"card": card, "analysis_id": getattr(row, "id", ""), "quality": quality},
    )


@tool(
    "get_reproduce_evidence",
    scopes=["paper", "lab", "writing"],
    description="取这篇的复现证据：是否实测、实测指标、诊断结论。任何「复现成功」的说法都必须以这里的实测为准，没实测就只能说尚未实测。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    stub_keywords=("复现", "实测", "跑通", "成功", "证据", "gpu", "声称和实测", "差距报告"),
)
def get_reproduce_evidence(ctx: ToolContext, args: dict) -> ToolResult:
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.services.analysis import claimed_metrics_from_card, load_best_method_card
    from fengsuan.app.services.reproduce import evidence_brief, latest_reproduce_evidence

    ev = latest_reproduce_evidence(ctx.db, item.id)
    claimed = dict((ev or {}).get("claimed_metrics") or {})
    if not claimed:
        _row, card = load_best_method_card(ctx.db, item.id)
        claimed = claimed_metrics_from_card(card) if card else {}
    sources = []
    if ev:
        sources.append(
            {
                "id": f"run:{ev.get('id')}",
                "kind": "reproduce",
                "title": item.title,
                "page": None,
                "href": ev.get("href") or "",
                "item_id": item.id,
                "excerpt": _clip(
                    f"实测：{'是' if ev.get('measured') else '否'}｜"
                    f"指标：{json.dumps(ev.get('actual_metrics') or {}, ensure_ascii=False)}｜{evidence_brief(ev)}",
                    600,
                ),
            }
        )
        gap = str(ev.get("gap_excerpt") or "").strip()
        if gap:
            sources.append(
                {
                    "id": f"run-gap:{ev.get('id')}",
                    "kind": "reproduce_gaps",
                    "title": "声称与实测差距",
                    "page": None,
                    "href": ev.get("href") or "",
                    "item_id": item.id,
                    "excerpt": _clip(gap, 600),
                }
            )
    if claimed:
        sources.append(
            {
                "id": f"claimed:{item.id}",
                "kind": "claimed_metrics",
                "title": "论文声称（不是实测）",
                "page": None,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?panel=card",
                "item_id": item.id,
                "excerpt": _clip("论文声称，不得当成组内实测：" + json.dumps(claimed, ensure_ascii=False), 400),
            }
        )
    if not ev:
        href = f"/labs/{ctx.lab_id}/reproduces?literature_item_id={item.id}"
        nxt = {
            "title": "这篇还没有复现记录",
            "text": "去复现页提交后再问实测。没有实测不得声称已复现。",
            "steps": [{"key": "repro", "label": "去复现", "href": href}],
        }
        return ToolResult(
            text="这篇还没有复现记录。不得声称已复现。",
            sources=sources,
            data={"measured": False, "claimed_metrics": claimed, "empty": True, "empty_next": nxt},
        )
    return ToolResult(
        text=evidence_brief(ev),
        sources=sources,
        data={"measured": bool(ev.get("measured")), "honest": ev.get("honest"), "claimed_metrics": claimed},
    )


@tool(
    "get_my_notes",
    scopes=["paper", "writing"],
    description="取当前文献里提问者自己的页级笔记与高亮。只返回本人写的，不看别人的笔记。",
    params={
        "properties": {
            "item_id": {"type": "string"},
            "page_no": {"type": "integer", "description": "只看某一页；不填则看这篇全部本人笔记"},
        },
        "required": [],
    },
    stub_keywords=("我的笔记", "页注", "我写过", "批注", "记过"),
)
def get_my_notes(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    item = _resolve_literature_item(ctx, args)
    if item is None:
        return _literature_miss(ctx)
    from fengsuan.app.models.literature import LiteratureNote
    from fengsuan.app.services.literature import literature_open_href

    q = ctx.db.query(LiteratureNote).filter(
        LiteratureNote.item_id == item.id,
        LiteratureNote.user_id == ctx.user_id,
        LiteratureNote.deleted_at.is_(None),
    )
    page_no = _resolve_page_no(ctx, item, args)
    if page_no:
        q = q.filter(LiteratureNote.page_no == page_no)
    rows = q.order_by(LiteratureNote.created_at.desc()).limit(20).all()
    sources = [
        {
            "id": f"note:{n.id}",
            "kind": "note",
            "title": item.title,
            "page": n.page_no,
            "href": f"/labs/{ctx.lab_id}{literature_open_href(item.id, n.page_no, note_id=n.id, panel='notes')}",
            "item_id": item.id,
            "excerpt": _clip(n.content_md or "", 600),
        }
        for n in rows
    ]
    if not sources:
        href = f"/labs/{ctx.lab_id}{literature_open_href(item.id, page_no or 1, panel='notes')}"
        nxt = {
            "title": "你还没有这篇的笔记",
            "text": "对着这一页记下再问，不要把别人的批注当成自己的。",
            "steps": [{"key": "notes", "label": "打开笔记", "href": href}],
        }
        return ToolResult(
            text=nxt["title"],
            sources=[
                {
                    "id": f"notemiss:{item.id}",
                    "kind": "note",
                    "title": nxt["title"],
                    "page": page_no,
                    "href": href,
                    "item_id": item.id,
                    "excerpt": nxt["text"],
                }
            ],
            data={"empty": True, "empty_next": nxt},
        )
    return ToolResult(text=f"你的笔记 {len(sources)} 条", sources=sources)


# ---------------------------------------------------------------- T4 写入类


@tool(
    "write_note",
    scopes=["paper", "writing"],
    description="把结论写成这篇文献的页级笔记。只在用户明确要求「记下来 / 存成笔记」时调用。",
    params={
        "properties": {
            "content_md": {"type": "string"},
            "page_no": {"type": "integer"},
            "item_id": {"type": "string"},
        },
        "required": ["content_md"],
    },
    writes=True,
    needs_confirm=True,
    stub_keywords=("记下来", "存成笔记", "写成笔记"),
)
def write_note(ctx: ToolContext, args: dict) -> ToolResult:
    item = _item(ctx, str(args.get("item_id") or ""))
    from fengsuan.app.models.literature import LiteratureNote

    body = str(args.get("content_md") or "").strip()
    if not body:
        return ToolResult(error="笔记内容为空")
    page_no = int(args.get("page_no") or 0) or None
    if not ctx.confirm:
        return ToolResult(
            text="会写成你自己的页级笔记，确认后再写入。不会改别人的笔记。",
            data={"pending_confirm": True, "item_id": item.id, "page_no": page_no, "preview": _clip(body, 400)},
        )
    row = LiteratureNote(
        item_id=item.id,
        user_id=ctx.user_id,
        content_md=body[:8000],
        page_no=page_no,
    )
    ctx.db.add(row)
    ctx.db.flush()
    return ToolResult(
        text="已存为笔记",
        data={"note_id": row.id, "page_no": row.page_no},
        sources=[
            {
                "id": f"note:{row.id}",
                "kind": "note",
                "title": item.title,
                "page": row.page_no,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?panel=notes",
                "item_id": item.id,
                "excerpt": _clip(body, 400),
            }
        ],
    )


@tool(
    "write_highlight",
    scopes=["paper"],
    description="把选中的原文存成带锚点的高亮笔记，之后能在 PDF 上点开。需要提供原文子串与页码。",
    params={
        "properties": {
            "page_no": {"type": "integer"},
            "quote": {"type": "string", "description": "原文子串，必须真的出现在该页"},
            "content_md": {"type": "string"},
            "item_id": {"type": "string"},
        },
        "required": ["page_no", "quote"],
    },
    writes=True,
    needs_confirm=True,
    stub_keywords=("高亮", "划线", "标记这段"),
)
def write_highlight(ctx: ToolContext, args: dict) -> ToolResult:
    item = _item(ctx, str(args.get("item_id") or ""))
    from fengsuan.app.models.literature import LiteratureNote
    from fengsuan.app.services.pagecache import item_single_page

    page_no = max(1, int(args.get("page_no") or 1))
    quote = str(args.get("quote") or "").strip()
    if not quote:
        return ToolResult(error="缺少原文子串")
    page_text = (item_single_page(ctx.db, item, page_no).get("page_text") or "").replace("\n", " ")
    if quote[:80] not in page_text.replace("\n", " "):
        return ToolResult(error="这段文字不在该页正文里，拒绝写入假高亮")
    if not ctx.confirm:
        return ToolResult(
            text="会把这段原文存成你的高亮笔记，确认后再写入。",
            data={"pending_confirm": True, "item_id": item.id, "page_no": page_no, "preview": _clip(quote, 400)},
        )
    row = LiteratureNote(
        item_id=item.id,
        user_id=ctx.user_id,
        content_md=(str(args.get("content_md") or quote))[:8000],
        page_no=page_no,
        anchor_json=json.dumps({"page_no": page_no, "quote": quote[:400]}, ensure_ascii=False),
    )
    ctx.db.add(row)
    ctx.db.flush()
    return ToolResult(
        text="已加高亮",
        data={"note_id": row.id, "page_no": page_no},
        sources=[
            {
                "id": f"note:{row.id}",
                "kind": "highlight",
                "title": item.title,
                "page": page_no,
                "href": f"/labs/{ctx.lab_id}/literature/{item.id}?page={page_no}&panel=notes",
                "item_id": item.id,
                "excerpt": _clip(quote, 400),
            }
        ],
    )


@tool(
    "writeback_manuscript",
    scopes=["writing"],
    description="把整理好的文字写回手稿。改的是学生的正文，必须先经用户确认，未确认只返回预览。",
    params={
        "properties": {
            "manuscript_id": {"type": "string"},
            "text": {"type": "string"},
            "heading": {"type": "string"},
            "mode": {"type": "string", "description": "append 或 replace_selection"},
        },
        "required": ["text"],
    },
    writes=True,
    needs_confirm=True,
    stub_keywords=("写回手稿", "插入正文", "写进论文"),
)
def writeback_manuscript(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    text = str(args.get("text") or "").strip()
    ms_id = str(args.get("manuscript_id") or ctx.manuscript_id or "").strip()
    if not text or not ms_id:
        return ToolResult(error="缺少手稿或内容")
    if not ctx.confirm:
        return ToolResult(
            text="这会改动手稿正文，已生成预览，等你确认后再写入。",
            data={"pending_confirm": True, "manuscript_id": ms_id, "preview": _clip(text, 1200)},
        )
    from fengsuan.app.api.v1.language import _maybe_writeback

    wrote = _maybe_writeback(
        ctx.db,
        _user(ctx),
        ms_id,
        text,
        ctx.lab_id,
        mode=str(args.get("mode") or "append"),
        heading=str(args.get("heading") or ""),
    )
    if not wrote:
        return ToolResult(error="写回未生效")
    return ToolResult(text="已写回手稿", data=dict(wrote))


# ---------------------------------------------------------------- T5 作业类


@tool(
    "submit_research_run",
    scopes=["lab", "writing"],
    description="提交一次文献调研作业（重型，排队占算力）。必须先经用户确认，未确认只返回将要提交的内容。",
    params={
        "properties": {"question": {"type": "string"}, "domain": {"type": "string"}},
        "required": ["question"],
    },
    writes=True,
    needs_confirm=True,
    stub_keywords=("提交调研", "跑一次调研", "文献调研作业"),
)
def submit_research_run(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    question = str(args.get("question") or "").strip()
    if not question:
        return ToolResult(error="缺少调研问题")
    if not ctx.confirm:
        return ToolResult(
            text="这会排一次重型作业并占用算力，确认后我才提交。",
            data={"pending_confirm": True, "question": question},
        )
    from fengsuan.app.services.research_engine import create_research_run

    try:
        run = create_research_run(
            ctx.db,
            lab_id=ctx.lab_id,
            user_id=ctx.user_id,
            question=question,
            domain=str(args.get("domain") or "general"),
        )
    except ValueError:
        return ToolResult(error="并发或预算已满，暂时不能提交")
    return ToolResult(
        text="已提交文献调研作业",
        data={"run_id": run.id, "href": f"/labs/{ctx.lab_id}/runs/{run.id}"},
    )


@tool(
    "submit_reproduce_run",
    scopes=["paper", "lab"],
    description="为这篇文献提交一次复现作业（重型，需要 GPU）。必须先经用户确认；没有实测结果之前不得声称复现成功。",
    params={"properties": {"item_id": {"type": "string"}}, "required": []},
    writes=True,
    needs_confirm=True,
    stub_keywords=("提交复现", "跑复现", "复现这篇"),
)
def submit_reproduce_run(ctx: ToolContext, args: dict) -> ToolResult:
    item = _item(ctx, str(args.get("item_id") or ""))
    if not ctx.confirm:
        return ToolResult(
            text="复现要排队占 GPU，确认后我才提交。",
            data={"pending_confirm": True, "item_id": item.id},
        )
    from fengsuan.app.services.reproduce import create_reproduce_run

    try:
        run = create_reproduce_run(ctx.db, lab_id=ctx.lab_id, user_id=ctx.user_id, literature_item_id=item.id)
    except ValueError:
        return ToolResult(error="并发或预算已满，暂时不能提交")
    return ToolResult(
        text="已提交复现作业",
        data={"run_id": run.id, "href": f"/labs/{ctx.lab_id}/reproduce/{run.id}"},
    )


# ---------------------------------------------------------------- 组里助手：简报 / 笔记 / 前沿


def _clip_line(text: str, n: int = 280) -> str:
    return (text or "").replace("\n", " ").strip()[:n]


@tool(
    "get_lab_overview",
    scopes=["lab", "onboarding"],
    description="用大白话说这个实验室整体在做什么。先看组里一页纸，不要凭记忆编。",
    params={"properties": {}, "required": []},
    stub_keywords=("介绍这个实验室", "组里一页纸", "全组概况", "大白话介绍"),
)
def get_lab_overview(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.lab import Lab
    from fengsuan.app.services.lab_brief import _loads, refresh_lab_brief

    lab = ctx.db.get(Lab, ctx.lab_id)
    if lab is None:
        return ToolResult(error="实验室不存在")
    brief = _loads(lab.brief_json)
    if not brief.get("text"):
        brief = refresh_lab_brief(ctx.db, lab)
    text = brief.get("text") or "这个组还没有写成一页纸。"
    sources = [
        {
            "id": f"lab:{ctx.lab_id}",
            "kind": "direction",
            "title": lab.name or "这个组",
            "excerpt": _clip_line(text),
            "href": f"/labs/{ctx.lab_id}/assistant",
        }
    ]
    for row in (brief.get("directions") or [])[:6]:
        if not row.get("id"):
            continue
        sources.append(
            {
                "id": f"dir:{row['id']}",
                "kind": "direction",
                "title": row.get("name") or "",
                "excerpt": _clip_line(row.get("one_line") or ""),
                "href": f"/labs/{ctx.lab_id}/directions/{row['id']}",
            }
        )
    return ToolResult(text=text, sources=sources)


@tool(
    "get_direction_brief",
    scopes=["lab", "onboarding"],
    description="讲清某一个研究方向：在做什么、代表论文、谁在做、新人先读哪几篇。",
    params={"properties": {"direction_id": {"type": "string"}, "name": {"type": "string"}}, "required": []},
    stub_keywords=("讲讲这个方向", "方向简报", "这个方向在研究"),
)
def get_direction_brief(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.services.lab_brief import _loads, refresh_direction_brief

    want = str(args.get("direction_id") or ctx.direction_id or "").strip()
    name = str(args.get("name") or "").strip()
    q = ctx.db.query(ResearchDirection).filter(ResearchDirection.lab_id == ctx.lab_id, ResearchDirection.deleted_at.is_(None))
    row = q.filter(ResearchDirection.id == want).first() if want else None
    if row is None and name:
        row = q.filter(ResearchDirection.name.contains(name[:40])).first()
    if row is None:
        row = q.order_by(ResearchDirection.sort).first()
    if row is None:
        return ToolResult(text="这个组还没有研究方向。", sources=[])
    brief = _loads(row.brief_json)
    if not brief.get("one_line"):
        brief = refresh_direction_brief(ctx.db, row)
    sources = [
        {
            "id": f"dir:{row.id}",
            "kind": "direction",
            "title": row.name,
            "excerpt": _clip_line(brief.get("one_line") or row.description or ""),
            "href": f"/labs/{ctx.lab_id}/directions/{row.id}",
        }
    ]
    for paper in (brief.get("papers") or [])[:4]:
        if not paper.get("id"):
            continue
        sources.append(
            {
                "id": f"lit:{paper['id']}",
                "kind": "literature",
                "title": paper.get("title") or "",
                "item_id": paper["id"],
                "href": f"/literature/{paper['id']}",
                "excerpt": "这个方向的代表论文",
            }
        )
    people = "、".join(p.get("name") or "" for p in (brief.get("people") or []) if p.get("name"))
    parts = [brief.get("one_line") or row.name]
    if brief.get("problem"):
        parts.append(f"要解决的是：{brief['problem']}")
    if brief.get("how"):
        parts.append(f"做法是：{brief['how']}")
    if people:
        parts.append(f"在做的人：{people}")
    titles = [p.get("title") for p in (brief.get("papers") or []) if p.get("title")]
    if titles:
        parts.append("组里这几篇：" + "、".join(f"《{t}》" for t in titles[:3]))
    words = []
    for term in (brief.get("terms") or [])[:4]:
        if not isinstance(term, dict) or not term.get("word"):
            continue
        words.append(f"{term['word']}（{term['plain']}）" if term.get("plain") else str(term["word"]))
    if words:
        parts.append("常见说法：" + "；".join(words))
    themes = [t for t in (brief.get("themes") or []) if isinstance(t, dict) and t.get("name")]
    if themes:
        parts.append(
            f"方向里分成 {len(themes)} 块："
            + "；".join(f"{t['name']}（{int(t.get('size') or 0)} 篇）" for t in themes[:8])
        )
    text = " ".join(parts)
    return ToolResult(text=text, sources=sources, data={"direction_id": row.id})


@tool(
    "get_paper_digests",
    scopes=["lab", "onboarding"],
    description="一次看多篇助手笔记，用来回答组里的论文都在讲什么、有没有做过某件事。",
    params={
        "properties": {
            "query": {"type": "string"},
            "direction_id": {"type": "string"},
        },
        "required": [],
    },
    stub_keywords=("组里的论文都", "这些论文讲", "论文都讲了", "有没有做过"),
)
def get_paper_digests(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.services.analysis import best_method_analysis, parse_card
    from fengsuan.app.services.lab_brief import plain_bits

    from sqlalchemy import or_

    from fengsuan.app.services.search_index import search_terms

    q = str(args.get("query") or "").strip().lower()
    direction_id = str(args.get("direction_id") or ctx.direction_id or "").strip()
    query = ctx.db.query(LiteratureItem).filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None))
    if direction_id:
        from fengsuan.app.services.directions import direction_scope_ids

        scope = direction_scope_ids(ctx.db, direction_id, include_children=True) or [direction_id]
        query = query.filter(
            LiteratureItem.id.in_(
                ctx.db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.direction_id.in_(scope))
            )
        )
    terms = search_terms(q)
    items: list = []
    if terms:
        conds = []
        for term in terms:
            conds.extend([LiteratureItem.title.ilike(f"%{term}%"), LiteratureItem.abstract.ilike(f"%{term}%")])
        items = query.filter(or_(*conds)).order_by(LiteratureItem.created_at.desc()).limit(60).all()
    matched = bool(items)
    if not items:
        items = query.order_by(LiteratureItem.created_at.desc()).limit(40).all()
    sources = []
    for item in items:
        row = best_method_analysis(ctx.db, item.id)
        bits = plain_bits(parse_card(row) if row else {})
        line = bits.get("one_line") or (item.abstract or "")[:160] or item.title or ""
        blob = f"{item.title} {line} {item.abstract or ''}".lower()
        if matched and q not in blob and not any(tok in blob for tok in terms):
            continue
        sources.append(
            {
                "id": f"lit:{item.id}",
                "kind": "digest",
                "title": item.title or "",
                "item_id": item.id,
                "href": f"/literature/{item.id}",
                "excerpt": _clip_line(line),
            }
        )
        if len(sources) >= 8:
            break
    if not sources:
        return ToolResult(text="组里还没有能讲的论文笔记。", sources=[])
    return ToolResult(text=f"找到 {len(sources)} 篇助手笔记", sources=sources)


@tool(
    "get_paper_digest",
    scopes=["lab", "onboarding", "paper"],
    description="用大白话讲清楚某一篇论文：解决什么、怎么做、结论、先看哪几页。",
    params={"properties": {"item_id": {"type": "string"}, "title": {"type": "string"}}, "required": []},
    stub_keywords=("大白话讲这篇", "讲讲这篇", "这篇讲了什么"),
)
def get_paper_digest(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.analysis import best_method_analysis, parse_card
    from fengsuan.app.services.lab_brief import plain_bits

    item_id = str(args.get("item_id") or ctx.item_id or "").strip()
    title = str(args.get("title") or "").strip()
    item = ctx.db.get(LiteratureItem, item_id) if item_id else None
    if item is None and title:
        item = (
            ctx.db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == ctx.lab_id, LiteratureItem.deleted_at.is_(None), LiteratureItem.title.contains(title[:60]))
            .first()
        )
    if item is None or item.lab_id != ctx.lab_id or item.deleted_at is not None:
        return ToolResult(text="没有找到这一篇。", sources=[])
    row = best_method_analysis(ctx.db, item.id)
    card = parse_card(row) if row else {}
    bits = plain_bits(card)
    parts = [bits.get("one_line"), bits.get("problem"), bits.get("how"), bits.get("result")]
    text = " ".join(p for p in parts if p) or (item.abstract or item.title or "")
    if card.get("source") == "abstract_only":
        text = f"只读了摘要。{text}"
    pages = bits.get("start_pages") or []
    if pages:
        text = f"{text} 建议先看第 {'、'.join(str(p) for p in pages[:4])} 页。"
    return ToolResult(
        text=_clip_line(text, 400),
        sources=[
            {
                "id": f"lit:{item.id}",
                "kind": "digest",
                "title": item.title or "",
                "item_id": item.id,
                "href": f"/literature/{item.id}",
                "excerpt": _clip_line(text),
                "page": pages[0] if pages else None,
            }
        ],
    )


@tool(
    "get_member_work",
    scopes=["lab", "onboarding"],
    description="说某个成员正在做什么：方向、在写的论文、在读的篇。不要评价进度。",
    params={"properties": {"user_id": {"type": "string"}, "name": {"type": "string"}}, "required": []},
    stub_keywords=("谁在做", "同学在做", "成员在做"),
)
def get_member_work(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.services.lab_brief import refresh_member_work

    uid = str(args.get("user_id") or "").strip()
    name = str(args.get("name") or "").strip()
    mem = None
    if uid:
        mem = (
            ctx.db.query(LabMembership)
            .filter(LabMembership.lab_id == ctx.lab_id, LabMembership.user_id == uid)
            .first()
        )
    if mem is None and name:
        user = ctx.db.query(User).filter(User.display_name.contains(name[:40])).first()
        if user is not None:
            mem = (
                ctx.db.query(LabMembership)
                .filter(LabMembership.lab_id == ctx.lab_id, LabMembership.user_id == user.id)
                .first()
            )
    if mem is None and not uid and not name:
        named_uid = _member_named_in_question(ctx)
        if named_uid:
            mem = (
                ctx.db.query(LabMembership)
                .filter(LabMembership.lab_id == ctx.lab_id, LabMembership.user_id == named_uid)
                .first()
            )

    def _work_src(row, brief: dict) -> dict:
        person = ctx.db.get(User, row.user_id)
        who = (person.display_name or person.email) if person is not None else ""
        return {
            "id": f"member:{row.user_id}",
            "kind": "member",
            "title": who or "这位",
            "role": row.role,
            "member_title": row.title or "",
            "href": f"/labs/{ctx.lab_id}/cockpit/members/{row.user_id}",
            "excerpt": _clip_line(brief.get("text") or ""),
        }

    def _paper_sources(brief: dict) -> list[dict]:
        from fengsuan.app.services.lab_brief import _read_href

        out = []
        for src in brief.get("sources") or []:
            if src.get("kind") != "literature" or not src.get("id"):
                continue
            page = src.get("page")
            try:
                page_no = int(page or 0)
            except (TypeError, ValueError):
                page_no = 0
            out.append(
                {
                    "id": f"lit:{src['id']}",
                    "kind": "literature",
                    "item_id": src["id"],
                    "title": src.get("title") or "这篇",
                    "href": _read_href(src["id"], page_no),
                    "page": page_no if page_no >= 2 else None,
                    "excerpt": _clip_line(brief.get("text") or ""),
                }
            )
        return out

    if mem is None:
        rows = ctx.db.query(LabMembership).filter(LabMembership.lab_id == ctx.lab_id).limit(12).all()
        sources = []
        lines = []
        for row in rows:
            brief = refresh_member_work(ctx.db, ctx.lab_id, row)
            lines.append(brief.get("text") or "")
            sources.append(_work_src(row, brief))
            sources.extend(_paper_sources(brief))
        return ToolResult(text="；".join(x for x in lines if x)[:400] or "组里还没有成员分工。", sources=sources[:12])
    brief = refresh_member_work(ctx.db, ctx.lab_id, mem)
    return ToolResult(
        text=brief.get("text") or "",
        sources=[_work_src(mem, brief), *_paper_sources(brief)][:8],
    )


@tool(
    "get_direction_frontier",
    scopes=["lab", "onboarding"],
    description="看这个方向外面最近的论文、相关作者和值得先读的。传 topic 时按任意主题查，不写进方向缓存。必须说明这些还没放进这个组。",
    params={"properties": {"direction_id": {"type": "string"}, "topic": {"type": "string"}}, "required": []},
    stub_keywords=("外面在做", "前沿", "相关作者", "该读什么", "推荐阅读"),
)
def get_direction_frontier(ctx: ToolContext, args: dict) -> ToolResult:
    _member(ctx)
    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.services.frontier import _loads, refresh_direction_frontier

    topic = str(args.get("topic") or "").strip()
    if topic:
        from fengsuan.app.services.frontier import frontier_for_topic

        years = int(args.get("years") or 2)
        queries = args.get("queries") if isinstance(args.get("queries"), list) else None
        plan = args.get("plan") if isinstance(args.get("plan"), dict) else None
        focus = str(args.get("focus") or "")
        from fengsuan.app.services.terms import derive_terms, glossary_terms

        gloss = glossary_terms(ctx.db, ctx.lab_id, topic)
        base = [str(x).strip() for x in (queries or []) if str(x).strip()]
        if not base:
            base = derive_terms(topic, db=ctx.db, lab_id=ctx.lab_id)
        merged_queries: list[str] = []
        for bit in gloss + base:
            if bit and bit not in merged_queries:
                merged_queries.append(bit)
        queries = merged_queries or None
        front = frontier_for_topic(topic, years=years, queries=queries, plan=plan, focus=focus)
        try:
            from fengsuan.app.services.agent.parse import rerank_papers

            front["papers"] = rerank_papers(ctx.db, ctx.user_id, ctx.lab_id, topic, front.get("papers") or [])
        except Exception:
            pass
        sources = []
        remembered = []
        for paper in (front.get("papers") or [])[:12]:
            sources.append(
                {
                    "id": f"ext:{paper.get('doi') or paper.get('openalex_id') or paper.get('title')}",
                    "kind": "external",
                    "title": paper.get("title") or "",
                    "href": paper.get("url") or "",
                    "url": paper.get("url") or "",
                    "excerpt": _clip_line(paper.get("abstract") or paper.get("why") or ""),
                    "doi": paper.get("doi") or "",
                    "arxiv_id": paper.get("arxiv_id") or "",
                    "openalex_id": paper.get("openalex_id") or "",
                    "pdf_url": paper.get("pdf_url") or "",
                    "cluster_id": paper.get("cluster_id") or "",
                    "cnki_id": paper.get("cnki_id") or "",
                    "year": paper.get("year"),
                    "citation_count": paper.get("citation_count") or 0,
                    "authors": paper.get("authors") or [],
                    "venue": paper.get("venue") or "",
                    "shelf": paper.get("shelf") or "",
                    "why": paper.get("why") or "",
                    "external": True,
                }
            )
            remembered.append(
                {
                    "n": len(remembered) + 1,
                    "title": paper.get("title") or "",
                    "doi": paper.get("doi") or "",
                    "url": paper.get("url") or "",
                    "arxiv_id": paper.get("arxiv_id") or "",
                    "openalex_id": paper.get("openalex_id") or "",
                    "pdf_url": paper.get("pdf_url") or "",
                }
            )
        for author in (front.get("authors") or [])[:4]:
            sources.append(
                {
                    "id": f"field-author:{topic}:{author.get('name')}",
                    "kind": "scholar",
                    "who": "person",
                    "meter": "in_papers",
                    "title": author.get("name") or "",
                    "excerpt": author.get("said") or "",
                    "external": True,
                }
            )
        text = front.get("note") or f"「{topic}」外面最近能看到这些。都还没放进这个组。"
        # D-HONEST：模型只挑了一部分时，要写清还剩多少没选上。
        pool_total = int(front.get("merged_total") or 0)
        failed = front.get("failed_sources") or {}
        shortlist = front.get("papers") or []
        if failed:
            names = "、".join(list(failed)[:3])
            text = f"这次 {names} 等源没连上。{text}"
        if pool_total and len(shortlist) < pool_total:
            text = f"{text} 候选 {pool_total} 篇里选出了 {len(shortlist)} 篇，还有 {pool_total - len(shortlist)} 篇没选上。"
        return ToolResult(
            text=text,
            sources=sources,
            data={
                "topic": topic,
                "remember": {"topic": topic, "papers": remembered},
                "pool_total": pool_total,
                "shortlisted": len(shortlist),
                "failed_sources": failed,
            },
        )
    want = str(args.get("direction_id") or ctx.direction_id or "").strip()
    q = ctx.db.query(ResearchDirection).filter(ResearchDirection.lab_id == ctx.lab_id, ResearchDirection.deleted_at.is_(None))
    row = q.filter(ResearchDirection.id == want).first() if want else None
    if row is None:
        row = q.filter(ResearchDirection.parent_id.is_(None)).order_by(ResearchDirection.sort).first()
    if row is None:
        return ToolResult(text="还没有方向，没法向外看。", sources=[])
    front = _loads(row.frontier_json)
    if not front.get("papers"):
        front = refresh_direction_frontier(ctx.db, ctx.lab_id, row)
    sources = []
    for paper in (front.get("papers") or [])[:6]:
        sources.append(
            {
                "id": f"ext:{paper.get('doi') or paper.get('title')}",
                "kind": "external",
                "title": paper.get("title") or "",
                "href": paper.get("url") or "",
                "excerpt": "还没放进这个组。" + _clip_line(paper.get("abstract") or ""),
                "doi": paper.get("doi") or "",
                "arxiv_id": paper.get("arxiv_id") or "",
                "openalex_id": paper.get("openalex_id") or "",
                "pdf_url": paper.get("pdf_url") or "",
                "cnki_id": paper.get("cnki_id") or "",
                "year": paper.get("year"),
                "authors": paper.get("authors") or [],
            }
        )
    remembered = []
    for paper in (front.get("papers") or [])[:6]:
        remembered.append(
            {
                "n": len(remembered) + 1,
                "title": paper.get("title") or "",
                "doi": paper.get("doi") or "",
                "url": paper.get("url") or "",
                "openalex_id": paper.get("openalex_id") or "",
                "pdf_url": paper.get("pdf_url") or "",
                "cnki_id": paper.get("cnki_id") or "",
            }
        )
    authors = "；".join(a.get("said") or a.get("name") or "" for a in (front.get("authors") or [])[:4])
    text = f"{row.name}外面最近能看到这些。都还没放进这个组。"
    if authors:
        text = f"{text} 相关作者：{authors}。"
    if front.get("note"):
        text = f"{text} {front['note']}"
    return ToolResult(
        text=text,
        sources=sources,
        data={"direction_id": row.id, "themes": front.get("themes") or [], "remember": {"papers": remembered, "topic": row.name}},
    )
