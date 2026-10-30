"""问题理解：意图、人名、论文、主题、时间窗。不调用模型，方便复算。"""

from __future__ import annotations

import re

_ORDINAL = re.compile(r"第\s*((?:\d+\s*[、,，]\s*)*\d+)\s*篇")
_PRONOUN = re.compile(r"(?<!其)他(?!们)|她|这位")
_YEAR_FROM = re.compile(r"(20\d{2})\s*年以来")
_RECENT = re.compile(r"近\s*(\d+)\s*年")
_QUOTED = re.compile(r"[「“\"]([^」”\"]{2,80})[」”\"]")
_ORG = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{2,24}(?:大学|学院|研究所|研究院|实验室))")
_ORG_PERSON = re.compile(r"(?:大学|学院|研究所|研究院|实验室)的([\u4e00-\u9fff]{2,4})")
_TITLE = r"(?:副教授|助理教授|副研究员|教授|研究员|老师|博士|同学)"
_COMPOUND_SURNAMES = (
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "皇甫",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "司空",
    "鲜于",
    "令狐",
    "诸葛",
)
_RESEARCH_NAME = re.compile(r"([\u4e00-\u9fff]{2,4})\s*有什么(?:科研|论文|成果|文章)")
_BARE_PERSON = re.compile(r"^(?:(?:查|搜|找|介绍)\s*一下\s*)?([\u4e00-\u9fff]{2,4})(?:是谁|这个人)?$")
_NOT_A_NAME = {
    "组里",
    "我们",
    "这个",
    "实验室",
    "大家",
    "有没有",
    "最近",
    "现在",
    "咱们",
    "本组",
    "你们",
    "你好",
    "谢谢",
    "在吗",
    "哈哈",
    "好的",
    "收到",
    "文献",
    "论文",
    "任务",
    "组会",
    "成员",
    "方向",
    "什么",
    "怎么",
    "为何",
    "帮我",
    "检索",
    "导入",
    "综述",
    "速读",
    "档案",
    "导师",
    "测试",
    "一下",
}

_BROAD = {"ai", "人工智能", "机器学习", "深度学习", "计算机", "材料", "医学"}

_INTENT = (
    ("sweep", ("全面检索", "系统检索", "中英文都")),
    ("import", ("收进组里", "导入这篇", "加到论文库", "加到库里", "再收下一批")),
    ("landscape", ("学术环境", "谁在跟进", "在外面的影响", "被引曲线", "谁引用", "引用了这篇", "引用它的", "上下游")),
    ("field_people", ("代表学者", "大牛", "顶尖团队", "谁做得好")),
    ("expert", ("谁做过", "谁懂", "谁熟悉")),
    ("web_person", ("在网上", "网上的", "学术情况", "科研情况", "学术成果", "研究成果", "研究什么", "做什么研究", "履历", "h指数", "h 指数")),
    ("frontier", ("值得读", "最新进展", "推荐论文")),
)


