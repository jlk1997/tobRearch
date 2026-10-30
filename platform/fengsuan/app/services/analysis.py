from __future__ import annotations

import json
import re
import unicodedata
import uuid

from sqlalchemy.orm import Session

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureFile, LiteratureItem
from fengsuan.app.services.language import TUTOR_FOLLOWUP_SEL, clip_preview
from fengsuan.app.services.llm import chat, iter_chat_deltas, strip_model_reasoning
from fengsuan.app.services.prompts import load_prompt

_CITE_MACROS = ("cite", "citep", "citet", "citealp", "citeyear")
CARD_CHUNK_CHARS = 12000
MAX_CARD_BATCHES = 4
FAST_CARD_CHARS = 8000
SHORT_PAPER_BATCHES = 3
FAST_COVERAGE = "先读了摘要和方法开头，方法和实验细节还在补。"
FULL_COVERAGE = "整篇读完。"
CARD_SCHEMA = 2
RUNNING_STALE_S = 15 * 60
PREREAD_FAIL_RETRY_S = 10 * 60
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _item_plain(item) -> str:
    """抽出给人看的一句。不要把整段 JSON 当正文。"""
    if isinstance(item, str):
        blob = item.strip()
        return "" if blob.startswith("{") or blob.startswith("[") else blob
    if not isinstance(item, dict):
        return ""
    for key in ("text", "claim", "said", "support", "description", "raw"):
        val = item.get(key)
        if isinstance(val, str) and val.strip() and not val.strip().startswith(("{", "[")):
            return val.strip()
    name = str(item.get("name") or item.get("metric") or item.get("key") or "").strip()
    val = item.get("value") if "value" in item else item.get("claimed")
    if val is None:
        val = item.get("score")
    if name and val is not None and str(val).strip():
        return f"{name} = {val}"
    return name


def _as_block(val, default_pages=None) -> dict | list:
    if isinstance(val, list):
        return [x for x in (_as_claim(i) for i in val) if x]
    if isinstance(val, dict):
        text = _item_plain(val)
        out = {"text": text, "pages": val.get("pages") or default_pages or []}
        if val.get("ungrounded"):
            out["ungrounded"] = True
        if val.get("unverified"):
            out["unverified"] = True
        quote = str(val.get("quote") or "").strip()
        if quote:
            out["quote"] = quote[:400]
        if val.get("said"):
            out["said"] = val.get("said")
        support = val.get("support") or val.get("evidence")
        if support and str(support).strip() and str(support).strip() != text:
            out["support"] = str(support).strip()
        return out
    if isinstance(val, str):
        return {"text": val.strip(), "pages": default_pages or []}
    return {"text": str(val or "").strip(), "pages": default_pages or []}


def _as_claim(item) -> dict | None:
    if isinstance(item, str):
        text = item.strip()
        return {"text": text, "pages": []} if text else None
    if not isinstance(item, dict):
        return None
    text = _item_plain(item)
    support = str(item.get("support") or item.get("evidence") or "").strip()
    if not text and not support:
        return None
    out = {"text": text or support, "pages": item.get("pages") or []}
    quote = str(item.get("quote") or "").strip()
    if quote:
        out["quote"] = quote[:400]
    if support and support != out["text"]:
        out["support"] = support
    if item.get("ungrounded"):
        out["ungrounded"] = True
    if item.get("unverified"):
        out["unverified"] = True
    if item.get("said"):
        out["said"] = item.get("said")
    return out


def _plain_block(raw) -> dict:
    if isinstance(raw, str):
        text = raw.strip()
        return {"text": text, "pages": []} if text else {"text": "", "pages": []}
    if isinstance(raw, dict):
        block = _as_block(raw)
        return block if isinstance(block, dict) else {"text": "", "pages": []}
    return {"text": "", "pages": []}


