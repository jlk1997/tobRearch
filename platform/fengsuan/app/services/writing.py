from __future__ import annotations

import json

KIND_OUTLINES = {
    "survey": ["引言", "相关工作", "方法综述", "比较与讨论", "结论"],
    "conference": ["引言", "相关工作", "方法", "实验", "结论"],
    "journal": ["引言", "相关工作", "方法", "实验", "讨论", "结论"],
    "proposal": ["选题背景", "研究目标", "技术路线", "计划进度", "预期成果"],
    "thesis": ["绪论", "文献综述", "方法", "实验", "总结与展望"],
    "other": ["引言", "正文", "结论"],
}


def fallback_outline(kind: str, title: str) -> dict:
    sections = KIND_OUTLINES.get(kind, KIND_OUTLINES["other"])
    return {
        "title": title,
        "kind": kind,
        "sections": [{"id": f"s{i}", "heading": name, "children": []} for i, name in enumerate(sections, start=1)],
        "used_literature": False,
        "fallback": True,
    }


def outline_to_markdown(tree: dict) -> str:
    lines = [f"# {tree.get('title') or '未命名'}", ""]
    for section in tree.get("sections") or []:
        lines.append(f"## {section.get('heading')}")
        for child in section.get("children") or []:
            heading = child.get("heading") if isinstance(child, dict) else str(child)
            if heading:
                lines.append(f"### {heading}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _card_block_text(block) -> str:
    if isinstance(block, dict):
        if block.get("ungrounded"):
            return ""
        return (block.get("text") or "").strip()
    if isinstance(block, list):
        return "；".join(t for t in (_card_block_text(x) for x in block) if t)
    return str(block or "").strip()


def literature_card_hints(db, lab_id: str, item_ids: list[str]) -> list[dict]:
    """所选文献的最好方法卡摘录，供大纲使用。无卡则只留题录。"""
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.analysis import best_cards_for_items, card_is_written

    ids = [iid for iid in item_ids or [] if iid]
    cards = best_cards_for_items(db, ids)
    hints: list[dict] = []
    for iid in ids:
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None or item.lab_id != lab_id:
            continue
        raw = cards.get(iid)
        card = raw if isinstance(raw, dict) else {}
        claims = [_card_block_text(c) for c in (card.get("claims") or [])]
        from fengsuan.app.services.reproduce import evidence_brief, latest_reproduce_evidence

        ev = latest_reproduce_evidence(db, iid)
        hints.append(
            {
                "id": iid,
                "title": item.title or "",
                "has_card": card_is_written(raw) if raw else False,
                "problem": _card_block_text(card.get("problem"))[:300],
                "methods": _card_block_text(card.get("methods"))[:300],
                "limitations": _card_block_text(card.get("limitations"))[:300],
                "claims": [c[:120] for c in claims if c][:6],
                "reproduce_honest": (ev or {}).get("honest") or "",
                "reproduce_measured": bool((ev or {}).get("measured")),
                "has_reproduce": bool(ev),
                "reproduce_brief": evidence_brief(ev),
            }
        )
    return hints


def enrich_outline_with_cards(tree: dict, hints: list[dict]) -> dict:
    out = dict(tree or {})
    kids = []
    used = False
    for hint in hints or []:
        if hint.get("has_card"):
            used = True
            for claim in hint.get("claims") or []:
                kids.append({"heading": f"{hint.get('title') or '文献'}：{claim}"[:80]})
            if hint.get("methods"):
                kids.append({"heading": f"方法：{(hint.get('methods') or '')[:60]}"})
            if hint.get("limitations"):
                kids.append({"heading": f"局限：{(hint.get('limitations') or '')[:60]}"})
        if hint.get("reproduce_brief"):
            kids.append({"heading": f"复现：{(hint.get('reproduce_brief') or '')[:60]}"})
            if hint.get("has_reproduce"):
                out["used_reproduce"] = True
    sections = list(out.get("sections") or [])
    seen = {
        (ch.get("heading") if isinstance(ch, dict) else str(ch))
        for sec in sections
        if isinstance(sec, dict)
        for ch in (sec.get("children") or [])
    }
    kids = [k for k in kids if k.get("heading") and k["heading"] not in seen]
    if kids:
        attached = False
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            if "文献" in (sec.get("heading") or "") or "相关" in (sec.get("heading") or ""):
                sec["children"] = list(sec.get("children") or []) + kids[:8]
                attached = True
                break
        if not attached:
            sections.append({"id": "scard", "heading": "对照这篇怎么做", "children": kids[:8]})
        out["sections"] = sections
    out["used_method_card"] = used or bool(out.get("used_method_card"))
    return out


def owner_manuscript_blob(db, lab_id: str, owner_id: str | None) -> str:
    """作者在本实验室全部手稿正文。给未点名催条做点名过滤，不改正文。"""
    if not lab_id or not owner_id:
        return ""
    from fengsuan.app.models.manuscript import Manuscript

    rows = (
        db.query(Manuscript)
        .filter(
            Manuscript.lab_id == lab_id,
            Manuscript.owner_id == owner_id,
            Manuscript.deleted_at.is_(None),
        )
        .all()
    )
    return "\n".join((row.content_md or "") for row in rows)


def user_unused_research_note(db, lab_id: str, user_id: str | None) -> dict | None:
    """看这个人自己的综述没写到；其手稿已点名或写「未使用」则不再催写作面。"""
    owner_id = str(user_id or "")
    return writing_unused_research_note(
        db,
        lab_id,
        owner_id,
        body=owner_manuscript_blob(db, lab_id, owner_id),
    )


def writing_unused_owner_id(db, user_id: str | None, manuscript_id: str | None = None) -> str:
    """选手稿时看作者的未点名，不把导师自己的失败任务写进学生稿。"""
    mid = str(manuscript_id or "").strip()
    if mid:
        from fengsuan.app.models.manuscript import Manuscript

        host = db.get(Manuscript, mid)
        if host is not None and host.deleted_at is None and host.owner_id:
            return host.owner_id
    return str(user_id or "")


def unused_note_from_pack(lab_id: str, pack: dict | None, body: str | None) -> dict | None:
    """用已算好的未点名包对照这篇正文。图谱/列表按手稿打标，避免每人再扫一遍作业。"""
    pack = pack if isinstance(pack, dict) else {}
    if not lab_id or not int(pack.get("count") or 0):
        return None
    leftover: list[dict] = []
    blob = body if body is not None else ""
    try:
        from openlens_ai.utils.literature_bib import unused_bib_titles
    except ImportError:
        unused_bib_titles = None
    for item in pack.get("items") or []:
        if not isinstance(item, dict):
            continue
        bib = list(item.get("unused_bib_titles") or [])
        inj = list(item.get("unused_injected_titles") or [])
        if unused_bib_titles and blob:
            bib = unused_bib_titles(blob, bib)
            inj = unused_bib_titles(blob, inj)
        if bib or inj:
            leftover.append({**item, "unused_bib_titles": bib, "unused_injected_titles": inj})
    if blob and unused_bib_titles and not leftover:
        return None
    source = leftover if leftover else [item for item in (pack.get("items") or []) if isinstance(item, dict)]
    first = leftover[0] if leftover else (source[0] if source else {})
    if not isinstance(first, dict):
        first = {}
    titles: list[str] = []
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, dict):
            continue
        for title in list(item.get("unused_bib_titles") or []) + list(item.get("unused_injected_titles") or []):
            name = str(title or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            titles.append(name)
    named = titles[0] if titles else ""
    n = len(titles)
    if named and n > 1:
        text = f"上次综述没写到「{named}」等 {n} 篇。写相关工作时写到篇名，或写「未使用」，不要假装已经交代。"
    elif named:
        text = f"上次综述没写到「{named}」。写相关工作时写到篇名，或写「未使用」，不要假装已经交代。"
    else:
        text = "上次综述没写到。打开该任务恢复，或在相关工作里写到篇名 / 写「未使用」。"
    href = first.get("href") or pack.get("href") or f"/labs/{lab_id}/runs?unused=1"
    return {
        "kind": "unused_research",
        "level": "warning",
        "text": text,
        "href": href,
        "label": "打开文献调研",
        "titles": titles[:8],
        "title_more": max(0, n - 8),
        "run_id": first.get("id") or pack.get("run_id") or "",
        "count": len(leftover) if leftover else int(pack.get("count") or 0),
    }


def writing_unused_research_note(
    db,
    lab_id: str,
    user_id: str | None,
    *,
    body: str | None = None,
    manuscript_id: str | None = None,
) -> dict | None:
    """上次综述没写到，给写作诚实条。正文已点名或写「未使用」则不再催。"""
    owner_id = writing_unused_owner_id(db, user_id, manuscript_id)
    if not lab_id or not owner_id:
        return None
    from fengsuan.app.services.research_engine import research_unused_brief

    pack = research_unused_brief(db, lab_id, requester_id=owner_id)
    return unused_note_from_pack(lab_id, pack, body if body is not None else "")


def outline_honesty(
    *,
    paper_titles: list[str],
    used_card: bool,
    used_repro: bool,
    unused_research: dict | None = None,
    missing_cards: list[dict] | None = None,
) -> dict:
    """出口诚实：未结合文献 / 未结合方法卡 / 复现差距 / 综述没写到。生成不等于写回。"""
    notes: list[dict] = []
    if unused_research and unused_research.get("text"):
        notes.append(
            {
                "kind": "unused_research",
                "level": unused_research.get("level") or "warning",
                "text": unused_research["text"],
                "href": unused_research.get("href") or "",
                "label": unused_research.get("label") or "打开文献调研",
                "titles": list(unused_research.get("titles") or [])[:8],
                "title_more": int(unused_research.get("title_more") or 0),
            }
        )
    if not paper_titles:
        notes.append({"kind": "literature", "level": "info", "text": "未结合文献"})
        return {"notes": "未结合文献", "honesty_notes": notes}
    if used_card:
        notes.append({"kind": "method_card", "level": "success", "text": "已对照这篇的做法"})
    else:
        weak = [h for h in (missing_cards or []) if h.get("id")]
        first = weak[0] if weak else {}
        notes.append(
            {
                "kind": "method_card",
                "level": "info",
                "text": "还没写方法，只按题名写了大纲",
                "item_id": first.get("id") or "",
                "href": f"/literature/{first['id']}?panel=card" if first.get("id") else "",
                "titles": [
                    {
                        "id": h.get("id"),
                        "title": h.get("title") or "",
                        "href": f"/literature/{h['id']}?panel=card" if h.get("id") else "",
                    }
                    for h in weak[:3]
                ],
            }
        )
    if used_repro:
        notes.append({"kind": "reproduce", "level": "success", "text": "已结合组内复现差距"})
    else:
        notes.append({"kind": "reproduce", "level": "info", "text": "所选文献尚无组内实测差距"})
    if not used_card:
        primary = "还没写方法，只按题名写了大纲" + (
            "；已结合组内复现差距" if used_repro else "；所选文献尚无组内实测差距"
        )
    else:
        primary = "已结合组内复现差距" if used_repro else "所选文献尚无组内实测差距"
    return {"notes": primary, "honesty_notes": notes}


def literature_aware_outline(kind: str, title: str, paper_titles: list[str], keywords: list[str], card_hints: list[dict] | None = None) -> dict:
    tree = fallback_outline(kind, title)
    if not paper_titles and not keywords and not card_hints:
        return tree
    heads = []
    topic = paper_titles[0][:48] if paper_titles else (keywords[0] if keywords else title)
    heads.append({"id": "s1", "heading": f"围绕「{topic}」的问题与贡献", "children": []})
    if paper_titles:
        children = [{"id": f"lit{i}", "heading": t[:80]} for i, t in enumerate(paper_titles[:8], start=1)]
        heads.append({"id": "s2", "heading": "文献对照", "children": children})
    else:
        heads.append({"id": "s2", "heading": "相关工作", "children": []})
    rest = KIND_OUTLINES.get(kind, KIND_OUTLINES["other"])[2:]
    for i, name in enumerate(rest, start=3):
        heads.append({"id": f"s{i}", "heading": name, "children": []})
    tree["sections"] = heads
    tree["used_literature"] = True
    tree["fallback"] = False
    return enrich_outline_with_cards(tree, card_hints or [])


def parse_outline_json(raw: str, fallback: dict) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    if not isinstance(parsed, dict):
        return fallback
    sections = parsed.get("sections")
    if not isinstance(sections, list) or not sections:
        return fallback
    cleaned = []
    for i, sec in enumerate(sections, start=1):
        if isinstance(sec, str):
            cleaned.append({"id": f"s{i}", "heading": sec, "children": []})
            continue
        if not isinstance(sec, dict):
            continue
        heading = sec.get("heading") or sec.get("title") or ""
        if not heading:
            continue
        kids = []
        for j, ch in enumerate(sec.get("children") or [], start=1):
            if isinstance(ch, str) and ch.strip():
                kids.append({"id": f"c{i}_{j}", "heading": ch.strip()})
            elif isinstance(ch, dict) and (ch.get("heading") or ch.get("title")):
                kids.append({"id": f"c{i}_{j}", "heading": ch.get("heading") or ch.get("title")})
        cleaned.append({"id": sec.get("id") or f"s{i}", "heading": heading, "children": kids})
    if not cleaned:
        return fallback
    out = dict(fallback)
    out["title"] = parsed.get("title") or fallback.get("title")
    out["sections"] = cleaned
    out["fallback"] = False
    out["used_literature"] = True
    if "used_method_card" in fallback:
        out["used_method_card"] = fallback.get("used_method_card")
    return out


def writeback_preview(text: str, limit: int = 48) -> str:
    t = " ".join((text or "").split())
    if not t:
        return ""
    return t if len(t) <= limit else t[:limit] + "…"


def describe_writeback(*, mode: str, heading: str = "", selection: str = "", version: int | None = None) -> dict:
    """人能读懂的写回摘要，挂在现有版本快照上，不是第三条写作引擎。"""
    mode = (mode or "append").strip() or "append"
    sel = writeback_preview(selection)
    head = (heading or "").strip()
    if mode == "replace_selection":
        summary = f"替换了选区「{sel}」" if sel else "替换了选区"
    elif mode == "append_heading":
        summary = f"追加到「{head}」节" if head else "追加到指定节"
    else:
        summary = "文末追加"
    if version is not None:
        summary = f"{summary}（版本 {version}）"
    return {
        "mode": mode,
        "heading": head,
        "selection_preview": sel,
        "version": version,
        "summary": summary,
    }


def writeback_next_step(db, manuscript) -> dict | None:
    """写回之后的下一步。不是新写作引擎。"""
    if manuscript is None:
        return None
    from fengsuan.app.services.reproduce import manuscript_success_warning

    lab_id = getattr(manuscript, "lab_id", None) or ""
    mid = getattr(manuscript, "id", None) or ""
    warn = manuscript_success_warning(db, manuscript)
    if warn:
        return {
            "key": "ungrounded",
            "label": "去复现",
            "text": "正文把未测写成成功。打开手稿看警告，去复现。",
            "href": f"/manuscripts/{mid}" if mid else f"/labs/{lab_id}/manuscripts?ungrounded=1",
        }
    cov = core_literature_coverage(db, manuscript)
    n = int(cov.get("core_missing_count") or 0)
    if n > 0 and mid:
        scope = "这个大方向（含小课题）" if cov.get("includes_children") else "本方向"
        return {
            "key": "core",
            "label": "写到核心文献",
            "text": f"相关工作还没写到 {n} 篇{scope}核心文献。",
            "href": f"/manuscripts/{mid}?tab=claims",
        }
    owner_id = getattr(manuscript, "owner_id", None) or ""
    unused = writing_unused_research_note(
        db,
        lab_id,
        owner_id,
        body=getattr(manuscript, "content_md", None) or "",
        manuscript_id=mid,
    )
    if unused:
        return {
            "key": "unused_research",
            "label": unused.get("label") or "打开文献调研",
            "text": unused["text"],
            "href": unused.get("href") or f"/labs/{lab_id}/runs?unused=1",
        }
    return None


def writing_draft_next_step(lab_id: str, manuscript_id: str | None = None, *, stage: str = "section") -> dict:
    """生成大纲/章节之后的下一步。生成不等于写回。"""
    mid = (manuscript_id or "").strip()
    if stage == "created":
        if mid:
            return {
                "key": "related_work",
                "label": "写相关工作",
                "text": "手稿已建。下一步写相关工作，生成不等于写回。",
                "href": f"/labs/{lab_id}/writing?ms={mid}",
            }
        return {
            "key": "pick_ms",
            "label": "选手稿再写回",
            "text": "先选手稿。生成不会改库。",
            "href": f"/labs/{lab_id}/manuscripts",
        }
    if stage == "outline":
        if mid:
            return {
                "key": "related_work",
                "label": "写相关工作",
                "text": "大纲已出。下一步写相关工作，生成不等于写回。",
                "href": f"/labs/{lab_id}/writing?ms={mid}",
            }
        return {
            "key": "create_ms",
            "label": "从大纲创建手稿",
            "text": "先有手稿，再写相关工作。生成不会改库。",
            "href": f"/labs/{lab_id}/writing",
        }
    if mid:
        return {
            "key": "writeback",
            "label": "写回手稿",
            "text": "生成不等于写回。把草稿追加到相关工作节。",
            "href": f"/labs/{lab_id}/writing?ms={mid}",
        }
    return {
        "key": "pick_ms",
        "label": "选手稿再写回",
        "text": "先选手稿。生成不会改库。",
        "href": f"/labs/{lab_id}/manuscripts",
    }


def manuscript_page_next_step(db, manuscript) -> dict | None:
    """人已经在这篇手稿上。下一步不能再指回同一页。"""
    if manuscript is None:
        return None
    from fengsuan.app.services.reproduce import lab_unmeasured_mentioned, manuscript_success_warning

    lab_id = getattr(manuscript, "lab_id", None) or ""
    mid = getattr(manuscript, "id", None) or ""
    warn = manuscript_success_warning(db, manuscript)
    if warn:
        mentioned = lab_unmeasured_mentioned(db, lab_id, getattr(manuscript, "content_md", None) or "")
        href = ""
        if mentioned:
            href = mentioned[0].get("href") or ""
        if not href and lab_id:
            href = f"/labs/{lab_id}/reproduces"
        return {
            "key": "ungrounded",
            "label": "去复现",
            "text": "正文把未测写成成功。去复现，未测不会写成已成功。",
            "href": href,
        }
    cov = core_literature_coverage(db, manuscript)
    n = int(cov.get("core_missing_count") or 0)
    if n > 0 and lab_id and mid:
        scope = "这个大方向（含小课题）" if cov.get("includes_children") else "本方向"
        return {
            "key": "core",
            "label": "去写作写到篇名",
            "text": f"相关工作还没写到 {n} 篇{scope}核心文献。",
            "href": f"/labs/{lab_id}/writing?ms={mid}",
        }
    owner_id = getattr(manuscript, "owner_id", None) or ""
    unused = writing_unused_research_note(
        db,
        lab_id,
        owner_id,
        body=getattr(manuscript, "content_md", None) or "",
        manuscript_id=mid,
    )
    if unused:
        return {
            "key": "unused_research",
            "label": unused.get("label") or "打开文献调研",
            "text": unused["text"],
            "href": unused.get("href") or f"/labs/{lab_id}/runs?unused=1",
        }
    return None


_RELATED_HEADING_KEYS = ("相关", "综述", "related")


def _markup_heading_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            lines.append(s)
            continue
        low = s.lower()
        if low.startswith(("\\section", "\\subsection", "\\subsubsection", "\\chapter")):
            lines.append(s)
    return lines


_RELATED_PROSE_MARKS = ("没有", "还没", "尚未", "未写", "。", "！", "？", ".", "!", "?")


def is_related_work_heading(heading: str) -> bool:
    """Bare heading fields match loosely; manuscript bodies only match markup headings.

    Prose such as 「还没有相关工作标题。」 must not count as a written related-work section.
    """
    raw = (heading or "").strip()
    if not raw:
        return False
    heads = _markup_heading_lines(raw)
    if heads:
        return any(any(k in h.lower() for k in _RELATED_HEADING_KEYS) for h in heads)
    if "\n" in raw:
        return False
    if any(tok in raw for tok in _RELATED_PROSE_MARKS):
        return False
    if len(raw) > 48:
        return False
    return any(k in raw.lower() for k in _RELATED_HEADING_KEYS)


def section_card_honesty(related_ctx: dict | None) -> dict | None:
    """章节出口：未写出全文这篇怎么做时，带去提炼。"""
    ctx = related_ctx if isinstance(related_ctx, dict) else {}
    if ctx.get("used_method_card"):
        return None
    missing = [h for h in ctx.get("hints") or [] if isinstance(h, dict) and h.get("id") and not h.get("has_card")]
    if not missing:
        return None
    first = missing[0]
    return {
        "kind": "method_card",
        "level": "info",
        "text": "还没写方法，只按题名写了这一节",
        "item_id": first.get("id") or "",
        "href": f"/literature/{first['id']}?panel=card" if first.get("id") else "",
        "titles": [
            {
                "id": h.get("id"),
                "title": h.get("title") or "",
                "href": f"/literature/{h['id']}?panel=card" if h.get("id") else "",
            }
            for h in missing[:3]
        ],
    }


def related_work_context(db, lab_id: str, item_ids: list[str]) -> dict:
    """相关工作节吃方法卡与复现差距；无全文标出。"""
    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem

    hints = literature_card_hints(db, lab_id, item_ids)
    no_fulltext: list[str] = []
    for iid in item_ids or []:
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None:
            continue
        has_file = (
            db.query(LiteratureFile.id)
            .filter(LiteratureFile.item_id == iid, LiteratureFile.parse_status != "unusable")
            .first()
        )
        if not has_file:
            no_fulltext.append(item.title or iid)
    return {
        "hints": hints,
        "used_method_card": any(h.get("has_card") for h in hints),
        "used_reproduce": any(h.get("has_reproduce") for h in hints),
        "no_fulltext_titles": no_fulltext,
    }


def core_literature_coverage(db, manuscript) -> dict:
    """本方向核心文献是否在手稿正文点名。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.models.manuscript import ManuscriptDirectionLink
    from fengsuan.app.services.analysis import citation_key_in_body

    raw_dir_ids = [
        r.direction_id
        for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.manuscript_id == manuscript.id).all()
    ]
    lab_id = getattr(manuscript, "lab_id", None)
    includes_children = False
    dir_ids = raw_dir_ids
    if dir_ids and lab_id:
        from fengsuan.app.services.directions import expand_direction_ids

        dir_ids = expand_direction_ids(db, lab_id, dir_ids)
        includes_children = len(dir_ids) > len({x for x in raw_dir_ids if x})
    item_ids: list[str] = []
    pinned_n = 0
    if dir_ids:
        links = db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(dir_ids)).all()
        pinned = [r.item_id for r in links if getattr(r, "is_core", False)]
        pinned_n = len({x for x in pinned if x})
        item_ids = pinned if pinned else [r.item_id for r in links]
    rows = []
    if item_ids:
        rows = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(item_ids), LiteratureItem.deleted_at.is_(None))
            .all()
        )
    body = manuscript.content_md or ""
    mentioned = []
    missing = []
    for lit in rows:
        hit = bool(lit.title and (citation_key_in_body("", body, lit.title) or (lit.title[:16] in body)))
        entry = {"id": lit.id, "title": lit.title, "mentioned": hit, "href": f"/literature/{lit.id}"}
        (mentioned if hit else missing).append(entry)
    return {
        "core_total": len(rows),
        "core_mentioned": mentioned,
        "core_missing": missing,
        "core_mentioned_count": len(mentioned),
        "core_missing_count": len(missing),
        "core_pinned": pinned_n > 0,
        "core_pinned_count": pinned_n,
        "includes_children": includes_children,
    }


def manuscript_core_missing_count(db, manuscript) -> int:
    if manuscript is None:
        return 0
    return int(core_literature_coverage(db, manuscript).get("core_missing_count") or 0)


def manuscript_core_missing_map(db, manuscripts: list) -> dict[str, int]:
    """一次扫方向链接后按手稿计核心文献还没写进正文，避免 N+1。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.models.manuscript import ManuscriptDirectionLink
    from fengsuan.app.services.analysis import citation_key_in_body

    rows = [m for m in manuscripts if m is not None]
    if not rows:
        return {}
    ms_ids = [m.id for m in rows]
    dirs_by: dict[str, list[str]] = defaultdict(list)
    for link in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.manuscript_id.in_(ms_ids)).all():
        dirs_by[link.manuscript_id].append(link.direction_id)
    raw_dirs = {did for ids in dirs_by.values() for did in ids}
    lab_id = getattr(rows[0], "lab_id", None)
    expanded_of: dict[str, list[str]] = {}
    if raw_dirs and lab_id:
        from fengsuan.app.services.directions import expand_direction_ids

        for did in raw_dirs:
            expanded_of[did] = expand_direction_ids(db, lab_id, [did])
    all_dirs = {sid for did in raw_dirs for sid in (expanded_of.get(did) or [did])}
    lit_by_dir: dict[str, list] = defaultdict(list)
    pinned_by_dir: dict[str, list] = defaultdict(list)
    item_ids: set[str] = set()
    if all_dirs:
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(list(all_dirs))).all():
            lit_by_dir[link.direction_id].append(link.item_id)
            if getattr(link, "is_core", False):
                pinned_by_dir[link.direction_id].append(link.item_id)
            item_ids.add(link.item_id)
    titles: dict[str, str] = {}
    if item_ids:
        for lit in db.query(LiteratureItem).filter(LiteratureItem.id.in_(list(item_ids)), LiteratureItem.deleted_at.is_(None)).all():
            titles[lit.id] = lit.title or ""
    out: dict[str, int] = {}
    for m in rows:
        scoped: list[str] = []
        seen_d: set[str] = set()
        for did in dirs_by.get(m.id) or []:
            for sid in expanded_of.get(did) or [did]:
                if sid in seen_d:
                    continue
                seen_d.add(sid)
                scoped.append(sid)
        pinned = [iid for did in scoped for iid in pinned_by_dir.get(did) or []]
        wanted = pinned if pinned else [iid for did in scoped for iid in lit_by_dir.get(did) or []]
        body = m.content_md or ""
        missing = 0
        seen = set()
        for iid in wanted:
            if iid in seen or iid not in titles:
                continue
            seen.add(iid)
            title = titles[iid]
            hit = bool(title and (citation_key_in_body("", body, title) or (title[:16] in body)))
            if not hit:
                missing += 1
        out[m.id] = missing
    return out