def understand(question: str, memory: dict | None = None) -> dict:
    q = question or ""
    low = q.lower()
    intent = ""
    for name, toks in _INTENT:
        if any(tok in q or tok in low for tok in toks):
            intent = name
            break
    if not intent and _org(q) and _ORG_PERSON.search(q):
        intent = "web_person"
    if not intent and _asked_person(q):
        intent = "web_person"
    if not intent and _research_name(q):
        intent = "web_person"
    if not intent and _bare_person(q):
        intent = "web_person"
    memory = memory or {}
    pronoun = bool(_PRONOUN.search(q))
    person = _person(q)
    remembered = memory.get("person") or {}
    if pronoun and not person and remembered.get("name"):
        person = remembered["name"]
    elif person in {"他", "她", "这位"} and remembered.get("name"):
        person = remembered["name"]
    cited = _cited_hit(q, memory)
    if cited and cited.get("person") and _usable_name(str(cited.get("person") or "")):
        person = str(cited["person"])
        intent = "web_person"
    elif cited and cited.get("affiliation"):
        intent = intent or "web_person"
    if not intent and person and _usable_name(person) and _org(q):
        intent = "web_person"
    ordinals: list[int] = []
    for group in _ORDINAL.findall(q):
        for n in re.findall(r"\d+", group):
            ordinals.append(int(n))
    papers = []
    for n in ordinals:
        for row in memory.get("papers") or []:
            if int(row.get("n") or 0) == n:
                papers.append(row)
    if intent == "import" and "再收下一批" in q:
        pool = [
            row
            for row in (memory.get("papers") or [])
            if isinstance(row, dict) and (row.get("title") or row.get("doi") or row.get("openalex_id"))
        ]
        papers = pool[10:20]
    elif intent == "import" and not papers and any(tok in q for tok in ("上面", "这几篇", "这些", "这一批", "这批", "引用它的")):
        for row in memory.get("papers") or []:
            if isinstance(row, dict) and (row.get("title") or row.get("doi") or row.get("openalex_id")):
                papers.append(row)
            if len(papers) >= 10:
                break
    years = None
    recent = _RECENT.search(q)
    since = _YEAR_FROM.search(q)
    if recent:
        years = int(recent.group(1))
    elif since:
        from fengsuan.app.db.base import utcnow

        years = max(1, utcnow().year - int(since.group(1)) + 1)
    catalog = ""
    works_page = 1
    works_sort = ""
    if intent != "import" and any(tok in q for tok in ("下一批", "再来一批", "下一页")) and "引用" not in q:
        catalog = "next"
        try:
            works_page = int(memory.get("works_page") or 1) + 1
        except (TypeError, ValueError):
            works_page = 2
    elif any(tok in q for tok in ("被引最高", "高被引")):
        catalog = "cited"
        works_sort = "cited_by_count:desc"
    elif any(tok in q for tok in ("近五年", "近几年", "最近五年")):
        catalog = "recent"
        works_sort = "publication_date:desc"
        years = years or 5
    citing_page = 1
    if "下一批" in q and "引用" in q:
        intent = "landscape"
        try:
            citing_page = int(memory.get("citing_page") or 1) + 1
        except (TypeError, ValueError):
            citing_page = 2
    if intent == "landscape" and not papers:
        picked = None
        mark = re.search(r"S\s*(\d+)", q, re.I)
        if mark:
            n = int(mark.group(1))
            for row in memory.get("papers") or []:
                if isinstance(row, dict) and int(row.get("n") or 0) == n:
                    picked = row
                    break
        if picked is None:
            for row in memory.get("papers") or []:
                if isinstance(row, dict) and (row.get("doi") or row.get("title") or row.get("openalex_id")):
                    picked = row
                    break
        if picked:
            papers = [picked]
    shelf = ""
    if any(tok in q for tok in ("奠基", "经典")) and "前沿" not in q:
        shelf = "classic"
    elif "综述" in q and "前沿" not in q:
        shelf = "reviews"
    elif any(tok in q for tok in ("前沿", "近两年")):
        shelf = "recent"
    remembered_person = memory.get("person") if isinstance(memory.get("person"), dict) else {}
    remembered_name = str(remembered_person.get("name") or "")
    if catalog and (person or remembered_name):
        person = person or remembered_name
        intent = "web_person"
    if intent == "web_person" and not person and any(tok in q for tok in ("方向", "领域", "主题")):
        intent = ""
    if not intent and person and re.search(r"的(?:学术|科研|研究)", q):
        intent = "web_person"
    wants_people = _wants_people(q)
    wants_papers = _wants_papers(q)
    if not intent and not person and _field_question(q):
        intent = "field_people" if wants_people and not wants_papers else "frontier"
    if not intent and shelf in {"classic", "reviews", "recent"} and not person:
        intent = "frontier"
    topic = _topic(q, intent)
    remembered_topic = str((memory or {}).get("topic") or "").strip()
    if (
        intent in {"frontier", "field_people"}
        and remembered_topic
        and not any(tok in q for tok in ("组里", "这个组", "库里"))
        and (not topic or (shelf and (remembered_topic in q or len(topic) > 24)))
    ):
        topic = remembered_topic
    member_hint = any(tok in q for tok in ("老师", "同学", "这位", "组里", "成员"))
    out = {
        "intent": intent,
        "person": person,
        "member_hint": member_hint,
        "topic": topic,
        "years": years,
        "wants_people": wants_people,
        "wants_papers": wants_papers,
        "ordinals": ordinals,
        "papers": papers,
        "sweep_id": str(memory.get("sweep_id") or ""),
        "affiliation": (cited or {}).get("affiliation") or _org(q),
        "broad": _is_broad(topic),
        "memory_person": memory.get("person") or {},
        "pronoun": pronoun,
        "topic_en": [],
        "name_en": [],
        "catalog": catalog,
        "works_page": works_page,
        "works_sort": works_sort,
        "shelf": shelf,
        "citing_page": citing_page,
    }
    parsed = memory.get("parsed") if isinstance(memory.get("parsed"), dict) else {}
    if parsed.get("person") and str(parsed["person"]) in q and _usable_name(str(parsed["person"])):
        out["person"] = str(parsed["person"])
        out["intent"] = out["intent"] or "web_person"
    if parsed.get("affiliation") and str(parsed["affiliation"]) in q:
        out["affiliation"] = str(parsed["affiliation"])
    if parsed.get("topic") and str(parsed["topic"]) in q and out["intent"] in {"frontier", "field_people", ""}:
        out["topic"] = str(parsed["topic"])[:80]
        if not out["intent"]:
            out["intent"] = "frontier"
    if isinstance(parsed.get("topic_en"), list):
        out["topic_en"] = [str(x) for x in parsed["topic_en"] if str(x).strip()][:4]
    if isinstance(parsed.get("name_en"), list):
        out["name_en"] = [str(x) for x in parsed["name_en"] if str(x).strip()][:4]
    out["person"] = guard_person(out.get("person") or "")
    if out["intent"] == "web_person" and not out["person"]:
        out["intent"] = ""
    return _pin_followup(q, out, memory)


