"""问句和大模型核验。测试模式不调用模型。人名和单位必须能在原句、对话或上一轮出处里找到。"""

from __future__ import annotations

import hashlib
import json
import re

_INTENTS = {"web_person", "frontier", "field_people"}
_SHELVES = {"入门综述", "奠基经典", "近两年代表", "中文综述"}
_TASKS = {
    "person_works",
    "person_profile",
    "field_reading",
    "field_people",
    "lab_lookup",
    "paper_qa",
    "relation",
    "import",
    "sweep",
    "general",
}
_WANT_OK = {"papers", "profile", "people", "reviews", "classic", "recent", "author_works", "ingest"}
_OUTSIDE_Q = re.compile(r"(前沿|领域|方向).{0,16}(论文|文献|文章)|(论文|文献).{0,12}前沿")


def asks_outside_field(question: str) -> bool:
    """问外面的方向或前沿，且没有点名本组。"""
    q = question or ""
    if any(tok in q for tok in ("组里", "我们组", "本组", "实验室")):
        return False
    return bool(_OUTSIDE_Q.search(q))


_TASK_INTENT = {
    "person_works": "web_person",
    "person_profile": "web_person",
    "field_reading": "frontier",
    "field_people": "field_people",
    "paper_qa": "passage",
    "relation": "relation",
    "import": "import",
    "sweep": "sweep",
    "lab_lookup": "direction",
}
_PLAN_KEYS = ("openalex", "scholar", "site", "cnki", "web")
_GOALS = ("入门", "找基线", "写相关工作", "找数据集或代码", "复现", "选题", "跟最新", "查人")


def _stub() -> bool:
    from fengsuan.app.services.llm import llm_stub_enabled

    return llm_stub_enabled()


def llm_json(db, user_id: str, lab_id: str, system: str, user: str, *, session_id: str = "") -> dict:
    """非流式要一个 JSON。解析失败就再要一次。两次都失败就记一条追踪。"""
    if _stub():
        return {}
    from fengsuan.app.services.llm import chat, extract_json

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (user or "")[:4000]},
    ]
    text = _chat_text(db, user_id, lab_id, messages, chat)
    data = extract_json(text)
    if data:
        return data
    retry = [
        {"role": "system", "content": system + "\n只输出一个 JSON 对象，不要代码块，不要解释。"},
        {"role": "user", "content": (user or "")[:4000]},
    ]
    text = _chat_text(db, user_id, lab_id, retry, chat)
    data = extract_json(text)
    if not data:
        _note_json_fail(db, user_id, lab_id, session_id, system, text)
    return data


def _chat_text(db, user_id: str, lab_id: str, messages: list[dict], chat) -> str:
    try:
        return chat(db, user_id=user_id, lab_id=lab_id, messages=messages, feature="agent", json_mode=True) or ""
    except Exception:
        return ""


def _note_json_fail(db, user_id: str, lab_id: str, session_id: str, system: str, text: str) -> None:
    try:
        from fengsuan.app.services.agent.trace import record

        record(
            db,
            lab_id=lab_id or "",
            user_id=user_id or "",
            session_id=session_id or "",
            feature="agent",
            scope="lab",
            step=0,
            kind="llm_json_fail",
            tool="llm_json",
            args={"system": (system or "")[:80]},
            result_summary=(text or "")[:200],
            error="parse",
        )
    except Exception:
        return


def validate_parse(question: str, data: dict) -> dict:
    q = question or ""
    out: dict = {}
    person = str((data or {}).get("person") or "").strip()
    if person and person in q:
        out["person"] = person
    aff = str((data or {}).get("affiliation") or "").strip()
    if aff and aff in q:
        out["affiliation"] = aff
    topic = str((data or {}).get("topic") or "").strip()
    if topic and topic in q:
        out["topic"] = topic[:80]
    intent = str((data or {}).get("intent") or "").strip()
    if intent in _INTENTS:
        out["intent"] = intent
    ens = []
    for bit in (data or {}).get("topic_en") or []:
        text = str(bit or "").strip()
        if text and text not in ens:
            ens.append(text[:80])
    if ens:
        out["topic_en"] = ens[:4]
    names = []
    for bit in (data or {}).get("name_en") or []:
        text = str(bit or "").strip()
        if text and text not in names:
            names.append(text[:80])
    if names:
        out["name_en"] = names[:4]
    return out