def literature_mention_title(item) -> str:
    if item is None:
        return ""
    if isinstance(item, dict):
        return (item.get("title") or "").strip()
    return (getattr(item, "title", None) or "").strip()


def stamp_related_work_mentions(text: str, items) -> str:
    """把对比所选篇目题名写进相关工作稿，供 core_literature_coverage 点名。不是新综述。"""
    body = (text or "").rstrip()
    extra: list[str] = []
    for item in items or []:
        title = literature_mention_title(item)
        if len(title) < 8:
            continue
        if title in body or title[:16] in body:
            continue
        extra.append(f"- {title}")
    if not extra:
        return body
    block = "点名文献：\n" + "\n".join(extra)
    return f"{body}\n\n{block}" if body else block


def apply_writeback(content: str, text: str, *, mode: str = "append", selection: str = "", heading: str = "") -> str:
    mode = (mode or "append").strip() or "append"
    body = content or ""
    chunk = text or ""
    if mode == "replace_selection":
        sel = selection or ""
        if not sel:
            raise ValueError("selection")
        if sel not in body:
            raise ValueError("selection_missing")
        return body.replace(sel, chunk, 1)
    if mode == "append_heading":
        h = (heading or "").strip()
        if not h:
            raise ValueError("heading")
        marker = f"## {h}"
        idx = body.find(marker)
        if idx < 0:
            return body.rstrip() + f"\n\n## {h}\n\n{chunk}\n"
        rest = body[idx + len(marker) :]
        nxt = rest.find("\n## ")
        if nxt < 0:
            return body.rstrip() + "\n\n" + chunk + "\n"
        insert_at = idx + len(marker) + nxt
        return body[:insert_at] + "\n" + chunk + "\n" + body[insert_at:]
    return body.rstrip() + "\n\n" + chunk + "\n"