def _pin_followup(question: str, out: dict, memory: dict | None) -> dict:
    """点选的追问以原句为准。模型理解不能把作者、翻页和收进组里改去别的工具。"""
    q = question or ""
    remembered = (memory or {}).get("person") if isinstance((memory or {}).get("person"), dict) else {}
    if any(tok in q for tok in ("这篇作者还写过", "作者还写过", "他还写过", "她还写过")) and remembered.get("name"):
        out["person"] = guard_person(str(remembered.get("name") or "")) or str(remembered.get("name") or "")
        out["intent"] = "web_person"
        out["memory_person"] = remembered
    if "再收下一批" in q:
        pool = [
            row
            for row in ((memory or {}).get("papers") or [])
            if isinstance(row, dict) and (row.get("title") or row.get("doi") or row.get("openalex_id"))
        ]
        out["intent"] = "import"
        out["papers"] = pool[10:20]
    elif any(tok in q for tok in ("把上面这几篇", "把引用它的")):
        out["intent"] = "import"
    if "下一批引用" in q:
        out["intent"] = "landscape"
    return out


def turn_ready(turn: dict | None) -> bool:
    return isinstance(turn, dict) and bool(turn.get("from_model")) and bool(
        turn.get("intent") or turn.get("person_name") or turn.get("topic_zh")
    )


def view_from_turn(question: str, memory: dict | None, turn: dict) -> dict:
    """模型已经理解过这一问。人名和主题用模型的，篇次和指代仍沿用会话记忆。"""
    base = understand(question, memory)
    if turn.get("intent"):
        base["intent"] = turn["intent"]
    if turn.get("person_name"):
        base["person"] = guard_person(str(turn.get("person_name") or ""))
    if turn.get("affiliation"):
        base["affiliation"] = str(turn.get("affiliation") or "")
    if turn.get("topic_zh"):
        base["topic"] = str(turn.get("topic_zh") or "")[:80]
    if isinstance(turn.get("topic_en"), list) and turn.get("topic_en"):
        base["topic_en"] = [str(x) for x in turn["topic_en"] if str(x).strip()][:4]
    if isinstance(turn.get("name_en"), list) and turn.get("name_en"):
        base["name_en"] = [str(x) for x in turn["name_en"] if str(x).strip()][:4]
    if turn.get("years"):
        base["years"] = turn.get("years")
    wants = [str(bit) for bit in (turn.get("wants") or []) if str(bit)]
    if "people" in wants:
        base["wants_people"] = True
    if "papers" in wants or "recent" in wants or "classic" in wants or "reviews" in wants:
        base["wants_papers"] = True
    base["wants_reviews"] = "reviews" in wants
    base["wants_classic"] = "classic" in wants
    base["wants_recent"] = "recent" in wants or "前沿" in (question or "")
    base["wants_author_works"] = "author_works" in wants
    base["wants_ingest"] = "ingest" in wants
    base["scope"] = str(turn.get("scope") or "")
    base["inferred"] = [str(x)[:40] for x in (turn.get("inferred") or []) if str(x).strip()][:3]
    if turn.get("scope") == "lab":
        base["member_hint"] = True
    elif turn.get("scope") == "outside":
        base["member_hint"] = False
    if not base.get("years") and base["wants_recent"] and not base["wants_classic"]:
        base["years"] = 2
    base["plan"] = turn.get("plan") if isinstance(turn.get("plan"), dict) else {}
    base["affiliation_en"] = str(turn.get("affiliation_en") or "")
    base["summary"] = str(turn.get("summary") or "")
    base["from_model"] = True
    base["person"] = guard_person(base.get("person") or "")
    if base["intent"] == "web_person" and not base["person"]:
        base["intent"] = ""
    return _pin_followup(question, base, memory)


