"""文献检索停止：覆盖率为主，工具次数只作上限。"""

from __future__ import annotations

import json
import re

from loguru import logger


def _blob(state: dict) -> str:
    parts: list[str] = []
    for msg in state.get("messages") or []:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("title") or ""))
        name = getattr(msg, "name", "") or ""
        if name:
            parts.append(name)
    q = state.get("question") or ""
    if q:
        parts.append(str(q))
    return "\n".join(parts)


def _titles_from_obj(raw) -> list[str]:
    titles: list[str] = []
    if isinstance(raw, dict):
        for lit in raw.get("literature") or []:
            if isinstance(lit, dict) and lit.get("title"):
                titles.append(str(lit["title"]).strip())
        return [t for t in titles if t]
    text = str(raw or "")
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        return _titles_from_obj(data)
    return []


def _lab_titles_from_sidecar(save_path: str | None) -> list[str]:
    import os

    root = (save_path or "").strip()
    if not root:
        return []
    for rel in (os.path.join(root, "lab_context.json"), os.path.join(root, "workspace", "lab_context.json")):
        if not os.path.isfile(rel):
            continue
        try:
            text = open(rel, encoding="utf-8").read()
        except OSError:
            continue
        titles = _titles_from_obj(text)
        if titles:
            return titles
    return []


def _lab_titles(state: dict, save_path: str | None = None) -> list[str]:
    titles = _titles_from_obj(state.get("lab_context") or "")
    if titles:
        return titles
    return _lab_titles_from_sidecar(save_path or state.get("save_path"))


def _structured_papers(state: dict, save_path: str | None = None) -> list[dict]:
    path = save_path or state.get("save_path") or ""
    from .literature_bib import papers_from_messages, papers_from_tool_calls_dir
    from .paper_dedupe import dedupe_paper_dicts, paper_is_identifiable

    papers: list[dict] = []
    if path:
        papers.extend(papers_from_tool_calls_dir(path))
    papers.extend(papers_from_messages(state.get("messages")))
    return dedupe_paper_dicts([p for p in papers if paper_is_identifiable(p)])


def unique_paper_count(state: dict, save_path: str | None = None) -> int:
    path = save_path or state.get("save_path") or ""
    try:
        papers = _structured_papers(state, path)
        if papers:
            return len(papers)
    except Exception as exc:
        logger.warning("unique_paper_count structured parse failed: {}", exc)
    blob = _blob(state)
    dois = set(re.findall(r"10\.\d{4,9}/[^\s\"']+", blob, re.I))
    arxiv = set(re.findall(r"\d{4}\.\d{4,5}", blob))
    titles = set(re.findall(r'"title"\s*:\s*"([^"]{8,180})"', blob))
    return len(dois | arxiv | titles)


def has_academic_identifier_coverage(state: dict, save_path: str | None = None) -> bool:
    path = save_path or state.get("save_path") or ""
    try:
        from .paper_dedupe import has_academic_identifier

        return any(has_academic_identifier(p) for p in _structured_papers(state, path))
    except Exception as exc:
        logger.warning("has_academic_identifier_coverage failed: {}", exc)
        return False


def lab_titles_covered(state: dict, save_path: str | None = None) -> bool:
    titles = _lab_titles(state, save_path or state.get("save_path"))
    if not titles:
        return True
    blob = _blob(state)
    for title in titles:
        if title in blob or f"未使用：{title}" in blob or f"未使用: {title}" in blob:
            continue
        return False
    return True


def has_abstract_or_read(state: dict, save_path: str | None = None) -> bool:
    """有标识仍不够：至少一条摘要，或已经 Read 过全文。"""
    for msg in state.get("messages") or []:
        name = (getattr(msg, "name", None) or "").lower()
        if "read" in name and "paper" in name:
            return True
    path = save_path or state.get("save_path") or ""
    try:
        from .literature_bib import load_search_hits, papers_from_messages

        hits = list(load_search_hits(path) if path else []) + papers_from_messages(state.get("messages"))
        if any(len(str(p.get("abstract") or p.get("summary") or "")) >= 80 for p in hits if isinstance(p, dict)):
            return True
    except Exception as exc:
        logger.warning("has_abstract_or_read failed: {}", exc)
    blob = _blob(state)
    return bool(re.search(r'"abstract"\s*:\s*"[^"]{80,}', blob))


def literature_search_should_stop(state: dict, config) -> bool:
    """True = 结束检索、写综述。次数是上限；覆盖到了也可以提前停。"""
    wf = getattr(config, "workflow", None)
    cap = int(getattr(wf, "literature_search_min_tool_call", 10) or 10)
    counter = int(state.get("literature_tool_call_counter") or 0)
    if counter >= cap:
        return True
    if counter < 2:
        return False
    path = getattr(config, "save_path", None) or state.get("save_path")
    if (
        unique_paper_count(state, path) >= 3
        and lab_titles_covered(state, path)
        and has_academic_identifier_coverage(state, path)
        and has_abstract_or_read(state, path)
    ):
        return True
    return False
