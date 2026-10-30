from __future__ import annotations

import re
from statistics import median
from typing import Any

_JUNK_RE = re.compile(
    r"copyright|ieee|all rights reserved|authorized licensed|downloaded from|"
    r"personal use(?: only)?|doi:\s*10\.|https?://doi\.org|xxx\.xxx|"
    r"not for redistribution|©|\(c\)\s*\d{4}",
    re.I,
)
_INSTRUCTION_MARKERS = (
    "carefully translate",
    "carefully polish",
    "do not translate",
    "do not add explanations",
    "key points",
    "follow the glossary",
    "follow glossary",
    "translation guidelines",
    "i will now translate",
    "i will translate",
    "let me translate",
    "here is how to translate",
    "the translation should",
    "maintain technical accuracy",
    "output only the translation",
    "only output the translation",
)


def _item_xy(it: dict[str, Any]) -> dict[str, Any] | None:
    s = str(it.get("str") or "")
    if not s.strip():
        return None
    t = it.get("transform") or []
    x = float(t[4] if len(t) > 4 else it.get("x") or 0)
    y = float(t[5] if len(t) > 5 else it.get("y") or 0)
    h = float(it.get("height") or (abs(t[3]) if len(t) > 3 else 8) or 8)
    w = float(it.get("width") or 0)
    return {"str": s, "x": x, "y": y, "h": h, "w": w}