def _trim_title_name(raw: str) -> str:
    """「学的薛海」收成「薛海」。复姓可以到四个字。"""
    name = re.sub(r"^[的是有和与及]+", "", (raw or "").strip())
    if _usable_name(name) and len(name) <= 3:
        return name
    if len(name) >= 4 and any(name.startswith(s) for s in _COMPOUND_SURNAMES) and _usable_name(name[:4]):
        return name[:4]
    if len(name) >= 2 and name[-3:-2] in {"的", "学"} and _usable_name(name[-2:]):
        return name[-2:]
    if len(name) >= 3 and _usable_name(name[-3:]) and not name[-3:].startswith("的"):
        return name[-3:]
    if len(name) >= 2 and _usable_name(name[-2:]):
        return name[-2:]
    return name if _usable_name(name) else ""


def _asked_person(q: str) -> str:
    """「名字 + 这个作者/这个老师 + 有什么文章」。先去掉指代和校名，再取人名。"""
    raw = q or ""
    if not re.search(r"这个作者|这位作者|这个老师|这位老师|有什么文章", raw):
        return ""
    text = re.sub(r"的?\s*(?:这个|这位)\s*(?:作者|老师|教授|同学)", " ", raw)
    text = _ORG.sub(" ", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        head = run.split("有")[0] if "有什么" in run or run.endswith("有") else run
        if 2 <= len(head) <= 4 and _usable_name(head) and not any(tok in head for tok in ("这个", "这位", "方向", "领域", "文章", "论文")):
            return head
    return ""


def _person(q: str) -> str:
    quoted = _QUOTED.search(q or "")
    if quoted and not any(tok in quoted.group(1) for tok in ("论文", "方向")):
        return quoted.group(1).strip()
    asked = _asked_person(q or "")
    if asked:
        return asked
    text = q or ""
    lead = re.search(r"([\u4e00-\u9fff]{2,4})\s+[\u4e00-\u9fff]{2,16}(?:大学|学院|研究所|研究院)", text)
    if lead:
        name = lead.group(1)
        if any(tok in name for tok in ("一下", "查询", "介绍", "帮我", "这个", "这位")):
            name = name[-2:]
        if _usable_name(name) and not any(tok in name for tok in ("方向", "介绍", "查询")):
            return name
    loose = re.search(
        rf"(?:大学|学院|研究所|研究院|实验室)的\s*([\u4e00-\u9fff]{{2,4}}?)(?:{_TITLE}|[\s，,。]|$)",
        text,
    )
    if loose and _usable_name(loose.group(1)) and not any(tok in loose.group(1) for tok in ("这个", "这位", "介绍", "查询", "一下", "一些")):
        return loose.group(1)
    trailed = re.search(r"(?:大学|学院|研究所|研究院|实验室)\s+([\u4e00-\u9fff]{2,4})", text)
    if trailed and _usable_name(trailed.group(1)) and not any(tok in trailed.group(1) for tok in ("这个", "这位", "介绍", "什么", "学术", "成果")):
        return trailed.group(1)
    anchored = re.search(rf"的([\u4e00-\u9fff]{{2,4}}){_TITLE}", text)
    if anchored:
        name = _trim_title_name(anchored.group(1))
        if name:
            return name
    titled = re.search(rf"([\u4e00-\u9fff]{{2,4}}){_TITLE}", text)
    if titled:
        name = _trim_title_name(titled.group(1))
        if name:
            return name
    m = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text)
    if m:
        return m.group(1)
    named = _ORG_PERSON.search(q or "")
    if named and _usable_name(named.group(1)):
        return named.group(1)
    named = re.search(r"([\u4e00-\u9fff]{2,4})的(?:学术|科研|研究)", q or "")
    if named and _usable_name(named.group(1)):
        return named.group(1)
    named = _research_name(q or "")
    if named:
        return named
    named = _bare_person(q or "")
    if named:
        return named
    return ""