def parse_question(db, user_id: str, lab_id: str, question: str) -> dict:
    q = (question or "").strip()
    if _stub() or len(q) < 2:
        return {}
    from fengsuan.app.services.web.common import cache_get, cache_put

    cached = cache_get("parse", q[:240], 3 * 86400)
    if isinstance(cached, dict) and cached:
        return validate_parse(q, cached)
    data = llm_json(
        db,
        user_id,
        lab_id,
        "只输出 JSON。字段：intent（web_person、frontier、field_people 或空）、person、affiliation、topic、name_en、topic_en、wants（papers 或 people）。"
        "person、affiliation、topic 必须是用户原句里的连续文字，不要编造。topic_en 是主题的英文检索词，最多 4 个。",
        q[:500],
    )
    kept = validate_parse(q, data)
    if kept:
        cache_put("parse", q[:240], kept)
    return kept


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _seen(needle: str, corpus: str) -> bool:
    token = _compact(needle)
    return len(token) >= 2 and token in _compact(corpus)


def _history_blob(history: list | None) -> str:
    parts = []
    for turn in (history or [])[-4:]:
        if not isinstance(turn, dict):
            continue
        parts.append(str(turn.get("question") or turn.get("content") or "")[:300])
        parts.append(str(turn.get("answer") or "")[:300])
    return "\n".join(parts)


def _corpus(question: str, history: list | None, memory: dict | None) -> str:
    memory = memory or {}
    parts = [question or "", _history_blob(history)]
    remembered = memory.get("person") if isinstance(memory.get("person"), dict) else {}
    parts.append(str(remembered.get("name") or ""))
    parts.append(str(memory.get("topic") or ""))
    for hit in memory.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        parts.append(str(hit.get("title") or ""))
        parts.append(str(hit.get("person") or ""))
        parts.append(str(hit.get("affiliation") or ""))
        parts.append(str(hit.get("excerpt") or "")[:200])
    return "\n".join(parts)


def _hit_by_ref(ref: str, memory: dict | None) -> dict:
    found = re.search(r"S\s*(\d+)", ref or "", re.I)
    if not found:
        return {}
    n = int(found.group(1))
    for hit in (memory or {}).get("hits") or []:
        if isinstance(hit, dict) and int(hit.get("n") or 0) == n:
            return hit
    return {}


_PHRASE_STOPS = (
    "近两年代表作",
    "这个方向有哪些人",
    "有哪些人",
    "近两年",
    "代表作",
    "再看看",
    "想了解",
    "要找",
    "想找",
    "论文",
    "文献",
    "文章",
    "方法",
    "方向",
    "作者",
    "单位",
    "学校",
    "机构",
    "综述",
    "经典",
    "最近",
    "相关",
    "工作",
    "数据",
    "代码",
    "复现",
    "看看",
    "一下",
    "有没有",
    "是不是",
    "这个",
    "那个",
    "领域",
    "研究",
    "结果",
    "入门",
    "合作者",
    "还写过",
)
_PHRASE_CHARS = set("的了吗呢吧在是有和与或把被就也都还很最这那什么怎么如何哪些可以")


