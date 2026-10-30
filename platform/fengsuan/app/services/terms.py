"""中文主题换成能打到英文库的检索词。

优先用理解层给出的英文词，其次用本组术语表，最后用内置小词表。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_LEXICON = Path(__file__).resolve().parents[1] / "data" / "topic_lexicon.json"


@lru_cache(maxsize=1)
def _lexicon() -> dict[str, list[str]]:
    try:
        raw = json.loads(_LEXICON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        text = str(key or "").strip()
        if not text:
            continue
        words = []
        for bit in val if isinstance(val, list) else []:
            word = str(bit or "").strip()
            if word and word not in words:
                words.append(word)
        if words:
            out[text] = words
    return out


def lexicon_terms(topic: str) -> list[str]:
    text = (topic or "").strip()
    table = _lexicon()
    if not text:
        return []
    if text in table:
        return list(table[text])
    if "神经" in text and "压缩" in text and "图片神经压缩" in table:
        return list(table["图片神经压缩"])
    contained = [key for key in table if key and key in text]
    if not contained:
        return []
    return list(table[max(contained, key=len)])


def glossary_terms(db, lab_id: str, topic: str) -> list[str]:
    """本组术语表里，和这个主题对得上的另一面写法。"""
    text = (topic or "").strip()
    if db is None or not lab_id or not text:
        return []
    try:
        from fengsuan.app.models.kb import GlossaryTerm
    except Exception:
        return []
    needle = text.lower()
    out: list[str] = []
    try:
        rows = db.query(GlossaryTerm).filter(GlossaryTerm.lab_id == lab_id).all()
    except Exception:
        return []
    for row in rows:
        src = str(row.src or "").strip()
        tgt = str(row.tgt or "").strip()
        if src and src.lower() in needle and tgt and tgt not in out:
            out.append(tgt)
        elif tgt and tgt.lower() in needle and src and src not in out:
            out.append(src)
        if len(out) >= 4:
            break
    return out


def derive_terms(
    topic: str,
    *,
    topic_en: list[str] | None = None,
    db=None,
    lab_id: str = "",
) -> list[str]:
    """英文检索词。理解层给了就用它，否则术语表，否则内置词表。"""
    given: list[str] = []
    for bit in topic_en or []:
        text = str(bit or "").strip()
        if text and text not in given:
            given.append(text)
        if len(given) >= 4:
            break
    if given:
        return given
    gloss = glossary_terms(db, lab_id, topic)
    if gloss:
        return gloss[:4]
    return lexicon_terms(topic)[:4]