_NAME_NOISE = ("大学", "学院", "研究所", "研究院", "实验室", "成果", "有什么", "文章", "论文", "学术", "方向", "介绍", "查询")


def guard_person(name: str) -> str:
    """整句、校名、成果这类不能当成人名。复姓最多四个字。"""
    raw = (name or "").strip()
    text = re.sub(r"\s+", "", raw)
    if not text or text in _NOT_A_NAME:
        return ""
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    if len(cjk) > 4 or any(tok in text for tok in _NAME_NOISE):
        return ""
    return raw


def _usable_name(name: str) -> bool:
    text = (name or "").strip()
    return bool(text) and text not in _NOT_A_NAME and not text.endswith("组")


def _research_name(q: str) -> str:
    matched = _RESEARCH_NAME.search(q or "")
    if not matched or not _usable_name(matched.group(1)):
        return ""
    name = matched.group(1)
    if any(tok in name for tok in ("这个", "这位", "方向", "领域")):
        return ""
    return name


def _bare_person(q: str) -> str:
    text = re.sub(r"[\s?？。！!，,]", "", q or "")
    matched = _BARE_PERSON.fullmatch(text)
    if not matched or not _usable_name(matched.group(1)):
        return ""
    return matched.group(1)


def _cited_hit(q: str, memory: dict | None) -> dict:
    """「是S3」「上面这个」接上一轮标过号的那条。"""
    hits = (memory or {}).get("hits") or []
    if not isinstance(hits, list) or not hits:
        return {}
    found = re.search(r"S\s*(\d+)", q or "", re.I)
    if found:
        n = int(found.group(1))
        for hit in hits:
            if isinstance(hit, dict) and int(hit.get("n") or 0) == n:
                return hit
    if any(tok in (q or "") for tok in ("上面这个", "上面查", "刚才那个", "上面的这个", "查询的这个")):
        first = hits[0] if isinstance(hits[0], dict) else {}
        return first
    return {}


def _org(q: str) -> str:
    best = ""
    for matched in _ORG.finditer(q or ""):
        name = re.sub(r"^(?:说了|是|的|帮我|再|查询|一下)+", "", matched.group(1))
        if name.endswith(("大学", "学院", "研究所", "研究院", "实验室")) and (not best or len(name) < len(best)):
            best = name
    return best


_FIELD_NOISE = (
    "帮我看看",
    "查一下",
    "看一下",
    "这个方向",
    "研究方向",
    "学术方向",
    "这个领域",
    "这个主题",
    "有哪些",
    "有什么",
    "怎么样",
    "谁在做",
    "哪些人",
    "最近",
    "现在",
    "最新",
    "进展",
    "现状",
    "论文",
    "学者",
    "新的",
    "外面",
    "公开",
    "方向",
    "帮我",
    "一下",
)


def _field_question(q: str) -> bool:
    """问的是一个学术方向，不是组里的方向名单。"""
    text = q or ""
    outside_ask = any(tok in text for tok in ("这个领域", "这个方向", "好的文章", "有什么文章", "推荐", "外面", "必读", "经典"))
    if (
        any(tok in text for tok in ("这个组", "组里", "实验室", "库里", "有哪些方向", "做什么方向", "哪些方向"))
        and not outside_ask
    ):
        return False
    marks = (
        "进展",
        "论文",
        "文章",
        "文献",
        "学者",
        "现状",
        "前沿",
        "最近",
        "新的",
        "怎么样",
        "谁在做",
        "研究什么",
        "做什么",
        "哪些人",
        "必读",
        "经典",
        "入门",
        "综述",
        "推荐",
    )
    if any(tok in text for tok in ("这个方向", "研究方向", "学术方向", "这个领域", "这个主题")) and any(tok in text for tok in marks):
        return True
    if re.search(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}方向", text) and any(tok in text for tok in marks):
        return True
    if any(tok in text for tok in ("研究进展", "最新论文", "有哪些论文", "有什么新论文")) and not any(tok in text for tok in ("组里", "库里")):
        return True
    return False


def _wants_people(q: str) -> bool:
    return any(tok in (q or "") for tok in ("哪些学者", "有哪些人", "谁在做", "代表学者", "哪些人", "大牛", "顶尖团队"))


def _wants_papers(q: str) -> bool:
    return any(
        tok in (q or "")
        for tok in ("论文", "文章", "文献", "综述", "进展", "现状", "怎么样", "前沿", "最近", "新的", "值得读", "研究什么", "必读", "经典", "入门", "推荐")
    )