def _phrase_ok(text: str, corpus: str) -> bool:
    """推断句和摘要只能带语料里出现过的说法。停用词不算新事实。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    left = raw
    for word in _PHRASE_STOPS:
        left = left.replace(word, " ")
    left = "".join(ch if ch not in _PHRASE_CHARS else " " for ch in left)
    spans = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{2,}", left)
    if not spans:
        return True
    return all(_seen(span, corpus) for span in spans)


def clarify_should_ask(turn: dict | None) -> bool:
    """同名、单位没说清、主题太宽，才停下来问。其余理解记在答案里。"""
    if not isinstance(turn, dict):
        return False
    text = str(turn.get("clarify") or "")
    if not text.strip():
        return False
    if re.search(r"同名|哪一位|哪位|单位不明|哪所|哪个学校|哪个机构|太宽|过宽|范围太大|哪一类", text):
        return True
    person = turn.get("person") if isinstance(turn.get("person"), dict) else {}
    if person.get("name") and not person.get("affiliation") and any(k in text for k in ("单位", "学校", "机构")):
        return True
    return False


def search_plan_text(turn: dict | None) -> str:
    """把检索计划写成「分几步、每步查什么、为什么」。"""
    if not isinstance(turn, dict):
        return ""
    plan = turn.get("plan") if isinstance(turn.get("plan"), dict) else {}
    why = str(turn.get("summary") or "按这个问题拆开查").strip() or "按这个问题拆开查"
    labels = {"openalex": "OpenAlex", "scholar": "学术搜索", "site": "机构页", "cnki": "中文库", "web": "公开网页"}
    lines = []
    for key in ("openalex", "scholar", "cnki", "site", "web"):
        raw = plan.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        bits = [str(bit).strip() for bit in raw if str(bit).strip()][:3]
        if bits:
            lines.append(f"用{labels.get(key, key)}查「{'；'.join(bits)}」")
    if not lines:
        return ""
    return f"{why}。分 {len(lines)} 步：" + " ".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


def understanding_note(turn: dict | None) -> str:
    if not isinstance(turn, dict) or clarify_should_ask(turn):
        return ""
    text = str(turn.get("clarify") or "").strip()
    if not text:
        return ""
    if text.startswith("我按"):
        return text if text.endswith(("。", "？")) else text + "。"
    body = text[1:] if text.startswith("按") else text
    return f"我按{body}理解的。"


def turn_cache_key(user_id: str, lab_id: str, question: str, history: list | None, memory: dict | None) -> str:
    """理解缓存按人和会话记忆分开，避免把别人推出来的名字回给下一个人。"""
    memory = memory or {}
    remembered = memory.get("person") if isinstance(memory.get("person"), dict) else {}
    digest = "|".join(
        [
            str(remembered.get("name") or ""),
            str(remembered.get("openalex_id") or ""),
            str(memory.get("topic") or ""),
            str(memory.get("sweep_id") or ""),
        ]
    )[:240]
    blob = "\n".join([user_id or "", lab_id or "", question or "", _history_blob(history), digest])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _str_list(raw, limit: int) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for bit in raw or []:
        text = str(bit or "").strip()
        if text and text not in out:
            out.append(text[:80])
        if len(out) >= limit:
            break
    return out


def lab_roster_hint(db, lab_id: str) -> dict:
    """只给方向名和成员名，用来判断是不是组里的人。不带提问记录。"""
    empty = {"directions": [], "members": []}
    if db is None or not lab_id:
        return empty
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.models.lab import LabMembership, ResearchDirection

        directions = [
            row.name
            for row in db.query(ResearchDirection)
            .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
            .limit(12)
            .all()
            if row.name
        ]
        members = [
            name
            for (name,) in db.query(User.display_name)
            .join(LabMembership, LabMembership.user_id == User.id)
            .filter(LabMembership.lab_id == lab_id, LabMembership.status == "active")
            .limit(30)
            .all()
            if name
        ]
        return {"directions": directions[:12], "members": members[:30]}
    except Exception:
        return empty


def validate_turn(question: str, history: list | None, memory: dict | None, data: dict) -> dict:
    """丢掉编出来的人名和单位。英文名只留给检索，不当成已经查实的事实。"""
    if not isinstance(data, dict):
        return {}
    corpus = _corpus(question, history, memory)
    task = str(data.get("task") or "").strip()
    if task not in _TASKS:
        task = ""
    person_raw = data.get("person") if isinstance(data.get("person"), dict) else {}
    name = str(person_raw.get("name") or "").strip()
    aff = str(person_raw.get("affiliation") or "").strip()
    rejected = []
    hit = _hit_by_ref(str(data.get("refers_to") or ""), memory)
    if name and not _seen(name, corpus):
        rejected.append(name)
        name = ""
    if aff and not _seen(aff, corpus):
        rejected.append(aff)
        aff = ""
    if not name and hit.get("person") and _seen(str(hit.get("person")), corpus):
        name = str(hit.get("person")).strip()
    if not aff and hit.get("affiliation") and _seen(str(hit.get("affiliation")), corpus):
        aff = str(hit.get("affiliation")).strip()
    topic_raw = data.get("topic") if isinstance(data.get("topic"), dict) else {}
    topic = str(topic_raw.get("zh") or "").strip()
    if topic and not _seen(topic, corpus):
        topic = ""
    if not topic:
        topic = str((memory or {}).get("topic") or "").strip() if task in {"field_reading", "field_people"} else ""
        if topic and not _seen(topic, corpus):
            topic = ""
    name_en = _str_list(person_raw.get("name_en"), 4)
    topic_en = _str_list(topic_raw.get("en"), 4) + _str_list(topic_raw.get("sub"), 4)
    topic_en = [bit for bit in topic_en if not any(_seen(bad, bit) for bad in rejected)]
    topic_en = topic_en[:4]
    aff_en = str(person_raw.get("affiliation_en") or "").strip()[:80]
    anchors = [name, aff, topic, aff_en] + name_en + topic_en
    plan = {}
    raw_plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    for key in _PLAN_KEYS:
        kept = []
        for query in _str_list(raw_plan.get(key), 3):
            if any(_seen(bad, query) for bad in rejected):
                continue
            if any(bit and _seen(bit, query) for bit in anchors if bit):
                kept.append(query)
        if kept:
            plan[key] = kept
    wants = [str(bit) for bit in (data.get("wants") or []) if str(bit) in _WANT_OK]
    years = data.get("years")
    try:
        years = int(years) if years not in {None, ""} else None
    except (TypeError, ValueError):
        years = None
    if years is None and ("recent" in wants or ("前沿" in (question or "") and "classic" not in wants)):
        years = 2
    scope = str(data.get("scope") or "").strip()
    if scope not in {"lab", "outside", "both"}:
        scope = ""
    if asks_outside_field(question) and scope != "lab":
        scope = "outside"
    inferred = []
    for bit in data.get("inferred") or []:
        text = str(bit or "").strip()[:40]
        if text and text not in inferred and _phrase_ok(text, corpus):
            inferred.append(text)
        if len(inferred) >= 3:
            break
    intent = _TASK_INTENT.get(task, "")
    if not task and not name and not topic:
        return {}
    summary = str(data.get("summary") or "").strip()[:80]
    if summary and not _phrase_ok(summary, corpus):
        summary = ""
    goal = str(data.get("goal") or "").strip()
    if goal not in _GOALS:
        goal = ""
    if not topic:
        topic_en = [bit for bit in topic_en if _seen(bit, corpus)]
    return {
        "task": task or "general",
        "intent": intent,
        "scope": scope,
        "from_model": True,
        "person": {
            "name": name,
            "name_en": name_en,
            "affiliation": aff,
            "affiliation_en": aff_en,
            "dept": str(person_raw.get("dept") or "").strip()[:40],
            "role": str(person_raw.get("role") or "").strip()[:20],
        },
        "person_name": name,
        "affiliation": aff,
        "name_en": name_en,
        "affiliation_en": aff_en,
        "topic": {"zh": topic[:80], "en": topic_en},
        "topic_zh": topic[:80],
        "topic_en": topic_en,
        "wants": wants,
        "years": years,
        "plan": plan,
        "inferred": inferred,
        "refers_to": str(data.get("refers_to") or "").strip()[:8],
        "clarify": str(data.get("clarify") or "").strip()[:80],
        "summary": summary,
        "goal": goal,
    }


def infer_goal(question: str, turn: dict | None = None) -> str:
    """理解层给了目的就用；没有模型时按问句里的说法判断。"""
    if isinstance(turn, dict):
        goal = str(turn.get("goal") or "").strip()
        if goal in _GOALS:
            return goal
    q = question or ""
    rules = (
        ("写相关工作", ("相关工作", "related work")),
        ("找数据集或代码", ("数据集", "代码", "github", "开源")),
        ("找基线", ("基线", "baseline", "对比方法")),
        ("复现", ("复现",)),
        ("选题", ("选题", "做什么题")),
        ("入门", ("入门", "从哪看", "先读")),
        ("跟最新", ("最新", "近两年", "前沿")),
        ("查人", ("老师", "教授", "这位作者")),
    )
    for goal, keys in rules:
        if any(key in q for key in keys):
            return goal
    return ""


def shape_by_goal(goal: str, text: str, sources=None) -> str:
    """按用户目的换一种写法。没有对上的目的就保持原文。"""
    body = (text or "").strip()
    if not body or goal not in _GOALS:
        return body
    titles = []
    for src in getattr(sources, "items", None) or []:
        title = str(src.get("title") or "").strip()
        if title and title not in titles and src.get("kind") in {"external", "scholar", "literature"}:
            titles.append(title[:40])
        if len(titles) >= 3:
            break
    if goal == "入门" and titles:
        path = "可以分三步读：" + " ".join(f"{i}. {title}" for i, title in enumerate(titles, 1))
        return f"{path}\n{body}"
    if goal == "找基线":
        return f"按方法、指标、代码对照。\n{body}"
    if goal == "写相关工作":
        return f"按主题分组，可作相关工作素材。\n{body}"
    if goal == "找数据集或代码":
        return f"先看数据和代码在哪。\n{body}"
    if goal == "复现":
        return f"按能不能复现来看。\n{body}"
    if goal == "选题":
        return f"按还能做什么题来看。\n{body}"
    if goal == "跟最新":
        return f"按最近两年来看。\n{body}"
    if goal == "查人":
        return f"按这个人的工作来看。\n{body}"
    return body


def understand_turn(
    db,
    user_id: str,
    lab_id: str,
    question: str,
    history: list | None = None,
    memory: dict | None = None,
    lab_hint: dict | None = None,
    session_id: str = "",
) -> dict:
    """一次理解：要做什么、是谁、哪所学校、检索词。测试模式不调用模型。"""
    q = (question or "").strip()
    if _stub() or len(q) < 2:
        return {}
    from fengsuan.app.services.web.common import cache_get, cache_put

    memory = memory or {}
    key = turn_cache_key(user_id, lab_id, q, history, memory)
    cached = cache_get("turn", key, 86400)
    if isinstance(cached, dict) and cached.get("from_model"):
        return cached
    if lab_hint is None:
        lab_hint = lab_roster_hint(db, lab_id)
    hint = lab_hint or {}
    hits = []
    for hit in (memory.get("hits") or [])[:8]:
        if not isinstance(hit, dict):
            continue
        hits.append(
            {
                "n": hit.get("n"),
                "title": str(hit.get("title") or "")[:80],
                "person": str(hit.get("person") or "")[:40],
                "affiliation": str(hit.get("affiliation") or "")[:40],
            }
        )
    remembered = memory.get("person") if isinstance(memory.get("person"), dict) else {}
    outside = asks_outside_field(q)
    user = json.dumps(
        {
            "question": q[:500],
            "history": _history_blob(history)[:1200],
            "hits": hits,
            "last_person": str(remembered.get("name") or "")[:40],
            "last_topic": str(memory.get("topic") or "")[:80],
            **(
                {}
                if outside
                else {
                    "lab_directions": (hint.get("directions") or [])[:12],
                    "lab_members": (hint.get("members") or [])[:30],
                }
            ),
        },
        ensure_ascii=False,
    )
    data = llm_json(
        db,
        user_id,
        lab_id,
        "你是学术检索助手的理解层。只输出一个 JSON 对象。"
        "字段：task（person_works、person_profile、field_reading、field_people、lab_lookup、paper_qa、relation、import、sweep、general）、"
        "scope（lab、outside、both）、person（name、name_en、affiliation、affiliation_en、dept、role）、refers_to、"
        "topic（zh、en、sub）、wants（papers、profile、people、reviews、classic、recent、author_works、ingest）、years、"
        "inferred（1 到 3 条用户没说、但下一步很可能要问的短句）、"
        "goal（入门、找基线、写相关工作、找数据集或代码、复现、选题、跟最新、查人，对不上就空）、"
        "plan（openalex、scholar、site、cnki、web，每类最多 3 个短检索词）、clarify、summary。"
        "person.name 和 affiliation、topic.zh 必须是这一问、最近对话或上一轮出处里出现过的文字，不要编造。"
        "name_en 和 affiliation_en 可以是你知道的英文，只用于检索。"
        "「是S3」「上面这个」指上一轮出处里的那一位，把那个人的名字和单位填上。"
        "问成果、论文、文章用 person_works。问介绍用 person_profile。问领域有什么文章用 field_reading。"
        "问前沿且没写年份时 years 用 2，wants 带 recent。问经典 wants 带 classic。问综述 wants 带 reviews。"
        "问句写了外校，或问某个方向的前沿论文且没点名本组，scope 用 outside，不要根据实验室方向猜课题。"
        "检索词要短，不要把整句问话当检索词。summary 用一句中文。inferred 不要写成已经查到的结论。",
        user,
        session_id=session_id,
    )
    kept = validate_turn(q, history, memory, data)
    if kept:
        cache_put("turn", key, kept)
    return kept


def judge_candidates(
    db,
    user_id: str,
    lab_id: str,
    person: str,
    affiliation: str,
    rows: list[dict],
    *,
    forms: dict | None = None,
    session_id: str = "",
) -> dict:
    """把候选交给模型。名字完全对不上的不送。返回 {_key: {verdict, reason, evidence}}。"""
    if _stub():
        return {}
    pool = [row for row in rows or [] if row.get("_reason") != "名字对不上"][:30]
    if not pool:
        return {}
    brief = []
    for row in pool:
        brief.append(
            {
                "id": row.get("_key"),
                "title": str(row.get("title") or row.get("name") or "")[:120],
                "authors": [str(x) for x in (row.get("authors") or [])][:6],
                "site": row.get("site") or "",
                "excerpt": str(row.get("excerpt") or "")[:180],
                "name_hit": bool(row.get("_name_hit")),
                "affiliation_hit": bool(row.get("_affiliation_hit")),
                "official_site": bool(row.get("_official")),
            }
        )
    data = llm_json(
        db,
        user_id,
        lab_id,
        "判断每条是不是这位本人。只输出 JSON："
        "{\"items\":[{\"id\":\"\",\"verdict\":\"yes|no|unsure\",\"evidence\":\"\",\"reason\":\"\"}]}。"
        "evidence 必须是该条 title 或 excerpt 里的连续原文，并且能看到这个人的名字。reason 一句中文。不要编造。",
        json.dumps(
            {
                "person": person,
                "name_en": (forms or {}).get("latin") or [],
                "affiliation": affiliation,
                "rows": brief,
            },
            ensure_ascii=False,
        ),
        session_id=session_id,
    )
    out = {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "")
        verdict = str(item.get("verdict") or "")
        if key and verdict in {"yes", "no", "unsure"}:
            out[key] = {
                "verdict": verdict,
                "reason": str(item.get("reason") or "")[:80],
                "evidence": str(item.get("evidence") or "")[:180],
            }
    return out


def extract_profile(db, user_id: str, lab_id: str, page_text: str, person: str, *, session_id: str = "") -> dict:
    """从主页原文抽出职称、学院、方向、项目、代表作。原句不在页面里就丢掉。"""
    from fengsuan.app.services.web.person_match import page_facts

    raw = page_text or ""
    facts, titles = page_facts(raw, person)
    if _stub() or len(raw) < 20:
        return {"facts": facts, "titles": titles}
    data = llm_json(
        db,
        user_id,
        lab_id,
        "从页面原文抽出这个人的职称、学院、研究方向、项目、代表作。"
        "只输出 JSON：{\"items\":[{\"kind\":\"title|college|direction|project|work\",\"text\":\"\",\"quote\":\"\"}]}。"
        "quote 必须是页面里的连续原文。没有就不要写。",
        json.dumps({"person": person, "page": raw[:3500]}, ensure_ascii=False),
        session_id=session_id,
    )
    got_facts = []
    got_titles = []
    compact = _compact(raw)
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        if not quote or _compact(quote) not in compact:
            continue
        text = str(item.get("text") or quote).strip()[:80]
        if str(item.get("kind") or "") == "work":
            if text and text not in got_titles:
                got_titles.append(text)
        elif text and text not in got_facts:
            got_facts.append(text)
    return {"facts": (got_facts or facts)[:4], "titles": (got_titles or titles)[:4]}


def replan_queries(
    db,
    user_id: str,
    lab_id: str,
    name: str,
    affiliation: str,
    tried: list[str],
    summaries: list[str],
    *,
    name_en: list[str] | None = None,
    session_id: str = "",
) -> list[str]:
    """第一轮没核上时，请模型换最多 3 个检索词。词里必须有名字或单位。"""
    if _stub() or not name:
        return []
    data = llm_json(
        db,
        user_id,
        lab_id,
        "上一轮没有核上这个人。给最多 3 个新的短检索词。"
        "只输出 JSON：{\"queries\":[\"\"]}。每个词必须包含中文名、英文名或单位。不要重复已经查过的。",
        json.dumps(
            {
                "person": name,
                "name_en": name_en or [],
                "affiliation": affiliation,
                "tried": tried[:8],
                "saw": summaries[:6],
            },
            ensure_ascii=False,
        ),
        session_id=session_id,
    )
    anchors = [name, affiliation] + list(name_en or [])
    out = []
    seen = {str(x) for x in tried}
    for query in _str_list(data.get("queries"), 3):
        if query in seen:
            continue
        if not any(bit and _seen(bit, query) for bit in anchors):
            continue
        out.append(query[:80])
    return out[:3]


def rerank_papers(db, user_id: str, lab_id: str, topic: str, papers: list[dict]) -> list[dict]:
    """挑最多 12 篇。编号必须存在。为什么读必须取自摘要，否则用摘要开头。

    D-HONEST：把候选数与留下数写进返回的每一篇（`_pool_total` / `_dropped_count`），
    让调用方能如实写「还有多少没选上」，而不是静默丢掉其余候选。
    """
    all_rows = [paper for paper in papers or [] if paper.get("title")]
    rows = all_rows[:40]
    if _stub() or len(rows) < 2:
        return _mark_rerank(rows[:12], len(all_rows), len(rows))
    brief = []
    for i, paper in enumerate(rows, start=1):
        brief.append(
            {
                "n": i,
                "title": str(paper.get("title") or "")[:160],
                "year": paper.get("year"),
                "cited": paper.get("citation_count") or 0,
                "abstract": str(paper.get("abstract") or paper.get("excerpt") or "")[:220],
            }
        )
    data = llm_json(
        db,
        user_id,
        lab_id,
        "从编号里挑最多 12 篇，分成 入门综述、奠基经典、近两年代表、中文综述。"
        "只输出 JSON：{\"picks\":[{\"n\":1,\"shelf\":\"入门综述\",\"why\":\"\"}]}。"
        "why 必须是该篇摘要里的原句，不要编。",
        json.dumps({"topic": topic, "papers": brief}, ensure_ascii=False),
    )
    picked = []
    seen = set()
    for item in data.get("picks") or []:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if n < 1 or n > len(rows) or n in seen:
            continue
        seen.add(n)
        paper = dict(rows[n - 1])
        shelf = str(item.get("shelf") or "")
        if shelf in _SHELVES:
            paper["shelf"] = shelf
        abstract = str(paper.get("abstract") or paper.get("excerpt") or "")
        why = str(item.get("why") or "").strip()
        if why and why not in abstract:
            why = ""
        if not why and abstract:
            why = abstract[:40]
        if why:
            paper["why"] = why[:80]
        picked.append(paper)
        if len(picked) >= 12:
            break
    return _mark_rerank(picked or rows[:12], len(all_rows), len(rows))


def _mark_rerank(picked: list[dict], pool_total: int, shown_pool: int) -> list[dict]:
    """给每篇带上候选总数与未选上数，供答案如实说明筛掉了多少。"""
    dropped = max(0, shown_pool - len(picked))
    out = []
    for paper in picked:
        row = dict(paper)
        row["_pool_total"] = pool_total
        row["_dropped_count"] = dropped
        out.append(row)
    return out