def _is_junk(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return True
    if re.fullmatch(r"\d{1,4}", t):
        return True
    return bool(_JUNK_RE.search(t))


def _join_tokens(parts: list[str]) -> str:
    out = ""
    for raw in parts:
        p = re.sub(r"\s+", " ", raw)
        if not p:
            continue
        if not out:
            out = p
            continue
        if re.search(r"[A-Za-z]-$", out) and re.match(r"^[a-z]", p.lstrip()):
            out = re.sub(r"-+\s*$", "", out) + p.lstrip()
        elif re.search(r"[-/]$", out) or re.match(r"^[,.;:)\]]", p):
            out += p.lstrip()
        elif out.endswith(" ") or p.startswith(" "):
            out += p
        else:
            out += " " + p
    return re.sub(r"\s+", " ", out).strip()


def _cluster_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return []
    heights = [i["h"] for i in items if i["h"] > 0]
    tol = max(3.0, (median(heights) if heights else 8) * 0.45)
    ordered = sorted(items, key=lambda i: (-i["y"], i["x"]))
    lines: list[dict[str, Any]] = []
    for it in ordered:
        last = lines[-1] if lines else None
        if last and abs(last["y"] - it["y"]) <= tol:
            last["items"].append(it)
            n = len(last["items"])
            last["y"] = (last["y"] * (n - 1) + it["y"]) / n
        else:
            lines.append({"y": it["y"], "items": [it]})
    out: list[str] = []
    for ln in lines:
        ln["items"].sort(key=lambda i: i["x"])
        text = _join_tokens([i["str"] for i in ln["items"]])
        if text:
            out.append(text)
    return out


def _dehyphenate_lines(lines: list[str]) -> str:
    merged: list[str] = []
    for line in lines:
        t = line.strip()
        if not t:
            continue
        if merged and re.search(r"[A-Za-z]-$", merged[-1]) and re.match(r"^[a-z]", t):
            merged[-1] = re.sub(r"-$", "", merged[-1]) + t
        else:
            merged.append(t)
    return "\n".join(merged)


def _split_columns(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if len(items) < 12:
        return [items]
    xs = sorted(i["x"] for i in items)
    lo, hi = xs[0], xs[-1]
    if hi - lo < 200:
        return [items]
    best_gap = 0.0
    split_x = None
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        mid = (a + b) / 2.0
        rel = (mid - lo) / (hi - lo)
        if 0.28 < rel < 0.72 and gap > best_gap:
            best_gap = gap
            split_x = mid
    if split_x is None or best_gap < 18:
        return [items]
    left = [i for i in items if i["x"] < split_x]
    right = [i for i in items if i["x"] >= split_x]
    if len(left) < 4 or len(right) < 4:
        return [items]
    return [left, right]


def join_pdf_text_items(items: list[dict[str, Any]] | None) -> str:
    """按 y 分行、按 x 排字；去断词、去页眉页脚；双栏先左后右。"""
    raw: list[dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            row = _item_xy({"str": str(it)})
        else:
            row = _item_xy(it)
        if row:
            raw.append(row)
    if not raw:
        return ""
    ys = [i["y"] for i in raw]
    y_min, y_max = min(ys), max(ys)
    span = max(y_max - y_min, 1.0)
    body: list[dict[str, Any]] = []
    for i in raw:
        rel = (i["y"] - y_min) / span
        edge = rel < 0.045 or rel > 0.955
        if _is_junk(i["str"]) and (
            edge or re.search(r"copyright|authorized licensed|all rights reserved", i["str"], re.I)
        ):
            continue
        body.append(i)
    cols = _split_columns(body or raw)
    parts = [_dehyphenate_lines(_cluster_lines(col)) for col in cols]
    return "\n\n".join(p for p in parts if p).strip()


def surrounding_context(page_text: str, selection: str, radius: int = 280) -> str:
    """选区前后文，供消歧。找不到选区则空。"""
    page = page_text or ""
    sel = (selection or "").strip()
    if not page or not sel:
        return ""
    idx = page.find(sel)
    hay = page
    needle = sel
    if idx < 0:
        hay = re.sub(r"\s+", " ", page)
        needle = re.sub(r"\s+", " ", sel)
        idx = hay.find(needle)
        if idx < 0:
            return ""
    start = max(0, idx - radius)
    end = min(len(hay), idx + len(needle) + radius)
    return hay[start:end].strip()


def split_readable_chunks(text: str, max_chars: int = 2200) -> list[str]:
    """按段打包，避免整页一锅译。短文仍是一段。"""
    blob = (text or "").strip()
    if not blob:
        return []
    if len(blob) <= max_chars:
        return [blob]
    paras = re.split(r"\n\s*\n", blob)
    chunks: list[str] = []
    buf = ""
    for para in paras:
        p = para.strip()
        if not p:
            continue
        if len(p) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            parts = re.split(r"(?<=[.!?。！？])\s+", p)
            cur = ""
            for sent in parts:
                if cur and len(cur) + len(sent) + 1 > max_chars:
                    chunks.append(cur)
                    cur = sent
                else:
                    cur = f"{cur} {sent}".strip() if cur else sent
            if cur:
                chunks.append(cur)
            continue
        if buf and len(buf) + len(p) + 2 > max_chars:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}".strip() if buf else p
    if buf:
        chunks.append(buf)
    return chunks or [blob[:max_chars]]


def glossary_hits_on_page(text: str, terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blob = text or ""
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms or []:
        src = (term.get("src") or "").strip()
        if not src or src.lower() in seen:
            continue
        if src in blob or src.lower() in blob.lower():
            seen.add(src.lower())
            hits.append(
                {
                    "src": src,
                    "tgt": term.get("tgt") or "",
                    "do_not_translate": bool(term.get("do_not_translate")),
                }
            )
    return hits[:24]


TUTOR_FOLLOWUP_SEL = "这几句再讲细一点？"


def clip_preview(text: str, n: int = 72) -> str:
    """气泡/弹层短摘，不要整段糊上去。"""
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return ""
    if len(blob) <= n:
        return blob
    return blob[: max(1, n - 1)] + "…"


def wrap_language_user(protected: str, context: str = "", *, mode: str = "translate") -> str:
    ctx = (context or "").strip()
    if not ctx:
        return protected
    head = "【前后文，不要改这段，只用来消歧义】" if mode == "polish" else "【前后文，不要译这段，只用来消歧义】"
    return f"{head}\n{ctx[:2000]}\n【下面才是要处理的正文】\n{protected}"


_CAMEL_WORD = re.compile(r"([a-z]{2,})([A-Z][a-z]{2,})")
_PREAMBLE_LINE = re.compile(
    r"^\s*(?:\d+[\.\)]\s+|"
    r"(?:let me|i will|i'll|here is|the text includes|the translation|"
    r"only translate|only output|do not |follow the|key points|"
    r"carefully |maintaining |working on|output only))",
    re.I,
)
_LABEL_LINE = re.compile(
    r"^(?:title|authors?|abstract(?:\s+content)?|index\s+terms?|keywords?|affiliations?)\s*[:：]",
    re.I,
)


def cjk_count(text: str) -> int:
    return sum(1 for ch in text or "" if "\u4e00" <= ch <= "\u9fff")


def restore_latin_spacing(text: str) -> str:
    """PDF 字距丢失时 EfficientSmall → Efficient Small，避免模型跟着粘词。"""
    out = text or ""
    out = re.sub(r"([:：,;])([A-Za-z])", r"\1 \2", out)
    for _ in range(3):
        nxt = _CAMEL_WORD.sub(r"\1 \2", out)
        if nxt == out:
            break
        out = nxt
    return out


def extract_translation_body(text: str, target: str = "zh") -> str:
    """从模型回包抽出译文：丢掉英文说明书、条目规范和思考过程。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    low_all = raw.lower()
    has_markers = any(m in low_all for m in _INSTRUCTION_MARKERS) or bool(_PREAMBLE_LINE.search(raw))
    if not has_markers:
        return raw
    tgt = (target or "zh").lower()
    want_zh = tgt.startswith("zh")
    kept: list[str] = []
    started = False
    for line in raw.splitlines():
        blob = line.strip()
        if not blob:
            if started and kept and kept[-1] != "":
                kept.append("")
            continue
        low = blob.lower()
        cjk = cjk_count(blob)
        latin = sum(1 for ch in blob if ch.isascii() and ch.isalpha())
        preamble = any(m in low for m in _INSTRUCTION_MARKERS) or bool(_PREAMBLE_LINE.match(blob))
        label_only = bool(_LABEL_LINE.match(blob)) and cjk < 4
        latin_only = want_zh and cjk < 2 and latin > 16
        if not started:
            if preamble and cjk < 8:
                continue
            if latin_only or label_only:
                continue
            started = True
        if (preamble and cjk < 8) or label_only or latin_only:
            continue
        kept.append(_trim_leading_english(blob, want_zh))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _trim_leading_english(blob: str, want_zh: bool) -> str:
    """一行里先英文规范后中文时，从第一段中文截起。"""
    if not want_zh or not blob:
        return blob
    first = next((i for i, ch in enumerate(blob) if "\u4e00" <= ch <= "\u9fff"), -1)
    if first < 0:
        return blob
    latin_before = sum(1 for ch in blob[:first] if ch.isascii() and ch.isalpha())
    if latin_before < 24:
        return blob
    return blob[first:].lstrip(" -—:：")


def strict_translate_retry_messages(messages: list[dict], target: str = "zh") -> list[dict]:
    """第一次若只吐说明书，收紧再要一次，不另开 Agent。"""
    user = ""
    for m in messages or []:
        if m.get("role") == "user":
            user = m.get("content") or ""
            break
    marker = "【下面才是要处理的正文】"
    if marker in user:
        user = user.split(marker, 1)[-1].strip()
    tgt = (target or "zh").strip() or "zh"
    return [
        {
            "role": "system",
            "content": (
                f"立即把下面正文译成{tgt}。只输出译文正文。"
                f"第一个字必须是{tgt}。"
                "禁止英文说明书、条目规范、Let me、Title:、Authors:。"
            ),
        },
        {"role": "user", "content": user},
    ]


def translation_looks_like_instructions(text: str, target: str = "zh") -> bool:
    """拒收英文说明书，不要把提示词复述当译文。普通英文论文回声不算。"""
    blob = (text or "").strip().lower()
    if not blob:
        return False
    hits = [m for m in _INSTRUCTION_MARKERS if m in blob]
    if "carefully translate" in blob:
        return True
    if len(hits) >= 2:
        return True
    tgt = (target or "zh").lower()
    if tgt.startswith("zh") and hits:
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        if cjk < 12:
            return True
    return False


def apply_glossary(text: str, terms: list[dict[str, Any]]) -> str:
    """先锁定 do_not_translate 术语，再替换其余 src→tgt。公式与 citation key 由调用方预先保护。"""
    out = text
    locked: list[tuple[str, str]] = []
    for i, term in enumerate(terms):
        src = term.get("src") or ""
        if not src:
            continue
        if term.get("do_not_translate"):
            token = f"⟦GLOSS{i}⟧"
            out = out.replace(src, token)
            locked.append((token, src))
    for term in terms:
        src = term.get("src") or ""
        tgt = term.get("tgt") or ""
        if src and tgt and not term.get("do_not_translate"):
            out = out.replace(src, tgt)
    for token, src in locked:
        out = out.replace(token, src)
    return out


def protect_code_and_citations(text: str) -> tuple[str, list[tuple[str, str]]]:
    holders: list[tuple[str, str]] = []
    out = text

    def _hold(pattern_fn) -> None:
        nonlocal out
        i = 0
        while True:
            m = pattern_fn(out)
            if not m:
                return
            token = f"⟦HOLD{len(holders)}⟧"
            holders.append((token, m))
            out = out.replace(m, token, 1)
            i += 1
            if i > 200:
                return

    import re

    for m in re.findall(r"\$\$[\s\S]+?\$\$|\$[^$]+\$|`[^`]+`|\[[A-Za-z][\w:-]*\]", out):
        token = f"⟦HOLD{len(holders)}⟧"
        holders.append((token, m))
        out = out.replace(m, token, 1)
    return out, holders


def restore_holders(text: str, holders: list[tuple[str, str]]) -> str:
    for token, orig in holders:
        text = text.replace(token, orig)
    return text