def _topic(q: str, intent: str) -> str:
    if intent in {"frontier", "field_people"}:
        made = re.search(r"后面要做([\u4e00-\u9fffA-Za-z0-9]{2,24})", q or "")
        if made:
            return made.group(1)[:80]
    quoted = _QUOTED.search(q or "")
    if quoted and any(tok in (q or "") for tok in ("方向", "主题", "关于", "检索", "论文")):
        return quoted.group(1).strip()[:80]
    text = q or ""
    for _name, toks in _INTENT:
        for tok in toks:
            text = text.replace(tok, " ")
    if intent in {"frontier", "field_people", "sweep", "expert"}:
        for tok in _FIELD_NOISE:
            text = text.replace(tok, " ")
    for tok in ("帮我", "一下", "有哪些", "什么", "这个方向", "近一年", "中英文都要", "请", "吗", "？", "?"):
        text = text.replace(tok, " ")
    text = re.sub(r"20\d{2}\s*年以来", " ", text)
    text = re.sub(r"近\s*\d+\s*年", " ", text)
    text = re.sub(r"第\s*\d+\s*篇", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，。、的和与呢啊")
    if len(text) < 2 or text in _NOT_A_NAME or text in {"研究", "学术", "情况", "工作", "内容", "东西", "方面"}:
        return ""
    return text[:80]


def _is_broad(topic: str) -> bool:
    t = (topic or "").strip().lower()
    return t in _BROAD


def _cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _field_web_query(topic: str, *, people: bool, papers: bool) -> str:
    if people and papers:
        tail = "论文 学者"
    elif people:
        tail = "学者"
    else:
        tail = "论文"
    return f"{topic} {tail}"


def _lab_lookup(query: str) -> list[tuple[str, dict]]:
    text = (query or "").strip()
    if len(text) < 2:
        return []
    return [("search_lab_literature", {"query": text[:80], "limit": 5, "lab_wide": True})]


def _public_lookups(query: str) -> list[tuple[str, dict]]:
    """同一句检索：公开网页一次，中文名再加知网题录。两次都要跑。"""
    q = (query or "").strip()
    if not q:
        return []
    steps = [("web_search", {"q": q})]
    if _cjk(q):
        steps.append(("web_search", {"q": q, "site": "cnki.net"}))
    return steps


def recipe_for(question: str, memory: dict | None = None, turn: dict | None = None) -> list[tuple[str, dict]]:
    """固定查证的第一步。后续步骤由 followups 接上。模型理解过就按那个走。"""
    if turn_ready(turn):
        u = view_from_turn(question, memory, turn or {})
    else:
        u = understand(question, memory)
    intent = u["intent"]
    if intent == "sweep":
        return [("sweep_plan", {"topic": u["topic"] or (question or "")[:80], "years": u["years"] or 5})]
    if intent == "import":
        refs = list(u["papers"])
        topic = u["topic"] or ""
        batch = any(tok in (question or "") for tok in ("留下", "这批", "这些", "这一批", "这几篇", "上面"))
        if not refs and u["sweep_id"] and (batch or not topic):
            refs = [{"sweep_id": u["sweep_id"]}]
        elif not refs and topic and not batch:
            refs = [{"title": topic}]
        elif not refs and u["sweep_id"]:
            refs = [{"sweep_id": u["sweep_id"]}]
        if not refs:
            return [("import_paper", {})]
        return [("import_paper", {"refs": refs[:10]})]
    if intent == "landscape":
        paper = (u["papers"] or [{}])[0]
        return [(
            "get_paper_landscape",
            {
                "doi": paper.get("doi") or "",
                "title": paper.get("title") or u["topic"],
                "openalex_id": paper.get("openalex_id") or "",
                "page": int(u.get("citing_page") or 1),
            },
        )]
    if intent == "field_people":
        topic = u["topic"] or (question or "")[:80]
        years = u["years"] or 5
        steps = [("scholar_topic_leaders", {"topic": topic, "years": years})]
        if u.get("wants_papers") and u["topic"]:
            front = {"topic": u["topic"], "years": u["years"] or 2}
            if isinstance(u.get("plan"), dict) and u.get("plan"):
                front["plan"] = u["plan"]
            steps.insert(0, ("get_direction_frontier", front))
            steps.append(("scholar_search", {"q": u["topic"], "years": u["years"] or 2}))
        if u.get("scope") == "outside":
            return steps
        steps = _lab_lookup(u["topic"]) + steps
        if u["topic"]:
            steps.append(("get_direction_tree", {}))
            steps.append(("find_lab_expert", {"topic": u["topic"][:80]}))
        if _cjk(u["topic"]):
            steps.extend(_public_lookups(_field_web_query(u["topic"], people=True, papers=bool(u.get("wants_papers")))))
        return steps
    if intent == "expert":
        return [("find_lab_expert", {"topic": u["topic"] or (question or "")[:80]})]
    if intent == "web_person":
        who = guard_person(u["person"] or "")
        if not who:
            return []
        args: dict = {"name": who[:40]}
        if u.get("affiliation"):
            args["affiliation"] = str(u["affiliation"])[:80]
        if u.get("name_en"):
            args["name_en"] = u["name_en"]
        if u.get("affiliation_en"):
            args["affiliation_en"] = u["affiliation_en"]
        if isinstance(u.get("plan"), dict) and u.get("plan"):
            args["plan"] = u["plan"]
        remembered = u.get("memory_person") if isinstance(u.get("memory_person"), dict) else {}
        if remembered.get("openalex_id") and (
            u.get("catalog") or int(u.get("works_page") or 1) > 1 or "还写过" in (question or "")
        ):
            prof = {
                "openalex_id": remembered.get("openalex_id") or "",
                "scholar_id": remembered.get("scholar_id") or "",
                "orcid": remembered.get("orcid") or "",
                "name": who or remembered.get("name") or "",
                "page": int(u.get("works_page") or 1),
                "sort": u.get("works_sort") or "cited_by_count:desc",
            }
            if u.get("years"):
                prof["years"] = u["years"]
            return [("scholar_person_profile", prof)]
        if u["member_hint"] and who and not u.get("affiliation") and u.get("scope") != "outside":
            return _lab_lookup(who) + [("get_member_identity", args), ("get_direction_tree", {})]
        if u.get("scope") == "outside":
            return [("scholar_find_person", args)]
        return _lab_lookup(who) + [("scholar_find_person", args), ("get_direction_tree", {})]
    if intent == "frontier":
        topic = u["topic"] or ""
        years = u["years"] or 2
        args = {"years": years}
        if topic:
            args["topic"] = topic
            from fengsuan.app.services.frontier import topic_terms

            ens = [str(x) for x in (u.get("topic_en") or []) if str(x).strip()]
            if not ens:
                ens = topic_terms(topic)
            if ens:
                args["queries"] = ens[:4]
            if isinstance(u.get("plan"), dict) and u.get("plan"):
                args["plan"] = u["plan"]
            if u.get("shelf"):
                args["focus"] = u["shelf"]
        steps = [("get_direction_frontier", args)]
        if u.get("wants_people") and topic:
            steps.append(("scholar_topic_leaders", {"topic": topic, "years": u["years"] or 5}))
        if u.get("scope") == "outside":
            return steps
        steps = _lab_lookup(topic) + steps
        if topic:
            steps.append(("get_direction_tree", {}))
            steps.append(("find_lab_expert", {"topic": topic[:80]}))
        return steps
    remembered = u["memory_person"]
    if u.get("pronoun") and remembered and intent not in {"import", "sweep", "expert"}:
        if remembered.get("openalex_id") or remembered.get("scholar_id") or remembered.get("orcid"):
            return [
                (
                    "scholar_person_profile",
                    {
                        "openalex_id": remembered.get("openalex_id") or "",
                        "scholar_id": remembered.get("scholar_id") or "",
                        "orcid": remembered.get("orcid") or "",
                        "name": remembered.get("name") or "",
                    },
                )
            ]
        if remembered.get("name"):
            return [("get_member_identity", {"name": remembered.get("name") or ""})]
    return []


def _script_alias(who: str, picked: dict) -> str:
    """中文名对上了英文名，或反过来，再查一次。同一种文字不重复查。"""
    who_cjk = _cjk(who)
    candidates = [str(picked.get("name") or "")] + [str(x) for x in (picked.get("alternatives") or [])]
    for alt in candidates:
        alt = alt.strip()
        if len(alt) < 2 or alt == who:
            continue
        if _cjk(alt) != who_cjk:
            return alt
    return ""


def direction_followup_query(intent: str, person: str, keyword: str) -> str:
    """问方向时用关键词本身。问人时把姓名和关键词放在一起，避免查到别人的论文。"""
    key = (keyword or "").strip()
    if len(key) < 2:
        return ""
    if intent == "web_person" and (person or "").strip():
        return f"{person.strip()} {key}"[:80]
    if intent in {"frontier", "field_people"}:
        return key[:80]
    return ""


def _extra_direction_query(topic: str, name: str, description: str, keywords: list[str]) -> str:
    """方向对上了，再用一个和问句不同的关键词去查外面。"""
    topic = (topic or "").strip()
    if len(topic) < 2 or topic == "这个方向":
        return ""
    blob = f"{name or ''} {description or ''} {' '.join(str(k) for k in keywords)}"
    if topic not in blob:
        return ""
    for key in keywords:
        key = str(key).strip()
        if len(key) < 2 or key == topic or topic in key or key in topic:
            continue
        return key[:80]
    return ""


def followups(name: str, data: dict | None) -> list[tuple[str, dict]]:
    data = data or {}
    if data.get("ask_back"):
        return []
    if name == "sweep_plan" and data.get("sweep_id"):
        sid = data["sweep_id"]
        return [("sweep_search", {"sweep_id": sid}), ("sweep_expand", {"sweep_id": sid}), ("sweep_screen", {"sweep_id": sid})]
    if name in {"scholar_find_person", "get_member_identity"}:
        if data.get("ask_back"):
            return []
        if not data.get("picked") and not data.get("miss"):
            return []
        out: list[tuple[str, dict]] = []
        picked = data.get("picked") or {}
        if picked.get("openalex_id") or picked.get("scholar_id") or picked.get("orcid"):
            out.append(("scholar_person_profile", picked))
        if data.get("covered"):
            return out
        who = str(data.get("name") or picked.get("name") or "").strip()
        aff = str(data.get("affiliation") or "").strip()
        if who:
            inst = data.get("institution") if isinstance(data.get("institution"), dict) else {}
            domain = str(inst.get("domain") or "")
            if data.get("miss") and domain:
                out.append(("web_search", {"q": who, "site": domain}))
                out.append(("web_search", {"q": f"{who} {aff}".strip()[:80], "site": "cnki.net"}))
                scholar_q = str(data.get("scholar_q") or "").strip()
                if scholar_q:
                    out.append(("scholar_search", {"q": scholar_q[:80]}))
                return out
            query = " ".join(bit for bit in (who, aff) if bit)
            out.append(("scholar_search", {"q": query[:80]}))
            alias = _script_alias(who, picked)
            if alias:
                out.append(("scholar_search", {"q": " ".join(bit for bit in (alias, aff) if bit)[:80]}))
            out.extend(_public_lookups(" ".join(bit for bit in (who, aff, "学者") if bit)))
        return out
    if name == "get_direction_tree":
        out = []
        for query in data.get("extra_queries") or []:
            query = str(query or "").strip()
            if len(query) >= 2:
                out.append(("scholar_search", {"q": query[:80], "years": 2}))
        return out[:1]
    if name == "web_search":
        url = str(data.get("top_url") or "")
        site = str(data.get("site") or "")
        hosts = ("cnki.net", "wanfangdata", "cqvip.com", "openalex.org", "orcid.org", "scholar.google", "semanticscholar.org", "dblp.org", "chinaxiv")
        if url and ((site and site in url) or any(host in url for host in hosts)):
            return [("web_read", {"url": url})]
    return []


def stub_args(name: str, question: str, memory: dict | None = None) -> dict:
    u = understand(question, memory)
    if name == "web_search":
        return {"q": u["topic"] or (question or "")[:120]}
    if name == "scholar_search":
        return {"q": u["topic"] or (question or "")[:120], "years": u["years"] or 0}
    if name in {"sweep_plan", "scholar_topic_leaders", "find_lab_expert"}:
        return {"topic": u["topic"] or (question or "")[:80], "years": u["years"] or 5}
    if name == "get_member_identity":
        return {"name": u["person"]}
    if name == "scholar_find_person":
        return {"name": u["person"] or (question or "")[:40]}
    if name == "import_paper":
        return {"refs": u["papers"] or [{"title": u["topic"]}]}
    if name == "get_paper_landscape":
        paper = (u["papers"] or [{}])[0]
        return {"doi": paper.get("doi") or "", "title": paper.get("title") or u["topic"]}
    if name == "get_direction_frontier" and u["topic"]:
        return {"topic": u["topic"]}
    return {}