def _normalize_plain(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    terms = []
    for row in src.get("terms") or []:
        if isinstance(row, dict):
            word = str(row.get("word") or row.get("term") or "").strip()
            said = str(row.get("plain") or row.get("text") or "").strip()
        elif isinstance(row, str):
            word, said = row.strip(), ""
        else:
            continue
        if word:
            terms.append({"word": word[:40], "plain": said[:200]})
    pages = []
    for n in src.get("start_pages") or []:
        try:
            pages.append(int(n))
        except (TypeError, ValueError):
            continue
    return {
        "one_line": _plain_block(src.get("one_line")),
        "problem": _plain_block(src.get("problem")),
        "how": _plain_block(src.get("how")),
        "result": _plain_block(src.get("result")),
        "direction": _plain_block(src.get("direction")),
        "start_pages": pages[:6],
        "terms": terms[:5],
    }


def _normalize_card(raw: dict, *, source: str, warning: str | None = None) -> dict:
    metrics = raw.get("metrics")
    card = {
        "problem": _as_block(raw.get("problem"), [1] if source == "abstract_only" else []),
        "claims": [_as_claim(c) for c in (raw.get("claims") or []) if _as_claim(c)]
        if isinstance(raw.get("claims"), list)
        else [],
        "methods": _as_block(raw.get("methods")),
        "datasets": _as_block(raw.get("datasets")),
        "metrics": metrics if isinstance(metrics, (list, dict)) else [],
        "baselines": _as_block(raw.get("baselines")),
        "limitations": _as_block(raw.get("limitations")),
        "reusable": _as_block(raw.get("reusable")),
        "related_in_lab": raw.get("related_in_lab") if isinstance(raw.get("related_in_lab"), list) else [],
        "plain": _normalize_plain(raw.get("plain")),
        "source": source,
        "schema": CARD_SCHEMA,
    }
    if warning:
        card["warning"] = warning
    return card


def readable_method_card(card: dict | None) -> dict:
    """读库时抬升 claim/said，旧卡也不要把 JSON 键摊给侧栏。"""
    if not isinstance(card, dict):
        return {}
    out = dict(card)

    def _lift(block, *, as_claim: bool = False):
        if isinstance(block, list):
            return [row for row in (_lift(x, as_claim=as_claim) for x in block) if row]
        if isinstance(block, str):
            return {"text": block.strip(), "pages": []} if block.strip() else {"text": "", "pages": []}
        if not isinstance(block, dict):
            return block
        had_text = bool(str(block.get("text") or "").strip())
        row = _as_claim(block) if as_claim else _as_block(block)
        if not isinstance(row, dict):
            return row
        if not had_text and (row.get("text") or ""):
            row["said"] = row.get("said") or row["text"]
        return row

    for key in ("problem", "methods", "datasets", "baselines", "limitations", "reusable"):
        if key in out:
            out[key] = _lift(out[key])
    if isinstance(out.get("claims"), list):
        out["claims"] = _lift(out["claims"], as_claim=True)
    raw_m = out.get("metrics")
    if isinstance(raw_m, dict) and not any(k in raw_m for k in ("name", "value", "text", "metric")):
        out["metrics"] = [{"name": k, "value": v, "ungrounded": True} for k, v in raw_m.items()]
    elif isinstance(raw_m, list):
        out["metrics"] = _lift(raw_m, as_claim=True)
    if out.get("schema") != CARD_SCHEMA:
        def _quiet(block):
            if isinstance(block, list):
                return [_quiet(row) for row in block]
            if isinstance(block, dict) and block.get("ungrounded"):
                row = dict(block)
                row.pop("ungrounded", None)
                row["unverified"] = True
                return row
            return block

        for key in ("problem", "methods", "datasets", "baselines", "limitations", "reusable", "claims", "metrics"):
            if key in out:
                out[key] = _quiet(out[key])
    return out


_HYPHEN_BREAK = re.compile(r"-\s+")
_WS_RUN = re.compile(r"\s+")
_TIGHT = re.compile(r"[-\s]+")
_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
_QUOTE_TOKEN = re.compile(r"[a-z0-9\u4e00-\u9fff]{2,}")


def fold_excerpt(text: str) -> str:
    """折掉连字、弯引号、软连字符、换行断词与多余空白。对照原文用。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(_LIGATURES).translate(_QUOTES).replace("\u00ad", "")
    t = _HYPHEN_BREAK.sub("", t)
    return _WS_RUN.sub(" ", t).strip().lower()


def fold_excerpt_tight(text: str) -> str:
    return _TIGHT.sub("", fold_excerpt(text))


def _quote_tokens(text: str) -> set[str]:
    return set(_QUOTE_TOKEN.findall(fold_excerpt(text)))


def _page_nos(page_nos: list | None) -> list[int]:
    out: list[int] = []
    for raw in page_nos or []:
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def locate_quote(quote: str, pages: list[str], page_nos: list | None = None) -> int | None:
    """先在所标页及邻页找原句，找不到再找全篇。允许约八成词重合。"""
    folded = fold_excerpt(quote)
    if not folded:
        return None
    tight = fold_excerpt_tight(quote)
    total = len(pages or [])
    order: list[int] = []
    seeds = [n for n in _page_nos(page_nos) if 1 <= n <= total]
    for seed in seeds:
        for delta in (0, -1, 1):
            page = seed + delta
            if 1 <= page <= total and page not in order:
                order.append(page)
    for page in range(1, total + 1):
        if page not in order:
            order.append(page)
    best_page = None
    best_score = 0.0
    tokens = _quote_tokens(quote)
    for page in order:
        blob = pages[page - 1] or ""
        if folded in fold_excerpt(blob) or (tight and len(tight) >= 8 and tight in fold_excerpt_tight(blob)):
            return page
        if not tokens:
            continue
        score = len(tokens & _quote_tokens(blob)) / len(tokens)
        if score > best_score:
            best_score = score
            best_page = page
    if best_score >= 0.8:
        return best_page
    return None


def excerpt_in_pages(text: str, pages: list[str], page_nos: list | None) -> bool:
    """非空摘录必须能在原文里找到。空串无主张，不算编造。"""
    if not fold_excerpt(text):
        return True
    return locate_quote(text, pages, page_nos) is not None


def assert_excerpt(text: str, pages: list[str], page_nos: list | None = None) -> bool:
    return excerpt_in_pages(text, pages, page_nos)


def stamp_ungrounded(block, *, keep_text: str = ""):
    if isinstance(block, dict):
        out = dict(block)
        prior = _item_plain(out)
        out["ungrounded"] = True
        out["pages"] = []
        if "text" in out or keep_text:
            out["text"] = keep_text
        if prior and prior != keep_text:
            out["said"] = prior
        return out
    return {"text": keep_text, "pages": [], "ungrounded": True}


def _invented_number(quote: str, pages: list[str]) -> bool:
    blob = "\n".join(pages or [])
    for token in re.findall(r"\d+(?:\.\d+)?%?", quote or ""):
        if token not in blob:
            return True
    return False


def ground_block(block, pages: list[str]):
    """核对只核 quote。中文 text 对不上原文也不清空，只标出处没核到。"""
    if isinstance(block, list):
        return [ground_block(item, pages) for item in block]
    if isinstance(block, str):
        found = locate_quote(block, pages, None)
        if found:
            return {"text": block, "quote": block, "pages": [found]}
        return {"text": block, "pages": [], "unverified": True}
    if not isinstance(block, dict):
        return block
    text = (block.get("text") or "").strip() or _item_plain(block)
    quote = str(block.get("quote") or "").strip()
    pages_n = block.get("pages") or []
    out = dict(block)
    if text:
        out["text"] = text
    probe = quote or (text if text and locate_quote(text, pages, pages_n) else "")
    if quote and _invented_number(quote, pages):
        wiped = stamp_ungrounded(out, keep_text=text)
        wiped["quote"] = ""
        return wiped
    if probe:
        found = locate_quote(probe, pages, pages_n)
        if found:
            out["pages"] = [found]
            out.pop("unverified", None)
            out.pop("ungrounded", None)
            if quote:
                out["quote"] = quote
            return out
    if text or quote:
        out["unverified"] = True
        out.pop("ungrounded", None)
    return out


def _pages_blob(pages: list[str], page_nos: list | None = None) -> str:
    if not page_nos:
        return "\n".join(pages or [])
    parts: list[str] = []
    for n in page_nos:
        try:
            idx = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(pages or []):
            parts.append(pages[idx - 1] or "")
    return "\n".join(parts)


def _name_in_blob(name: str, blob: str) -> bool:
    token = (name or "").strip()
    if not token or not blob:
        return False
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])", blob, re.I) is not None


def ground_metric_item(item, pages: list[str]):
    """指标数字必须在原文中出现。中文说明对不上不清空，只标出处没核到。"""
    if not isinstance(item, dict):
        return ground_block(item, pages)
    out = ground_block(item, pages)
    if not isinstance(out, dict):
        return out
    val = out.get("value") if "value" in out else out.get("claimed")
    if val is None:
        val = out.get("score")
    val_s = "" if val is None else str(val).strip()
    blob = "\n".join(pages or [])
    if val_s and val_s not in blob:
        out["value"] = None
        out["unverified"] = True
    return out


def ground_card(card: dict, pages: list[str]) -> dict:
    out = dict(card or {})
    for key in ("problem", "methods", "datasets", "baselines", "limitations", "reusable"):
        if key in out:
            out[key] = ground_block(out[key], pages)
    if isinstance(out.get("claims"), list):
        out["claims"] = [ground_block(c, pages) for c in out["claims"]]
    raw_metrics = out.get("metrics")
    if isinstance(raw_metrics, dict):
        if "name" in raw_metrics or "value" in raw_metrics or "metric" in raw_metrics or "text" in raw_metrics:
            out["metrics"] = [ground_metric_item(raw_metrics, pages)]
        else:
            out["metrics"] = [ground_metric_item({"name": k, "value": v}, pages) for k, v in raw_metrics.items()]
    elif isinstance(raw_metrics, list):
        out["metrics"] = [ground_metric_item(item, pages) for item in raw_metrics]
    return out


def extract_json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.I)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def empty_method_card(*, source: str, warning: str) -> dict:
    return {
        "problem": {"text": "", "pages": [], "ungrounded": True},
        "claims": [],
        "methods": {"text": "", "pages": [], "ungrounded": True},
        "datasets": {"text": "", "pages": []},
        "metrics": [],
        "baselines": {"text": "", "pages": []},
        "limitations": {"text": "", "pages": []},
        "reusable": {"text": "", "pages": []},
        "related_in_lab": [],
        "source": source,
        "warning": warning,
    }


def page_batches(pages: list[str], max_chars: int = CARD_CHUNK_CHARS) -> list[list[tuple[int, str]]]:
    numbered = [(i, p) for i, p in enumerate(pages or [], start=1) if (p or "").strip()]
    if not numbered:
        return []
    batches: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    n = 0
    for item in numbered:
        extra = len(item[1])
        if cur and n + extra > max_chars:
            batches.append(cur)
            cur = []
            n = 0
        cur.append(item)
        n += extra
    if cur:
        batches.append(cur)
    return batches


_STRUCT_HINTS = (
    "method",
    "experiment",
    "result",
    "approach",
    "evaluation",
    "ablation",
    "方法",
    "实验",
    "结果",
    "评估",
    "消融",
)


def _structure_pages(structure: dict | None) -> list[int]:
    pages: list[int] = []
    if not isinstance(structure, dict):
        return pages
    for sec in structure.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or sec.get("heading") or sec.get("head") or "").lower()
        if not any(h in title for h in _STRUCT_HINTS):
            continue
        for key in ("page", "page_no", "start_page", "n"):
            try:
                n = int(sec.get(key))
            except (TypeError, ValueError):
                continue
            if n > 0:
                pages.append(n)
                break
    return pages


_BATCH_GROUPS = (
    ("abstract", "introduction", "摘要", "引言"),
    ("method", "approach", "方法"),
    ("experiment", "evaluation", "result", "实验", "结果", "评估"),
    ("conclusion", "discussion", "结论", "讨论"),
)


def _group_pages(structure: dict | None, hints: tuple[str, ...]) -> set[int]:
    pages: set[int] = set()
    if not isinstance(structure, dict):
        return pages
    for sec in structure.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or sec.get("heading") or sec.get("head") or "").lower()
        if not any(h in title for h in hints):
            continue
        for key in ("page", "page_no", "start_page", "n"):
            try:
                n = int(sec.get(key))
            except (TypeError, ValueError):
                continue
            if n > 0:
                pages.add(n)
                break
    return pages


def select_card_batches(batches: list, limit: int = MAX_CARD_BATCHES, structure: dict | None = None) -> list:
    """长文最多读四段：摘要或引言、方法、实验、结论。"""
    if not batches:
        return []
    cap = max(1, int(limit or MAX_CARD_BATCHES))
    if len(batches) <= cap:
        return list(batches)
    out: list = []

    def add(batch) -> None:
        if batch is not None and batch not in out and len(out) < cap:
            out.append(batch)

    add(batches[0])
    for hints in _BATCH_GROUPS:
        wanted = _group_pages(structure, hints)
        if not wanted:
            continue
        for batch in batches:
            if any(pno in wanted for pno, _ in batch):
                add(batch)
                break
    add(batches[-1])
    for batch in batches:
        add(batch)
    return out[:cap]


def fast_card_batch(pages: list[str], structure: dict | None = None, *, limit: int = FAST_CARD_CHARS) -> list[tuple[int, str]]:
    """摘要、引言和方法开头合成一段，给先出来的快卡。"""
    numbered = [(i, p) for i, p in enumerate(pages or [], start=1) if (p or "").strip()]
    if not numbered:
        return []
    method_pages = _group_pages(structure, ("method", "approach", "方法"))
    front_cap = max(1200, int(limit) * 5 // 8)
    picked: list[tuple[int, str]] = []
    used = 0

    def take(page_no: int, text: str, cap: int) -> bool:
        nonlocal used
        if any(no == page_no for no, _ in picked):
            return used >= cap
        room = cap - used
        if room < 200 and picked:
            return True
        chunk = text if len(text) <= room else text[:room]
        if not chunk.strip():
            return used >= cap
        picked.append((page_no, chunk))
        used += len(chunk)
        return used >= cap

    for page_no, text in numbered:
        if method_pages and page_no in method_pages:
            break
        if take(page_no, text, front_cap):
            break
    for page_no, text in numbered:
        if page_no not in method_pages:
            continue
        if take(page_no, text, limit):
            break
    if not picked:
        page_no, text = numbered[0]
        picked = [(page_no, text[:limit])]
    return picked


def _page_text(pages: list[str], page_no: int) -> str:
    idx = page_no - 1
    if idx < 0 or idx >= len(pages or []):
        return ""
    return pages[idx] or ""


def remaining_card_batches(selected: list, fast: list[tuple[int, str]], pages: list[str]) -> list:
    """快卡已经整页读过的段不再读第二遍。只截了一段的方法页仍整段补上。"""
    full_pages = {no for no, text in fast if text == _page_text(pages, no) and text}
    out = []
    for batch in selected or []:
        if batch and all(no in full_pages for no, _ in batch):
            continue
        out.append(batch)
    return out


def card_is_running(card: dict | None) -> bool:
    return isinstance(card, dict) and (card.get("status") or "") == "running"


WEAK_CARD_SOURCES = frozenset({"parse_failed", "pending", "abstract_only"})


def card_is_written(card: dict | None) -> bool:
    """全文这篇怎么做才算已写方法。摘要卡、失败壳、进行中不算。"""
    if not isinstance(card, dict) or card_is_running(card):
        return False
    return (card.get("source") or "") not in WEAK_CARD_SOURCES


def partial_running_card(
    merged: dict,
    *,
    source: str,
    warning: str | None,
    done: int,
    total: int,
    fast: bool = False,
    coverage: str = "",
) -> dict:
    """一段写完就先落库。status 仍是 running，最后一段才去掉。"""
    card = dict(merged or {})
    card["status"] = "running"
    card["source"] = card.get("source") or source
    card["chunks_done"] = int(done or 0)
    card["chunks_total"] = int(total or 0)
    note = f"已写出 {card['chunks_done']}/{card['chunks_total']} 段，还在写。"
    card["warning"] = f"{warning} {note}".strip() if warning else note
    if fast:
        card["fast"] = True
    if coverage:
        card["coverage"] = coverage
    return card


def _publish_running_partial(
    db: Session,
    item: LiteratureItem,
    merged: dict,
    *,
    source: str,
    warning: str | None,
    done: int,
    total: int,
    fast: bool = False,
    coverage: str = "",
) -> None:
    """长文已有 running 行时提交这一段，阅读器轮询能看见。短文没有 running 行则跳过。"""
    row = find_running_analysis(db, item.id)
    if row is None:
        return
    row.result_json = json.dumps(
        partial_running_card(
            merged,
            source=source,
            warning=warning,
            done=done,
            total=total,
            fast=fast,
            coverage=coverage,
        ),
        ensure_ascii=False,
    )
    row.model = "running"
    db.commit()


def running_method_card() -> dict:
    card = empty_method_card(source="pending", warning="正在提炼这篇怎么做…")
    card["status"] = "running"
    return card


def analysis_age_s(row: LiteratureAnalysis | None) -> float:
    if row is None or row.created_at is None:
        return 0.0
    from fengsuan.app.db.base import utcnow
    from fengsuan.app.services.activity import as_utc

    try:
        started = as_utc(row.created_at)
        if started is None:
            return 0.0
        return max(0.0, (utcnow() - started).total_seconds())
    except Exception:
        return 0.0


def expire_stale_running(row: LiteratureAnalysis) -> LiteratureAnalysis:
    card = empty_method_card(source="parse_failed", warning="提炼超时，请再点「提炼方法」。")
    row.result_json = json.dumps(card, ensure_ascii=False)
    row.model = "stale"
    return row


def iter_running_analyses(db: Session, *, cap: int = 40) -> list[LiteratureAnalysis]:
    """先排还在 pending 的 running，再扫最近卡里漏网的。较新的完成卡不得把旧 running 挤出窗口。"""
    cap = max(1, min(int(cap or 40), 80))
    pending = (
        db.query(LiteratureAnalysis)
        .filter(
            LiteratureAnalysis.analysis_type == "method_card",
            LiteratureAnalysis.model == "pending",
        )
        .order_by(LiteratureAnalysis.created_at.asc())
        .limit(cap)
        .all()
    )
    seen = {row.id for row in pending}
    extra: list[LiteratureAnalysis] = []
    if len(pending) < cap:
        extra = (
            db.query(LiteratureAnalysis)
            .filter(LiteratureAnalysis.analysis_type == "method_card")
            .order_by(LiteratureAnalysis.created_at.desc())
            .limit(cap)
            .all()
        )
    rows = list(pending)
    for row in extra:
        if row.id in seen:
            continue
        try:
            data = json.loads(row.result_json or "{}")
        except json.JSONDecodeError:
            continue
        if not card_is_running(data):
            continue
        rows.append(row)
        seen.add(row.id)
    return rows


def find_running_analysis(db: Session, item_id: str, *, expire: bool = False) -> LiteratureAnalysis | None:
    rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id == item_id, LiteratureAnalysis.analysis_type == "method_card")
        .order_by(LiteratureAnalysis.created_at.desc())
        .limit(8)
        .all()
    )
    for row in rows:
        try:
            data = json.loads(row.result_json or "{}")
        except json.JSONDecodeError:
            continue
        if not card_is_running(data):
            continue
        if analysis_age_s(row) > RUNNING_STALE_S:
            if expire:
                expire_stale_running(row)
                continue
        return row
    return None


def live_method_analysis(db: Session, item_id: str) -> LiteratureAnalysis | None:
    """侧栏优先进行中；没有 running 才用质量最好的卡。"""
    running = find_running_analysis(db, item_id)
    if running is not None:
        return running
    return best_method_analysis(db, item_id)


def paper_needs_defer(pages: list[str]) -> bool:
    return len(page_batches(pages)) > 2


def begin_or_run_analyze(
    db: Session, item: LiteratureItem, user_id: str, analysis_type: str = "method_card"
) -> tuple[LiteratureAnalysis, bool]:
    """短文当场写完；长文先落 running。返回 (行, 是否后台)。"""
    from fengsuan.app.services.pagecache import item_pages

    if analysis_type == "method_card":
        running = find_running_analysis(db, item.id, expire=True)
        if running is not None:
            return running, True
        pages, _src = item_pages(db, item)
        if pages and paper_needs_defer(pages):
            row = LiteratureAnalysis(
                item_id=item.id,
                analysis_type=analysis_type,
                result_json=json.dumps(running_method_card(), ensure_ascii=False),
                citations_json="[]",
                model="pending",
                created_by=user_id,
            )
            db.add(row)
            db.flush()
            return row, True
    return analyze_item(db, item, user_id, analysis_type), False


def _llm_fail_text(exc: BaseException) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:200]
    return "模型服务中断"


def join_page_batch(batch: list[tuple[int, str]]) -> str:
    return "\n".join(f"[p.{i}] {p}" for i, p in batch)


def merge_method_cards(parts: list[dict]) -> dict:
    usable = [p for p in parts if isinstance(p, dict)]
    if not usable:
        return {}
    if len(usable) == 1:
        return dict(usable[0])
    base = dict(usable[0])
    for key in ("claims", "metrics"):
        merged: list = []
        seen: set[str] = set()
        for part in usable:
            for item in part.get(key) or []:
                sig = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
                if sig in seen:
                    continue
                seen.add(sig)
                merged.append(item)
        if merged:
            base[key] = merged
    for key in ("problem", "methods", "datasets", "baselines", "limitations", "reusable"):
        picked = None
        for part in usable:
            block = part.get(key)
            if isinstance(block, dict) and _item_plain(block) and not block.get("ungrounded"):
                picked = block
                break
            if picked is None and isinstance(block, dict) and _item_plain(block):
                picked = block
        if picked is not None:
            base[key] = picked
    sources = [p.get("source") for p in usable if p.get("source")]
    if any(s == "fulltext" or s == "pdf" for s in sources):
        base["source"] = next(s for s in sources if s in {"fulltext", "pdf"})
    elif sources:
        base["source"] = sources[0]
    warn = next((p.get("warning") for p in usable if p.get("warning")), None)
    if warn:
        base["warning"] = warn
    base["chunks"] = len(usable)
    return base


def _related_in_lab(db: Session, item: LiteratureItem) -> list[dict]:
    from fengsuan.app.models.literature import LiteratureDirectionLink
    from fengsuan.app.services.literature import search_local

    out: list[dict] = []
    seen: set[str] = set()
    dir_ids = [
        r.direction_id
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    ]
    if dir_ids and item.lab_id:
        from fengsuan.app.services.directions import expand_direction_ids

        dir_ids = expand_direction_ids(db, item.lab_id, dir_ids) or dir_ids
    if dir_ids:
        links = (
            db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.direction_id.in_(dir_ids), LiteratureDirectionLink.item_id != item.id)
            .limit(20)
            .all()
        )
        for link in links:
            if link.item_id in seen:
                continue
            other = db.get(LiteratureItem, link.item_id)
            if other is None or other.deleted_at is not None or other.lab_id != item.lab_id:
                continue
            seen.add(other.id)
            out.append({"literature_item_id": other.id, "title": other.title or "", "reason": "同研究方向"})
            if len(out) >= 5:
                return out
    from fengsuan.app.models.manuscript import ManuscriptCitation

    cited_ms = [
        r.manuscript_id
        for r in db.query(ManuscriptCitation).filter(ManuscriptCitation.literature_item_id == item.id).all()
    ]
    if cited_ms:
        for c in (
            db.query(ManuscriptCitation)
            .filter(ManuscriptCitation.manuscript_id.in_(cited_ms), ManuscriptCitation.literature_item_id != item.id)
            .limit(20)
            .all()
        ):
            if c.literature_item_id in seen:
                continue
            other = db.get(LiteratureItem, c.literature_item_id)
            if other is None or other.deleted_at is not None or other.lab_id != item.lab_id:
                continue
            seen.add(other.id)
            out.append({"literature_item_id": other.id, "title": other.title or "", "reason": "同手稿引用"})
            if len(out) >= 5:
                return out
    q = (item.title or "")[:80]
    hits = search_local(db, item.lab_id, q) if q else []
    for hit in hits:
        hid = hit.get("id")
        if not hid or hid == item.id or hid in seen:
            continue
        seen.add(hid)
        out.append({"literature_item_id": hid, "title": hit.get("title") or "", "reason": "题录相近，供对照阅读"})
        if len(out) >= 5:
            break
    return out


def _fatal_card_error(exc: BaseException) -> bool:
    return isinstance(exc, ClassifiedHTTPException) and (
        exc.error_class in {"permission", "quota"} or exc.status_code == 429
    )


def _read_method_batch(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    system: str,
    batch: list[tuple[int, str]],
    source: str,
    warning: str | None,
) -> dict | None:
    raw = chat(
        db,
        user_id=user_id,
        lab_id=lab_id,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": join_page_batch(batch)},
        ],
        feature="analysis",
        json_mode=True,
    )
    parsed = extract_json_object(raw)
    if not parsed:
        return None
    return _normalize_card(parsed, source=source, warning=warning)


def _read_method_batch_own_session(**kwargs) -> dict:
    """并行段各自开会话，不和正在写快卡的会话抢同一条连接。"""
    from fengsuan.app.db.session import SessionLocal

    own = SessionLocal()
    try:
        card = _read_method_batch(own, **kwargs)
        own.commit()
        return {"card": card, "error": None}
    except Exception as exc:
        own.rollback()
        return {"card": None, "error": exc}
    finally:
        own.close()


def _read_batches_parallel(
    batches: list,
    *,
    user_id: str,
    lab_id: str,
    system: str,
    source: str,
    warning: str | None,
    on_part,
) -> BaseException | None:
    """最多三路并行。权限和配额错误立刻抛出，其余失败记下来继续。"""
    if not batches:
        return None
    from concurrent.futures import ThreadPoolExecutor, as_completed

    last_err: BaseException | None = None
    workers = min(3, len(batches))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _read_method_batch_own_session,
                user_id=user_id,
                lab_id=lab_id,
                system=system,
                batch=batch,
                source=source,
                warning=warning,
            )
            for batch in batches
        ]
        for fut in as_completed(futures):
            got = fut.result()
            err = got.get("error")
            if err is not None:
                if _fatal_card_error(err):
                    raise err
                last_err = err
                continue
            on_part(got.get("card"))
    return last_err


def analyze_item(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    analysis_type: str = "method_card",
    *,
    depth: str = "auto",
) -> LiteratureAnalysis:
    """depth="abstract"：几千篇排队时先只读摘要，全文等组里的都过一遍摘要再读。"""
    from fengsuan.app.services.pagecache import item_pages

    files = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).all()
    source = "pdf"
    warning = None
    abstract = (item.abstract or "").strip()
    if depth == "abstract" and len(abstract) >= 200:
        pages = []
    else:
        pages, _src = item_pages(db, item)
    if not pages and depth == "abstract" and len(abstract) >= 200:
        pages = [abstract]
        source = "abstract_only"
        warning = "先读了摘要。组里新进来的论文多，全文排在后面，等都读过一遍摘要再读。"
    elif not pages:
        if files and all((f.parse_status or "") == "unusable" for f in files):
            raise ClassifiedHTTPException(400, "无法提取正文，请换可复制 PDF。", "parse")
        text = (item.abstract or item.title or "").strip()
        if not text:
            raise ClassifiedHTTPException(
                400,
                "尚无可用全文：请上传 PDF，或补全摘要后再点「提炼方法」。",
                "parse",
            )
        pages = [text]
        source = "abstract_only"
        warning = "未解析到 PDF 文本，这篇怎么做只根据题录/摘要，页码仅供对照，不能当作原文页。"
    from fengsuan.app.services.agent.features import stamp_trace, try_seed_tools

    system = load_prompt("method_card")
    batches = page_batches(pages)
    if not batches:
        batches = [[(1, pages[0] if pages else "")]]
    from fengsuan.app.services.pagecache import item_structure

    structure = item_structure(db, item.id) if source != "abstract_only" else {}
    selected = select_card_batches(batches, structure=structure)
    short = source != "abstract_only" and len(batches) <= SHORT_PAPER_BATCHES
    fast_batch = [] if short else fast_card_batch(pages, structure)
    rest = list(selected) if short else remaining_card_batches(selected, fast_batch, pages)
    total_steps = (len(selected) if short else (1 if fast_batch else 0) + len(rest)) or 1
    parsed_parts: list[dict] = []
    last_err: BaseException | None = None
    pack = None
    seeded = False

    def _seed() -> None:
        nonlocal pack, seeded
        if seeded:
            return
        seeded = True
        pack = try_seed_tools(
            db,
            user_id=user_id,
            lab_id=item.lab_id,
            feature="analysis",
            scope="paper",
            item_id=item.id,
            seeds=[
                ("get_paper_page_text", {"page_no": 1}),
                ("get_paper_structure", {}),
                ("get_paper_biblio", {}),
                ("search_lab_literature", {"query": (item.title or "")[:80]}),
            ],
        )

    def _keep(part: dict | None, *, fast: bool = False, coverage_now: str = "") -> None:
        if not part:
            return
        parsed_parts.append(part)
        if analysis_type != "method_card" or len(parsed_parts) < 1:
            return
        if short and len(selected) <= 1:
            return
        merged = merge_method_cards(parsed_parts)
        _publish_running_partial(
            db,
            item,
            merged,
            source=source,
            warning=warning,
            done=len(parsed_parts),
            total=total_steps,
            fast=fast,
            coverage=coverage_now,
        )

    if fast_batch:
        try:
            first = _read_method_batch(
                db,
                user_id=user_id,
                lab_id=item.lab_id,
                system=system,
                batch=fast_batch,
                source=source,
                warning=warning,
            )
        except ClassifiedHTTPException as exc:
            if _fatal_card_error(exc):
                raise
            last_err = exc
            first = None
        except Exception as exc:
            last_err = exc
            first = None
        _keep(first, fast=True, coverage_now=FAST_COVERAGE)
        _seed()
    if rest:
        db.commit()
        parallel_err = _read_batches_parallel(
            rest,
            user_id=user_id,
            lab_id=item.lab_id,
            system=system,
            source=source,
            warning=warning,
            on_part=lambda part: _keep(part, fast=bool(fast_batch), coverage_now=FAST_COVERAGE if fast_batch else ""),
        )
        if parallel_err is not None:
            last_err = parallel_err
    _seed()
    read_all = bool(parsed_parts) and (
        (short and len(parsed_parts) >= len(batches))
        or (
            not short
            and len(selected) >= len(batches)
            and len(parsed_parts) >= len(rest) + (1 if fast_batch else 0)
        )
    )
    coverage = ""
    if source != "abstract_only" and read_all:
        coverage = FULL_COVERAGE
    elif fast_batch and parsed_parts and not read_all:
        coverage = (
            FAST_COVERAGE
            if len(parsed_parts) <= 1
            else f"这次读了 {min(len(parsed_parts), len(selected))} 段（摘要或引言、方法、实验、结论），不是整篇。"
        )
    elif len(batches) > len(selected) and parsed_parts:
        coverage = f"这次读了 {len(selected)} 段（摘要或引言、方法、实验、结论），不是整篇。"
    if not parsed_parts:
        fail_src = "parse_failed" if source != "abstract_only" else source
        fail_warn = "没提炼出这篇怎么做，未装入无法解析的原文。请再点「提炼方法」。"
        if last_err:
            fail_warn = f"{_llm_fail_text(last_err)}。没提炼出这篇怎么做，请再点「提炼方法」。"
        if warning:
            fail_warn = f"{warning} {fail_warn}"
        result = empty_method_card(source=fail_src, warning=fail_warn)
    else:
        result = merge_method_cards(parsed_parts)
        result = ground_card(result, pages)
        result["schema"] = CARD_SCHEMA
        if coverage:
            result["coverage"] = coverage
        if warning:
            result["warning"] = warning
    related = result.get("related_in_lab") if isinstance(result.get("related_in_lab"), list) else []
    cleaned: list[dict] = []
    seen: set[str] = set()
    for rel in related:
        if not isinstance(rel, dict):
            continue
        rid = rel.get("literature_item_id")
        if not rid or rid == item.id or rid in seen:
            continue
        other = db.get(LiteratureItem, rid)
        if other is None or other.deleted_at is not None or other.lab_id != item.lab_id:
            continue
        seen.add(rid)
        cleaned.append({"literature_item_id": rid, "title": rel.get("title") or other.title or "", "reason": rel.get("reason") or "分析器关联"})
    if len(cleaned) < 5:
        for extra in _related_in_lab(db, item):
            rid = extra.get("literature_item_id")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            cleaned.append(extra)
            if len(cleaned) >= 5:
                break
    result["related_in_lab"] = cleaned[:5]
    result = stamp_trace(result, pack)
    result.pop("status", None)
    result.pop("fast", None)
    existing = find_running_analysis(db, item.id) if analysis_type == "method_card" else None
    if existing is not None:
        existing.result_json = json.dumps(result, ensure_ascii=False)
        existing.citations_json = "[]"
        existing.model = "configured"
        existing.created_by = user_id
        db.flush()
        existing.agent_session_id = (pack or {}).get("session_id") or ""
        _stamp_level(db, item, analysis_type)
        return existing
    row = LiteratureAnalysis(
        item_id=item.id,
        analysis_type=analysis_type,
        result_json=json.dumps(result, ensure_ascii=False),
        citations_json="[]",
        model="configured",
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    row.agent_session_id = (pack or {}).get("session_id") or ""
    _stamp_level(db, item, analysis_type)
    return row


def _stamp_level(db: Session, item: LiteratureItem, analysis_type: str) -> None:
    if analysis_type != "method_card":
        return
    try:
        from fengsuan.app.services.reading_tier import stamp_read_level

        stamp_read_level(db, item)
    except Exception:
        pass


def count_ungrounded(card: dict | None) -> int:
    n = 0

    def walk(obj) -> None:
        nonlocal n
        if isinstance(obj, dict):
            if obj.get("ungrounded"):
                n += 1
            for val in obj.values():
                walk(val)
        elif isinstance(obj, list):
            for val in obj:
                walk(val)

    walk(card or {})
    return n


def parse_card(row: LiteratureAnalysis | None) -> dict:
    if row is None:
        return {}
    try:
        data = json.loads(row.result_json or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    if card_is_running(data):
        return data
    return readable_method_card(data)


def card_quality(card: dict | None) -> dict:
    """质量口径：非摘要卡、少无依据摘录优先。分数越高越好。"""
    card = card if isinstance(card, dict) else {}
    if card_is_running(card):
        return {
            "source": card.get("source") or "pending",
            "abstract_only": False,
            "ungrounded_count": 0,
            "weak": True,
            "running": True,
            "score": -100,
        }
    source = card.get("source") or ""
    abstract_only = source == "abstract_only"
    parse_failed = source == "parse_failed"
    ungrounded = count_ungrounded(card)
    score = 0
    if parse_failed:
        score -= 80
    elif not abstract_only and source:
        score += 100
    elif not abstract_only:
        score += 40
    score -= ungrounded * 10
    if card.get("schema") == CARD_SCHEMA:
        score += 20
    if card.get("warning"):
        score -= 5
    return {
        "source": source,
        "abstract_only": abstract_only,
        "ungrounded_count": ungrounded,
        "weak": abstract_only or ungrounded > 0,
        "score": score,
    }


def analysis_next_step(item, empty: dict | None = None, *, page_text_missing: bool = False) -> dict | None:
    """方法卡/辅导空态下一步，形状与写回一致。"""
    item_id = item.id if item is not None else ""
    href = f"/literature/{item_id}?panel=card"
    if page_text_missing:
        return {
            "key": "upload_pdf",
            "label": "上传可复制 PDF",
            "text": "这一页还没有可复制文字，不能按页提问。请先上传可复制 PDF。",
            "href": href,
        }
    empty = empty or {}
    kind = (empty.get("kind") or "").strip()
    if not kind:
        return None
    return {
        "key": kind,
        "label": empty.get("label") or "下一步",
        "text": empty.get("next_step") or empty.get("text") or "",
        "href": href,
    }


def analysis_followups(item, empty: dict | None = None) -> list[dict]:
    """从现有下一步抽出可点花片。卡写好后才给问/写/跑，不另编下一问。"""
    empty = empty or {}
    kind = (empty.get("kind") or "").strip()
    item_id = getattr(item, "id", "") or ""
    lab_id = getattr(item, "lab_id", "") or ""
    if kind == "running":
        return [{"label": "正在提炼", "href": f"/literature/{item_id}?panel=card"}] if item_id else []
    if kind:
        step = analysis_next_step(item, empty)
        if not step:
            return []
        return [{"label": (step.get("label") or "下一步")[:40], "href": step.get("href") or ""}]
    if not item_id:
        return []
    out = [{"label": "问这一页", "href": f"/literature/{item_id}?panel=tutor"}]
    if lab_id:
        out.append({"label": "去写作", "href": f"/labs/{lab_id}/writing?lit={item_id}"})
        out.append({"label": "开始复现", "href": f"/labs/{lab_id}/reproduces?literature_item_id={item_id}"})
    return out[:3]


def analysis_ready_notice_followups(item_id: str, lab_id: str) -> list[dict]:
    """分析写完的通知只给问这一页、去写作。不另编句子。"""
    from types import SimpleNamespace

    chips = analysis_followups(SimpleNamespace(id=item_id or "", lab_id=lab_id or ""), {})
    return [c for c in chips if c.get("label") in {"问这一页", "去写作"}]


def stamp_analysis_ai(payload: dict, item, *, empty: dict | None = None, result: dict | None = None) -> dict:
    """把这次怎么查的和下一步盖进提炼返回体。已有 followups 不改。"""
    result = result if isinstance(result, dict) else {}
    sid = (
        str(payload.get("session_id") or result.get("session_id") or result.get("trace_id") or "").strip()
    )
    if sid:
        payload["session_id"] = sid
        payload["trace_id"] = sid
    for key in ("steps", "degraded", "degraded_to", "available_tools"):
        if result.get(key) and not payload.get(key):
            payload[key] = result[key]
    if empty is not None and not payload.get("next_step"):
        step = analysis_next_step(item, empty)
        if step:
            payload["next_step"] = step
    if not payload.get("followups"):
        payload["followups"] = analysis_followups(item, empty)
    return payload


def card_empty_state(*, files=None, card=None) -> dict:
    """阅读器空态：弱卡/扫描件说清下一步。不新开分析产品。"""
    statuses: list[str] = []
    for f in files or []:
        st = f.get("parse_status") if isinstance(f, dict) else getattr(f, "parse_status", None)
        statuses.append(str(st or ""))
    unusable = any(s in {"unusable", "failed"} for s in statuses)
    ok_file = any(s and s not in {"unusable", "failed", "pending"} for s in statuses)
    has_file = bool(list(files or []))
    card = card if isinstance(card, dict) else {}
    if card_is_running(card):
        return {
            "kind": "running",
            "label": "正在提炼",
            "next_step": "正在写这篇怎么做，写完会显示在这里。",
        }
    quality = card_quality(card) if card else {"abstract_only": False, "ungrounded_count": 0}
    if (card.get("source") or "") == "parse_failed":
        return {
            "kind": "ungrounded",
            "label": "还没提炼方法",
            "next_step": "没提炼出这篇怎么做。请再点「提炼方法」，不要把无法解析的原文当主张。",
        }
    if unusable and not ok_file:
        return {
            "kind": "unusable",
            "label": "扫描件无法提取",
            "next_step": "请用工具条「上传 PDF」换成可复制文字版，再点「提炼方法」。扫描件不会假装写出这篇怎么做。",
        }
    if not card:
        if not has_file or not ok_file:
            return {
                "kind": "abstract_only",
                "label": "尚无可用全文",
                "next_step": "请先上传可复制文字版 PDF，再点「提炼方法」。不要把摘要当成全文依据。",
            }
        return {"kind": "", "label": "", "next_step": ""}
    if quality.get("abstract_only"):
        return {
            "kind": "abstract_only",
            "label": "只根据摘要写的",
            "next_step": "只根据题录或摘要写了速读。上传 PDF 后再生成一次。",
        }
    return {"kind": "", "label": "", "next_step": ""}


def item_has_fulltext(db: Session, item_id: str) -> bool:
    """页文本已抽出。不重解析 PDF。"""
    if not item_id:
        return False
    row = (
        db.query(LiteratureFile)
        .filter(
            LiteratureFile.item_id == item_id,
            LiteratureFile.parse_status == "ok",
            LiteratureFile.text_hash.isnot(None),
        )
        .first()
    )
    return row is not None


def newest_method_analysis(db: Session, item_id: str) -> LiteratureAnalysis | None:
    if not item_id:
        return None
    return (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id == item_id, LiteratureAnalysis.analysis_type == "method_card")
        .order_by(LiteratureAnalysis.created_at.desc())
        .first()
    )


def queue_schema_upgrade(db: Session, item: LiteratureItem, user_id: str) -> bool:
    """旧速读打开时只排队重写，不在这次请求里跑模型。"""
    if find_running_analysis(db, item.id) is not None:
        return False
    best = best_method_analysis(db, item.id)
    if best is None:
        return False
    card = parse_card(best)
    if card.get("schema") == CARD_SCHEMA or card_is_running(card):
        return False
    if (card.get("source") or "") in {"abstract_only", "parse_failed", "pending"}:
        return False
    if not item_has_fulltext(db, item.id):
        return False
    db.add(
        LiteratureAnalysis(
            item_id=item.id,
            analysis_type="method_card",
            result_json=json.dumps(running_method_card(), ensure_ascii=False),
            citations_json="[]",
            model="pending",
            created_by=user_id or item.added_by,
        )
    )
    db.flush()
    return True


def method_card_needs_preread(db: Session, item_id: str) -> bool:
    """无卡、过期失败壳、或只有摘要卡但全文已到，才预读。失败后冷却，避免每轮空转。"""
    if find_running_analysis(db, item_id) is not None:
        return False
    best = best_method_analysis(db, item_id)
    if best is None:
        return True
    card = parse_card(best)
    if card_is_running(card):
        return False
    source = card.get("source") or ""
    newest = newest_method_analysis(db, item_id)
    newest_card = parse_card(newest)
    newest_src = newest_card.get("source") or ""
    if newest_src == "parse_failed" and analysis_age_s(newest) < PREREAD_FAIL_RETRY_S:
        return False
    if source == "abstract_only" and item_has_fulltext(db, item_id):
        return True
    if card.get("schema") != CARD_SCHEMA and source not in {"parse_failed", "pending"} and item_has_fulltext(db, item_id):
        return True
    if source != "parse_failed":
        return False
    return analysis_age_s(best) >= PREREAD_FAIL_RETRY_S


def best_method_analysis(db: Session, item_id: str) -> LiteratureAnalysis | None:
    """仍保存每次分析；选用最好的卡，后一次弱卡不得盖掉好卡。"""
    if not item_id:
        return None
    rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id == item_id, LiteratureAnalysis.analysis_type == "method_card")
        .all()
    )
    if not rows:
        return None

    def sort_key(row: LiteratureAnalysis) -> tuple:
        q = card_quality(parse_card(row))
        created = row.created_at.isoformat() if row.created_at else ""
        return (q["score"], created)

    return max(rows, key=sort_key)


def load_best_method_card(db: Session, item_id: str) -> tuple[LiteratureAnalysis | None, dict]:
    row = best_method_analysis(db, item_id)
    return row, parse_card(row)


def _file_parse_status(files) -> str:
    statuses = []
    for f in files or []:
        st = f.get("parse_status") if isinstance(f, dict) else getattr(f, "parse_status", None)
        statuses.append(str(st or ""))
    if any(s and s not in {"unusable", "failed", "pending"} for s in statuses):
        return "ok"
    if any(s in {"unusable", "failed"} for s in statuses):
        return "unusable"
    if statuses:
        return "pending"
    return ""


def stamp_list_card_state(db: Session, payloads: list[dict], *, id_key: str = "id") -> None:
    """文献库列表盖全文空态。批量查询，禁止 N+1。"""
    ids = [p.get(id_key) for p in payloads if isinstance(p, dict) and p.get(id_key)]
    if not ids:
        return
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureFile

    files_by: dict[str, list] = defaultdict(list)
    for f in db.query(LiteratureFile).filter(LiteratureFile.item_id.in_(ids)).all():
        files_by[f.item_id].append(f)
    rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id.in_(ids), LiteratureAnalysis.analysis_type == "method_card")
        .all()
    )
    cards_by: dict[str, list] = defaultdict(list)
    for row in rows:
        cards_by[row.item_id].append(row)
    for p in payloads:
        if not isinstance(p, dict):
            continue
        iid = p.get(id_key)
        files = files_by.get(iid) or []
        analyses = cards_by.get(iid) or []
        running_rows = [row for row in analyses if card_is_running(parse_card(row))]
        is_run = bool(running_rows)
        best = None
        if running_rows:
            running_rows.sort(key=lambda row: row.created_at.isoformat() if row.created_at else "", reverse=True)
            best = running_rows[0]
        elif analyses:

            def sort_key(row: LiteratureAnalysis) -> tuple:
                q = card_quality(parse_card(row))
                created = row.created_at.isoformat() if row.created_at else ""
                return (q["score"], created)

            best = max(analyses, key=sort_key)
        card = parse_card(best)
        empty = card_empty_state(files=files, card=card)
        p["has_method_card"] = bool(best) and not is_run and card_is_written(card)
        p["analysis_status"] = "running" if is_run else ""
        p["has_pdf"] = bool(files)
        p["parse_status"] = _file_parse_status(files)
        p["card_empty"] = empty
        href = p.get("href") or (f"/literature/{iid}" if iid else "")
        if href and "panel=" not in href:
            href = f"{href}{'&' if '?' in href else '?'}panel=card"
        p["card_href"] = href


def _metric_scalar(val):
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return val
    s = str(val).strip()
    if not s:
        return None
    try:
        if any(ch in s for ch in ".eE"):
            return float(s)
        return int(s)
    except ValueError:
        return s


def claimed_metrics_from_card(card: dict | None) -> dict:
    """方法卡 metrics 列表 → {name: value}。不写入实测。"""
    if not isinstance(card, dict):
        return {}
    raw = card.get("metrics")
    items: list = []
    if isinstance(raw, dict):
        if "name" in raw or "value" in raw or "metric" in raw:
            items = [raw]
        else:
            items = [{"name": k, "value": v} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    else:
        return {}
    out: dict = {}
    for item in items:
        if isinstance(item, dict) and item.get("ungrounded"):
            continue
        if isinstance(item, str):
            s = item.strip()
            name, rest = "", ""
            if ":" in s:
                name, _, rest = s.partition(":")
            elif "=" in s:
                name, _, rest = s.partition("=")
            name, val = name.strip(), _metric_scalar(rest.strip())
            if name and val is not None:
                out[name] = val
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("metric") or item.get("key")
        val = item.get("value") if "value" in item else item.get("claimed")
        if val is None:
            val = item.get("score")
        name = str(name or "").strip()
        coerced = _metric_scalar(val)
        if name and coerced is not None:
            out[name] = coerced
    return out


def filter_gap_md(gap_md: str, item_ids: list[str]) -> str:
    """只保留点名所选文献 id 的句子。缺出处不得用对照表顶替。"""
    text = (gap_md or "").strip()
    ids = [iid for iid in item_ids if iid]
    if not text or not ids:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    kept = [part.strip() for part in parts if part.strip() and any(iid in part for iid in ids)]
    return "".join(p if p.endswith(("。", "！", "？", ".", "!", "?")) else p + "。" for p in kept).strip()


def grounded_card_texts(card) -> list[str]:
    texts: list[str] = []

    def walk(obj) -> None:
        if isinstance(obj, dict):
            if obj.get("ungrounded"):
                return
            text = obj.get("text")
            if text:
                texts.append(str(text))
            for key, val in obj.items():
                if key != "text":
                    walk(val)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, str) and obj.strip():
            texts.append(obj)

    walk(card if isinstance(card, dict) else {})
    return texts


_TUTOR_QUOTE = re.compile(r"「([^」]{1,200})」|“([^”]{1,200})”|\"([^\"]{1,200})\"")
_TUTOR_METRIC = re.compile(r"(?<![A-Za-z])(?:\d+\.\d+|\d+%|\d{2,})(?![A-Za-z])")
TUTOR_NEXT = "刚才有数字或摘录无法在本页或「这篇怎么做」里找到。请只根据本页原文追问，不要编造实验数字。"
KB_GROUND_NOTE = "（部分主张缺少知识库片段依据，未当作已核验综述。）"


def ground_against_corpus(text: str, corpus: str, *, note: str) -> dict:
    spans = [g for m in _TUTOR_QUOTE.finditer(text or "") for g in m.groups() if g]
    spans.extend(m.group(0) for m in _TUTOR_METRIC.finditer(text or ""))
    bad: list[str] = []
    for span in spans:
        if span and span not in (corpus or "") and span not in bad:
            bad.append(span)
    if not bad:
        return {"text": text or "", "ungrounded": False, "spans": []}
    return {"text": text or "", "ungrounded": True, "spans": bad[:8]}


def ground_tutor_answer(answer: str, *, page_text: str, card: dict | None, extra_corpus: str = "") -> dict:
    corpus = "\n".join([(page_text or "")] + grounded_card_texts(card) + [extra_corpus or ""])
    grounded = ground_against_corpus(answer, corpus, note="（部分摘录缺少原文依据，请只根据本页继续。）")
    return {
        "answer": grounded["text"],
        "ungrounded": grounded["ungrounded"],
        "next_question": TUTOR_NEXT if grounded["ungrounded"] else "",
    }


def best_cards_for_items(db: Session, item_ids: list[str]) -> dict[str, dict]:
    """批量取最好方法卡，避免图谱/大纲 N+1。"""
    ids = [iid for iid in item_ids if iid]
    if not ids:
        return {}
    rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id.in_(ids), LiteratureAnalysis.analysis_type == "method_card")
        .all()
    )
    grouped: dict[str, list[LiteratureAnalysis]] = {}
    for row in rows:
        grouped.setdefault(row.item_id, []).append(row)
    out: dict[str, dict] = {}
    for iid, group in grouped.items():
        best = max(
            group,
            key=lambda r: (card_quality(parse_card(r))["score"], r.created_at.isoformat() if r.created_at else ""),
        )
        out[iid] = parse_card(best)
    return out


def written_method_item_ids(db: Session, item_ids: list[str] | set[str] | None) -> set[str]:
    """最好一行是全文这篇怎么做的篇。摘要卡不进「已写方法」。"""
    ids = [iid for iid in (item_ids or []) if iid]
    if not ids:
        return set()
    cards = best_cards_for_items(db, ids)
    return {iid for iid, card in cards.items() if card_is_written(card)}


def compare_items(db: Session, items: list[LiteratureItem], user_id: str, lab_id: str) -> dict:
    from fengsuan.app.services.reproduce import compose_reproduce_gap_md, latest_reproduce_runs, reproduce_evidence

    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    seeds = [("get_method_card", {"item_id": it.id}) for it in items[:4]]
    if items:
        seeds.append(("get_paper_biblio", {"item_id": items[0].id}))
    seeds.append(("get_method_compare", {"item_ids": [it.id for it in items[:4]]}))
    seeds.append(
        ("search_lab_literature", {"query": " ".join(" ".join((it.title or "").split()[:4]) for it in items[:2])})
    )
    pack = try_seed_tools(
        db,
        user_id=user_id,
        lab_id=lab_id,
        feature="compare",
        scope="lab",
        seeds=seeds,
    )
    runs = latest_reproduce_runs(db, [it.id for it in items])
    cards_map = best_cards_for_items(db, [it.id for it in items])
    cards = []
    card_meta = []
    reproduce_by_id: dict = {}
    for item in items:
        parsed = cards_map.get(item.id) or {}
        last = bool(parsed)
        quality = card_quality(parsed) if last else None
        ev = reproduce_evidence(runs.get(item.id))
        reproduce_by_id[item.id] = ev
        cards.append(
            {
                "id": item.id,
                "title": item.title,
                "card": parsed if last else {"title": item.title, "abstract": item.abstract},
                "card_quality": quality,
                "reproduce": ev,
            }
        )
        card_meta.append({"id": item.id, "card_quality": quality, "is_best": last})
    raw = chat(
        db,
        user_id=user_id,
        lab_id=lab_id,
        messages=attach_evidence(
            [
                {
                    "role": "system",
                    "content": load_prompt("compare"),
                },
                {"role": "user", "content": json.dumps(cards, ensure_ascii=False)[:12000]},
            ],
            pack,
        ),
        feature="compare",
        json_mode=True,
    )
    parsed = extract_json_object(raw) or {}
    table = parsed.get("table_md") if isinstance(parsed.get("table_md"), str) else ""
    if table and "|" not in table:
        table = ""
    parsed["table_md"] = table
    parsed["json"] = parsed.get("json") if isinstance(parsed.get("json"), dict) else {}
    parsed["gap_md"] = filter_gap_md(parsed.get("gap_md") or "", [it.id for it in items])
    parsed["card_meta"] = card_meta
    parsed["reproduce_by_id"] = reproduce_by_id
    parsed["reproduce_gap_md"] = compose_reproduce_gap_md(
        db, [it.id for it in items], {it.id: it.title for it in items}
    )
    from fengsuan.app.services.writing import stamp_related_work_mentions

    gap = parsed.get("gap_md") or ""
    parsed["related_work_md"] = stamp_related_work_mentions(gap, items) if gap.strip() else ""
    parsed["mention_titles"] = [it.title for it in items if getattr(it, "title", None)]
    from fengsuan.app.services.agent.features import stamp_trace

    return stamp_trace(parsed, pack)


def citation_key_in_body(key: str, body: str, title: str = "") -> bool:
    """正文是否点名该引用：LaTeX/Pandoc 宏、词边界 key、或足够长的题名片段。"""
    body = body or ""
    key = (key or "").strip()
    if key:
        escaped = re.escape(key)
        for macro in _CITE_MACROS:
            if f"\\{macro}{{{key}}}" in body:
                return True
            if re.search(rf"\\{macro}\{{[^}}]*\b{escaped}\b[^}}]*\}}", body):
                return True
        if f"[@{key}]" in body:
            return True
        if re.search(rf"(?<![A-Za-z0-9._%+-])@{escaped}(?![A-Za-z0-9_])", body):
            return True
        if re.search(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", body):
            return True
    title = (title or "").strip()
    if len(title) >= 12 and title[:24] in body:
        return True
    return False


def claim_mentioned_in_body(text: str, body: str) -> bool:
    t = " ".join((text or "").split())
    if len(t) < 16:
        return False
    return t[:40].lower() in (body or "").lower()


def _int_pages(raw) -> list[int]:
    out: list[int] = []
    for p in raw or []:
        try:
            n = int(p)
        except (TypeError, ValueError):
            continue
        if n >= 1 and n not in out:
            out.append(n)
    return out


def pages_of_block(block) -> list[int]:
    if not block:
        return []
    if isinstance(block, list):
        pages: list[int] = []
        for item in block:
            for n in pages_of_block(item):
                if n not in pages:
                    pages.append(n)
        return pages
    if isinstance(block, dict):
        return _int_pages(block.get("pages"))
    return []


def first_card_page(card: dict | None) -> int | None:
    card = card or {}
    for key in ("claims", "methods", "problem", "datasets", "metrics", "baselines", "limitations", "reusable"):
        pages = pages_of_block(card.get(key))
        if pages:
            return pages[0]
    return None


def claim_rows(card: dict | None, body: str = "") -> list[dict]:
    """方法卡主张：保留 pages，供手稿侧栏跳阅读器 `?page=`。"""
    rows: list[dict] = []
    for c in (card or {}).get("claims") or []:
        if isinstance(c, dict):
            t = (c.get("text") or c.get("claim") or "").strip()
            pages = pages_of_block(c)
            ungrounded = bool(c.get("ungrounded"))
        else:
            t = str(c).strip() if c is not None else ""
            pages = []
            ungrounded = False
        if not t:
            continue
        rows.append(
            {
                "text": t,
                "mentioned_in_body": claim_mentioned_in_body(t, body),
                "pages": pages,
                "page_no": pages[0] if pages else None,
                "ungrounded": ungrounded,
            }
        )
    return rows


def claim_texts(card: dict | None) -> list[str]:
    return [r["text"] for r in claim_rows(card, "")]


def item_page_excerpt(db: Session, item: LiteratureItem, page_no: int | None = None) -> dict:
    """Working memory for this page. Empty when there is no copyable PDF text."""
    from fengsuan.app.services.pagecache import item_pages

    pages, _source = item_pages(db, item)
    has_fulltext = bool(pages and any((p or "").strip() for p in pages))
    page_text = ""
    if has_fulltext:
        if page_no and 1 <= page_no <= len(pages):
            page_text = (pages[page_no - 1] or "")[:4000]
        else:
            page_text = (pages[0] or "")[:4000]
    return {
        "pages": pages,
        "page_text": page_text,
        "has_fulltext": has_fulltext,
        "page_count": len(pages) if pages else 0,
    }


def session_tutor_history(
    db: Session,
    item_id: str,
    user_id: str,
    session_id: str | None,
    *,
    limit: int = 12,
) -> tuple[str | None, list[dict]]:
    """Only the current session, newest-trimmed. Never mix other sessions."""
    prior = (
        db.query(LiteratureAnalysis)
        .filter(
            LiteratureAnalysis.item_id == item_id,
            LiteratureAnalysis.analysis_type == "tutor",
            LiteratureAnalysis.created_by == user_id,
        )
        .order_by(LiteratureAnalysis.created_at.asc())
        .limit(80)
        .all()
    )
    sid = (session_id or "").strip() or None
    parsed_rows: list[tuple[LiteratureAnalysis, dict]] = []
    for row in prior:
        try:
            payload = json.loads(row.result_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        parsed_rows.append((row, payload))
    if not sid and parsed_rows:
        last_p = parsed_rows[-1][1]
        sid = (last_p.get("session_id") or parsed_rows[-1][0].id) or None
    history: list[dict] = []
    for row, payload in parsed_rows:
        row_sid = payload.get("session_id") or row.id
        if sid and row_sid != sid:
            continue
        answer, _ = strip_model_reasoning(payload.get("answer") or "")
        history.append(
            {
                "question": payload.get("question") or "",
                "answer": answer[:800],
                "page_no": payload.get("page_no"),
            }
        )
    return sid, history[-max(1, limit) :]


def _tutor_messages(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    question: str,
    page_no: int | None = None,
    session_id: str | None = None,
    excerpt: str | None = None,
    context: str | None = None,
) -> tuple[list[dict], dict]:
    from fengsuan.app.models.literature import LiteratureNote

    selection = (excerpt or "").strip()[:4000]
    selection_context = (context or "").strip()[:2000]
    page_pack = item_page_excerpt(db, item, page_no)
    has_fulltext = bool(page_pack["has_fulltext"])
    page_text = page_pack["page_text"] or ((item.abstract or item.title or "")[:4000] if not has_fulltext else "")
    _last, card = load_best_method_card(db, item.id)
    notes = (
        db.query(LiteratureNote)
        .filter(LiteratureNote.item_id == item.id, LiteratureNote.deleted_at.is_(None))
        .order_by(LiteratureNote.created_at.desc())
        .limit(6)
        .all()
    )
    note_md = "\n".join((n.content_md or "")[:400] for n in notes)
    sid, history = session_tutor_history(db, item.id, user_id, session_id, limit=12)
    from fengsuan.app.services.reproduce import evidence_brief, latest_reproduce_evidence

    ev = latest_reproduce_evidence(db, item.id)
    from fengsuan.app.models.literature import LiteratureDirectionLink
    from fengsuan.app.services.directions import direction_scope_keywords

    hung = [
        row.direction_id
        for row in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    ]
    direction_kws, includes_children = direction_scope_keywords(db, item.lab_id, direction_ids=hung)
    ev_slim = None
    if ev:
        ev_slim = {
            "id": ev.get("id"),
            "href": ev.get("href"),
            "measured": ev.get("measured"),
            "honest": ev.get("honest"),
            "actual_metrics": ev.get("actual_metrics") if ev.get("measured") else {},
            "diagnosis_kind": ev.get("diagnosis_kind"),
            "brief": evidence_brief(ev),
        }
    messages = [
        {
            "role": "system",
            "content": load_prompt("tutor"),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "title": item.title,
                    "page_no": page_no,
                    "page_text": page_text,
                    "method_card": card,
                    "notes": note_md,
                    "reproduce": ev_slim,
                    "session_id": sid,
                    "history": history,
                    "question": question,
                    "selection": selection,
                    "selection_context": selection_context,
                    "direction_keywords": direction_kws,
                    "includes_children": includes_children,
                },
                ensure_ascii=False,
            )[:12000],
        },
    ]
    ctx = {
        "has_fulltext": has_fulltext,
        "page_text": page_text,
        "card": card,
        "sid": sid,
        "history": history,
        "ev_slim": ev_slim,
        "question": question,
        "page_no": page_no,
        "selection": selection,
    }
    return messages, ctx


def _tutor_row(db: Session, item: LiteratureItem, user_id: str) -> LiteratureAnalysis:
    row = LiteratureAnalysis(
        item_id=item.id,
        analysis_type="tutor",
        result_json="{}",
        citations_json="[]",
        model="configured",
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    return row


def _finalize_tutor(db: Session, item: LiteratureItem, user_id: str, answer: str, ctx: dict) -> dict:
    row = _tutor_row(db, item, user_id)
    sid = ctx.get("sid") or row.id
    ev_slim = ctx.get("ev_slim")
    extra_corpus = (
        json.dumps((ev_slim or {}).get("actual_metrics") or {}, ensure_ascii=False) if ev_slim and ev_slim.get("measured") else ""
    )
    selection = (ctx.get("selection") or "").strip()
    if selection:
        extra_corpus = f"{extra_corpus}\n{selection}".strip()
    cleaned, stripped = strip_model_reasoning(answer)
    if "讲这张图" in (ctx.get("question") or "") and "没看图像本身" not in cleaned:
        cleaned = (cleaned.rstrip() + "\n按图注和正文讲的，没看图像本身。").strip()
    grounded = ground_tutor_answer(
        cleaned,
        page_text=ctx.get("page_text") or "",
        card=ctx.get("card"),
        extra_corpus=extra_corpus,
    )
    has_fulltext = bool(ctx.get("has_fulltext"))
    payload = {
        "question": ctx.get("question") or "",
        "answer": grounded["answer"],
        "page_no": ctx.get("page_no"),
        "session_id": sid,
        "ungrounded": grounded["ungrounded"],
        "next_question": grounded["next_question"],
        "reproduce": ev_slim,
        "page_text_missing": not has_fulltext,
        "history_turns": len(ctx.get("history") or []),
        "reasoning_stripped": stripped,
        "excerpt": selection,
        "excerpt_preview": clip_preview(selection),
        "followup_prompt": TUTOR_FOLLOWUP_SEL if selection else "",
    }
    if not has_fulltext:
        payload["next_step"] = analysis_next_step(item, page_text_missing=True)
        payload["panel_title"] = "这一页还没有可复制文字"
    row.result_json = json.dumps(payload, ensure_ascii=False)
    out = {
        "id": row.id,
        "question": payload["question"],
        "answer": grounded["answer"],
        "page_no": payload["page_no"],
        "session_id": sid,
        "ungrounded": grounded["ungrounded"],
        "next_question": grounded["next_question"],
        "reproduce": ev_slim,
        "page_text_missing": not has_fulltext,
        "history_turns": payload["history_turns"],
        "reasoning_stripped": True,
        "excerpt": selection,
        "excerpt_preview": clip_preview(selection),
        "followup_prompt": payload["followup_prompt"],
    }
    if payload.get("next_step"):
        out["next_step"] = payload["next_step"]
        out["panel_title"] = payload["panel_title"]
    return out


def iter_tutor_item(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    question: str,
    page_no: int | None = None,
    session_id: str | None = None,
    excerpt: str | None = None,
    context: str | None = None,
):
    messages, ctx = _tutor_messages(
        db, item, user_id, question, page_no, session_id, excerpt=excerpt, context=context
    )
    pieces: list[str] = []
    for delta in iter_chat_deltas(
        db,
        user_id=user_id,
        lab_id=item.lab_id,
        messages=messages,
        feature="tutor",
    ):
        pieces.append(delta)
        yield {"delta": delta}
    out = _finalize_tutor(db, item, user_id, "".join(pieces), ctx)
    yield {"done": True, **out}


TUTOR_AGENT_SYSTEM_EXTRA = (
    "这是阅读器里的精读辅导。用中文，先一句结论，再分点。不要写 **加粗** 星号。"
    "用户正看着某一页。问的是这一页时先贴着这一页；问方法、实验、消融或某一节在哪时，先用 search_in_paper 在整篇里找，不要猜页码。"
    "问谁在读这篇用 get_paper_readers，只看当前这篇。不要评价对方读得快慢。"
)


def _agent_tutor_context(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    question: str,
    page_no: int | None,
    session_id: str | None,
    excerpt: str | None,
) -> dict:
    """Agent 路径的上下文：只要历史与是否有可复制文字，正文交给工具去取。"""
    from fengsuan.app.services.pagecache import item_single_page
    from fengsuan.app.services.reproduce import evidence_brief, latest_reproduce_evidence

    pack = item_single_page(db, item, page_no)
    sid, history = session_tutor_history(db, item.id, user_id, session_id, limit=12)
    selection = (excerpt or "").strip()[:4000]
    ev = latest_reproduce_evidence(db, item.id)
    ev_slim = None
    if ev:
        ev_slim = {
            "id": ev.get("id"),
            "href": ev.get("href"),
            "measured": ev.get("measured"),
            "honest": ev.get("honest"),
            "actual_metrics": ev.get("actual_metrics") if ev.get("measured") else {},
            "diagnosis_kind": ev.get("diagnosis_kind"),
            "brief": evidence_brief(ev),
        }
    return {
        "has_fulltext": bool((pack.get("page_text") or "").strip()) or bool(pack.get("page_count")),
        "page_text": pack.get("page_text") or "",
        "sid": sid,
        "history": history,
        "question": question,
        "page_no": page_no,
        "selection": selection,
        "card": None,
        "ev_slim": ev_slim,
    }


def _finalize_agent_tutor(db: Session, item: LiteratureItem, user_id: str, ctx: dict, agent_out: dict) -> dict:
    """Agent 结果落到与单轮路径完全相同的 payload 形状，前端与历史接口不用改。

    差别只在多出 `citations` / `steps` / `trace_session_id`，
    以及 `ungrounded` 由 `source_id` 追溯得出而非事后正则。
    """
    row = _tutor_row(db, item, user_id)
    sid = ctx.get("sid") or row.id
    answer, stripped = strip_model_reasoning(agent_out.get("answer") or "")
    # 两道接地：`source_id` 追溯管「有没有出处」，摘录数字比对管「出处里真有没有这句」。
    uncited = agent_out.get("ungrounded") or []
    corpus = "\n".join((s.get("excerpt") or "") for s in agent_out.get("sources") or [])
    if ctx.get("page_text"):
        corpus += "\n" + ctx["page_text"]
    if ctx.get("selection"):
        corpus += "\n" + ctx["selection"]
    ev_slim = ctx.get("ev_slim")
    if ev_slim and ev_slim.get("measured"):
        corpus += "\n" + json.dumps(ev_slim.get("actual_metrics") or {}, ensure_ascii=False)
    spans = ground_against_corpus(answer, corpus, note="（部分摘录缺少原文依据，请只根据本页继续。）")
    ungrounded_spans = list(uncited) + list(spans["spans"])
    ungrounded = bool(uncited) or bool(spans["ungrounded"])
    if spans["ungrounded"]:
        answer = spans["text"]
    selection = (ctx.get("selection") or "").strip()
    has_fulltext = bool(ctx.get("has_fulltext"))
    payload = {
        "question": ctx.get("question") or "",
        "answer": answer,
        "page_no": ctx.get("page_no"),
        "session_id": sid,
        "ungrounded": ungrounded,
        "ungrounded_spans": ungrounded_spans[:6],
        "next_question": TUTOR_NEXT if ungrounded else "",
        "reproduce": ctx.get("ev_slim"),
        "page_text_missing": not has_fulltext,
        "history_turns": len(ctx.get("history") or []),
        "reasoning_stripped": stripped,
        "excerpt": selection,
        "excerpt_preview": clip_preview(selection),
        "followup_prompt": TUTOR_FOLLOWUP_SEL if selection else "",
        "agent": True,
        "citations": agent_out.get("citations") or [],
        "sources": [
            {
                "id": s.get("id"),
                "source_id": s.get("id"),
                "label": s.get("label"),
                "kind": s.get("kind"),
                "kind_label": s.get("kind_label") or "",
                "title": s.get("title"),
                "page": s.get("page"),
                "page_no": s.get("page"),
                "href": s.get("href"),
                "item_id": s.get("item_id") or "",
                "literature_item_id": s.get("item_id") or "",
            }
            for s in (agent_out.get("sources") or [])
        ],
        "steps": [
            {
                "step": s.get("step"),
                "tool": s.get("tool"),
                "label": s.get("label") or s.get("tool"),
                "summary": s.get("result_summary") or "",
                "result_summary": s.get("result_summary") or "",
                "error": s.get("error") or "",
                "empty": bool(s.get("empty")),
                "empty_next": s.get("empty_next") or None,
            }
            for s in (agent_out.get("steps") or [])
        ],
        "grounded_ratio": agent_out.get("grounded_ratio"),
        "trace_id": agent_out.get("session_id") or "",
        "degraded": agent_out.get("degraded") or "",
        "degraded_to": agent_out.get("degraded_to") or ("chat" if agent_out.get("degraded") else ""),
        "pending_confirm": agent_out.get("pending_confirm") or [],
        "empty_next": agent_out.get("empty_next") or None,
        "followups": agent_out.get("followups") or [],
        "available_tools": list(agent_out.get("available_tools") or []),
    }
    if not has_fulltext:
        payload["next_step"] = analysis_next_step(item, page_text_missing=True)
        payload["panel_title"] = "这一页还没有可复制文字"
    row.result_json = json.dumps(payload, ensure_ascii=False)
    row.citations_json = json.dumps(payload["citations"], ensure_ascii=False)
    out = {k: v for k, v in payload.items() if k != "ungrounded_spans"}
    out["id"] = row.id
    out["reasoning_stripped"] = True
    return out


def iter_agent_tutor_item(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    question: str,
    page_no: int | None = None,
    session_id: str | None = None,
    excerpt: str | None = None,
    context: str | None = None,
    scope: str = "paper",
    confirm: bool = False,
    confirm_tools: list | None = None,
):
    """走 Agent 运行时的精读辅导。工具挑证据，引用可点，每步可追溯。"""
    from fengsuan.app.services.agent import run_agent

    ctx = _agent_tutor_context(db, item, user_id, question, page_no, session_id, excerpt)
    if not (ctx.get("sid") or "").strip():
        ctx["sid"] = uuid.uuid4().hex
    selection = ctx["selection"]
    ask = question
    page_text = (ctx.get("page_text") or "").strip()
    extra = TUTOR_AGENT_SYSTEM_EXTRA
    if selection:
        ask = f"{question}\n\n用户选中的原文（第 {page_no or 1} 页）：{selection[:1200]}"
    elif scope == "paper" and page_text:
        extra += (
            f"\n当前第 {page_no or 1} 页正文如下。先根据这一页回答，不要编造页上没有的数字：\n"
            f"{page_text[:2200]}"
        )
    if (context or "").strip():
        ask += f"\n\n选区上下文：{(context or '').strip()[:800]}"
    seed: list[tuple[str, dict]] = []
    if scope == "paper":
        seed.append(("get_paper_page_text", {"page_no": page_no or 1}))
        seed.append(("get_paper_biblio", {}))
        page_ask = any(tok in (question or "") for tok in ("这一页", "本页", "这页", "当前页"))
        if not page_ask:
            seed.append(("search_in_paper", {"query": (question or "")[:80]}))
    agent_out: dict = {}
    for ev in run_agent(
        db,
        user_id=user_id,
        lab_id=item.lab_id,
        scope=scope,
        question=ask,
        feature="tutor",
        item_id=item.id,
        page_no=page_no,
        history=ctx["history"],
        session_id=ctx.get("sid") or "",
        system_extra=extra,
        seed_tools=seed,
        confirm=confirm,
        excerpt=selection,
        confirm_tools=confirm_tools,
    ):
        if ev.get("done"):
            agent_out = ev
            break
        yield ev
    out = _finalize_agent_tutor(db, item, user_id, ctx, agent_out)
    yield {"done": True, **out}


def tutor_item(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    question: str,
    page_no: int | None = None,
    session_id: str | None = None,
    excerpt: str | None = None,
    context: str | None = None,
    scope: str = "",
    confirm: bool = False,
    confirm_tools: list | None = None,
) -> dict:
    out: dict = {}
    for ev in iter_any_tutor_item(
        db,
        item,
        user_id,
        question,
        page_no,
        session_id,
        excerpt=excerpt,
        context=context,
        scope=scope,
        confirm=confirm,
        confirm_tools=confirm_tools,
    ):
        if ev.get("done"):
            out = {k: v for k, v in ev.items() if k != "done"}
    return out


def tutor_use_agent(scope: str = "") -> bool:
    """默认走 Agent 运行时；`FENGSUN_TUTOR_AGENT=0` 可退回单轮兜底。"""
    import os

    if (scope or "") == "lab":
        return True
    return (os.environ.get("FENGSUN_TUTOR_AGENT", "1") or "1").lower() not in {"0", "false", "no"}


def iter_any_tutor_item(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    question: str,
    page_no: int | None = None,
    session_id: str | None = None,
    excerpt: str | None = None,
    context: str | None = None,
    scope: str = "",
    confirm: bool = False,
    confirm_tools: list | None = None,
):
    """单一入口：跨篇一律走运行时，单篇按开关决定，兜底仍是原单轮路径。"""
    wanted = (scope or "paper").strip() or "paper"
    if tutor_use_agent(wanted):
        yield from iter_agent_tutor_item(
            db,
            item,
            user_id,
            question,
            page_no,
            session_id,
            excerpt=excerpt,
            context=context,
            scope=wanted,
            confirm=confirm,
            confirm_tools=confirm_tools,
        )
        return
    yield from iter_tutor_item(
        db, item, user_id, question, page_no, session_id, excerpt=excerpt, context=context
    )
