from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator

from sqlalchemy.orm import Session

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.services.agent import trace
from fengsuan.app.services.agent.budget import Budget, budget_for
from fengsuan.app.services.agent.registry import (
    ToolContext,
    ToolResult,
    get_tool,
    kind_label,
    list_tools,
    normalize_confirm_tools,
    openai_tools,
    steer_tools,
    stub_plan,
    tool_label,
)
from fengsuan.app.services.agent.routing import TYPE_HINT, budget_block, classify_question, relation_groups

SYSTEM_BASE = """你是蜂算科研的实验室助手。回答规则按优先级：

1. 先查再答。事实性问题必须先调工具，不要凭记忆或标题推测。查不到就说查不到。
2. 每句结论后面标来源号 [S1] [S2]，对应工具返回的片段编号。没有来源的句子不要写成结论。
3. 复现、指标、是否跑通，只能引用实测证据。没有实测就说「尚未实测」，绝不能推断成功。
4. 扫描件或没有可复制文字时，直接说明这一页取不到文字，不要编造内容。
5. 用中文，像跟同组的人说话。先一句把事情说清，再分点。不要说「作为助手」「根据以上信息」「综上所述」。不要写 Looking at、Key observations 这类过程说明。不堆砌篇数，不催促对方，不评价对方进度。组外的论文要说明还没放进这个组。
6. 需要改笔记、改手稿或提交重型作业时，先说明将要做什么，等对方确认。
"""

SCOPE_HINT = {
    "paper": "当前场景：单篇精读。默认围绕用户正在看的这一页回答，必要时再取结构树、方法卡、书目或复现证据。问句里的第 N 页 / 第 N 节 / 图1 / 表1 / 引文片段 / 文中小数先对到那一页，不要被当前翻到的页压过。对照几篇方法只读已有方法卡，不要新写一篇对比。这篇被哪篇稿引用了走引用关系，不要猜。这一页中文先看已缓存的页译文，没有缓存不要当场另译。「我读到哪了」只看本人在读进度，不要把别人的在读当成自己的。「上次辅导说了什么」只看本人在这篇上的辅导记录，不要把笔记或别人的提问当成自己的。方法卡要报无依据条数、无依据主张原文和组内相关篇，不要把摘要卡说成已精读。哪年发的 / DOI / arXiv / PMID / 作者 / 稿里的引用键走书目，缺项照实说。提交前还缺什么、工人开了吗、还剩多少 token 走预检，不要猜能跑完。声称指标不是实测。",
    "lab": "当前场景：这个实验室的通用助手。组里在做什么先看组里的介绍和方向；某一篇怎么回事先看这篇的笔记；谁在做什么看成员在做的事；外面最近的论文、相关作者、该读什么看方向外面，并说明还没放进这个组。先在实验室知识库与文献库里检索，答案要指明来自哪一篇的哪一页。问组里有哪些文献、核心文献有哪些、我还没读哪些，走文献目录，不要拿空词去搜。问句里点到的作业题、作业编号、稿名、数据名、术语、人名、方向名、组会题名、作者、年份、刊物、DOI、arXiv、PMID、引用键要先按名字取，不要永远拿最近一条。点名一篇用了哪份数据先对到那一篇再取数据集。术语表没有的词要说没有，不要另编译名。DOI / arXiv 号里的数字不要当成发表年份。版本号不要当成 arXiv。点名一篇谁在读 / 我读到哪 / 被谁引用 / 上次辅导，先对到那一篇，不要被书目或当前打开的另一篇盖掉。短词如「方法」「任务」不要当成方向或人名。点名某人还没做完时只谈他的未复现稿，不要把组里卡住算成他的。等算力 / 缺数据停住走作业列表，不要写成失败。学生不能看见同学被指派的任务。上次辅导说了什么只看本人记录。这份文件 / 这份文档写了什么先开资料柜或知识库目录再看正文摘录。问作业状态、排队、失败原因、产物文件、数据集、这份数据被谁用、还能跑吗、工人开了吗、提交前缺什么、还剩多少 token、综述有没有写到、还没做完什么、哪篇把没复现写成成功、这篇被谁引用、这篇哪年发的、组里最近干什么、术语怎么译、看板上我的任务、任务是否过期、知识库有哪些文档、组里共享文件、稿上有什么评论、我读到哪了、我该做什么，必须先查工具，不要猜。",
    "onboarding": "当前场景：新生入门。先取方向树与成员名册说清「组里做什么、谁负责什么」（全组，不要只看本人方向），再建议先读哪几篇。「我该做什么」只给入门卡那一条下一步；「我的任务」走看板，不要两套待办。问句点到的人名、方向名、组会题名、篇名先按名字取。「组里谁在读」看本人方向名册；点名一篇谁在读不要被默认方向滤空。问组里有哪些文献走目录；问综述好了吗、作业卡在哪、组里有什么数据、还能跑吗、还剩多少 token、还没做完什么、最近干什么了、术语怎么译、知识库有哪些文档、组会定了什么、我读到哪了，走作业、数据集、预检、诚实口径、工作日志、术语表、文档目录、组会或本人在读工具。语气平实，不要给压力。",
    "writing": "当前场景：写作辅助。引用必须能追到文献库里的具体篇目，不得凭空造引用。问句里的稿名、方向名、DOI、arXiv、PMID、引用键要先按名字取，不要永远拿最近一稿。组里有哪些文献走目录；核心文献写进正文了吗走覆盖核对，不要和目录混。未写进综述、相关工作该点名谁、用哪份数据、这份数据被谁用、能提交吗、稿里哪些引用还没实测、哪篇把没复现写成成功、组里术语怎么译、稿上有什么评论、稿子改了几版、这篇哪年发的，必须先查工具。",
}

_CITE = re.compile(r"\[S(\d+)\]")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_PROCESS = re.compile(
    r"^(looking at|let me|based on the observations|key observations|direction tree|here is what|i'll |i will )",
    re.I,
)
_LEAK = re.compile(
    r"i should |the rule says|don't read|do not read|mark sources|like talking to a lab mate|"
    r"so the lab has|i'll present|i will present|回答要求|不要把编号|作为助手|综上所述|"
    r"最终稿|最终回答|我的回答要|按规则|get_member_profile|get_author|paper author|"
    r"每句结论后标|不要催促|要不要我|要不要再去|同一抓取",
    re.I,
)
_SCRATCH = re.compile(
    r"最终稿|最终回答|我的回答要|按规则|get_member_profile|paper author|每句结论后标|不要催促对方|要不要我去|要不要再去",
)


def _strip_process_notes(answer: str) -> str:
    kept = []
    for line in (answer or "").splitlines():
        raw = line.strip()
        if raw and (_PROCESS.match(raw) or _LEAK.search(raw)):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _looks_like_scratchpad(answer: str) -> bool:
    """模型把草稿、工具名和系统要求写进给用户的回答时，整段不能用。"""
    return bool(_SCRATCH.search(answer or ""))


_KEEP_LIMITS = (
    "还没放进这个组",
    "不是排名",
    "不是权威排名",
    "还没经本人或老师确认",
    "先别当成",
    "还不是这个组的成员",
)


def _accept_spoken(note: str, spoken: str) -> bool:
    """改写只能更易读，不能丢掉出处，也不能拿掉「还没进组 / 不是排名」这类限定。"""
    text = (spoken or "").strip()
    raw = note or ""
    if len(text) < 12 or len(text) > len(raw) + 160:
        return False
    if _looks_like_scratchpad(text) or _needs_zh_rewrite("问句", text):
        return False
    labels = set(_CITE.findall(raw))
    if labels and not labels <= set(_CITE.findall(text)):
        return False
    for phrase in _KEEP_LIMITS:
        if phrase in raw and phrase not in text:
            return False
    return True


def _speak_messages(question: str, note: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "把检索笔记改成给同组的人看的短答。先一句结论，说明这个人在做什么，或这个方向现在能看到什么。"
                "再最多四个要点。保留笔记里的每个 [S#]。不要新增笔记里没有的人名、数字、单位和论文。"
                "笔记里写了还没进组、不是排名、还没确认的，这些限定要留下来。"
            ),
        },
        {"role": "user", "content": f"问题：{(question or '')[:400]}\n\n笔记：\n{(note or '')[:1800]}"},
    ]


_TITLE_NAME = re.compile(r"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{2,3})(?:老师|教授|学者|作者|院士|研究员)")


def _invented_person(answer: str, allowed: str) -> bool:
    """证据里没有的人名，带上老师、教授这类称呼时不能留下。"""
    blob = allowed or ""
    for match in _TITLE_NAME.finditer(answer or ""):
        token = match.group(1)
        if token in {"这位", "这个", "那个", "本人", "导师"}:
            continue
        if token not in blob:
            return True
    return False


def _evidence_facts(sources) -> list[dict]:
    facts = []
    for src in getattr(sources, "items", None) or []:
        if src.get("drop_reason") or not src.get("label"):
            continue
        facts.append(
            {
                "id": src.get("label"),
                "title": str(src.get("title") or "")[:160],
                "excerpt": str(src.get("excerpt") or "")[:300],
                "shelf": src.get("shelf") or "",
                "why": str(src.get("why") or "")[:80],
                "year": src.get("year") or "",
            }
        )
        if len(facts) >= 12:
            break
    return facts


def _known_cites(sources) -> set[str]:
    known = set()
    for src in getattr(sources, "items", None) or []:
        label = str(src.get("label") or "")
        if label.startswith("S") and label[1:].isdigit():
            known.add(label[1:])
        elif label.isdigit():
            known.add(label)
    return known


def _accept_written(note: str, spoken: str, sources, allowed: str) -> bool:
    """按证据写的答案：出处必须真实存在，限定语要留下。不必把笔记里的每个编号都再抄一遍。"""
    text = (spoken or "").strip()
    raw = note or ""
    if len(text) < 12 or len(text) > 1800:
        return False
    if _looks_like_scratchpad(text) or _needs_zh_rewrite("问句", text):
        return False
    cited = set(_CITE.findall(text))
    if not cited <= _known_cites(sources):
        return False
    if _CITE.findall(raw) and not cited:
        return False
    for phrase in _KEEP_LIMITS:
        if phrase in raw and phrase not in text:
            return False
    if _invented_person(text, allowed):
        return False
    return True


def _write_from_evidence(db, user_id: str, lab_id: str, question: str, note: str, sources, turn: dict | None, session_id: str) -> str:
    """用已经核对过的证据写答案。不合格就交回原来的笔记。"""
    facts = _evidence_facts(sources)
    if not facts:
        return ""
    summary = str((turn or {}).get("summary") or "")
    dropped = sum(1 for src in (getattr(sources, "items", None) or []) if src.get("drop_reason"))
    outside = isinstance(turn, dict) and turn.get("scope") == "outside"
    system = (
        "根据证据写给同组的人看的中文短答。先一句结论，说明公开记录里能看到什么。"
        "再分点列出值得读的论文，每篇带年份或被引，不要把来源号排成一串。"
        "每句结论后最多标两个证据里已有的 [S#]，不要编新编号。"
        "不要新增证据和笔记里没有的人名、数字、单位和论文。"
        "笔记里写了还不是这个组的成员、还没放进这个组、还没经本人或老师确认的，这些话要留下来。"
        "只有证据里真的标了入门综述、奠基经典、近两年代表或中文综述时，才用两句写先读哪篇、再读哪篇。没有这些分类就不要写阅读顺序。"
        "不要写工具名，不要写英文过程说明。"
    )
    if outside:
        system += "这是组外检索。不要写组里的方向、成员、谁在读，不要把领域问题写成「这个人正在做」。正文不要反复说还没放进这个组。"
    user = (
        f"问题：{(question or '')[:400]}\n理解：{summary[:200]}\n排除了 {dropped} 条对不上的结果。\n\n"
        f"证据：\n{json.dumps(facts, ensure_ascii=False)[:4000]}\n\n检索笔记：\n{(note or '')[:1800]}"
    )
    try:
        from fengsuan.app.services.llm import chat

        text = chat(
            db,
            user_id=user_id,
            lab_id=lab_id,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            feature="agent",
            json_mode=False,
            run_id=session_id,
        )
    except Exception:
        return ""
    spoken = _strip_process_notes(text or "")
    allowed = " ".join(
        [
            question or "",
            note or "",
            summary,
            " ".join(f"{src.get('title') or ''} {src.get('excerpt') or ''}" for src in (getattr(sources, "items", None) or [])),
        ]
    )
    if not _accept_written(note, spoken, sources, allowed):
        return ""
    if re.search(r"(\[S\d+\]\s*){5,}", spoken):
        return ""
    if outside and any(bad in spoken for bad in ("组里已经有方向", "这个人正在做", "谁在读")):
        return ""
    return spoken


_ASIDE = re.compile(
    r"\b(the materials|looking at|key observations|based on the observations|"
    r"there(?:'s| is) no information|none of them are identified)\b",
    re.I,
)


def _needs_zh_rewrite(question: str, answer: str) -> bool:
    """问句是中文时，英文词盖过汉字，或整段在用英文解释材料，就要换掉。"""
    if not re.search(r"[\u4e00-\u9fff]", question or ""):
        return False
    text = answer or ""
    if _ASIDE.search(text):
        return True
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    if latin >= 8 and latin > cjk / 2:
        return True
    return False


def _tool_seed_key(name: str, args: dict | None) -> str:
    """同名工具参数不同也要各跑一次。知网和公开网页不能互相挤掉。"""
    if name in {"web_search", "web_read", "scholar_search"}:
        data = args or {}
        return f"{name}:{data.get('q') or ''}|{data.get('site') or ''}|{data.get('url') or ''}"
    return name


def public_person_args(question: str, tool_args: dict | None = None, turn: dict | None = None) -> dict:
    """组内作者没对上时，用问句里的人名和单位去查公开库。"""
    from fengsuan.app.services.agent.understand import guard_person, turn_ready, understand

    if turn_ready(turn):
        got = {
            "person": (turn or {}).get("person_name") or "",
            "affiliation": (turn or {}).get("affiliation") or "",
        }
    else:
        got = understand(question or "")
    who = str((tool_args or {}).get("name") or "").strip()
    if len(who) < 2:
        who = str(got.get("person") or "").strip()
    who = guard_person(who)
    if len(who) < 2:
        return {}
    out = {"name": who[:40]}
    aff = str((tool_args or {}).get("affiliation") or got.get("affiliation") or "").strip()
    if aff:
        out["affiliation"] = aff[:40]
    if isinstance((turn or {}).get("plan"), dict) and (turn or {}).get("plan"):
        out["plan"] = turn.get("plan")
    if (turn or {}).get("name_en"):
        out["name_en"] = turn.get("name_en")
    if (turn or {}).get("affiliation_en"):
        out["affiliation_en"] = turn.get("affiliation_en")
    return out


def _drop_author_hint(answer: str) -> str:
    """内部提示不能当成给用户的回答。"""
    text = answer or ""
    if "不要凭名字猜作者" not in text and "没有对上这个作者" not in text:
        return text
    cleaned = re.sub(r"[^。\n]*不要凭名字猜作者[^。\n]*[。]?", "", text)
    cleaned = re.sub(r"没有对上这个作者[：:]?", "", cleaned)
    cleaned = re.sub(r"根据查到的材料[：:]?", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" 。")
    if len(cleaned) < 8:
        return "组里署名没有这个人。公开库这次也没对上。可以补学院、英文名或主页再查。"
    return cleaned


def narration_to_zh(hint: str, text: str) -> str:
    """一次生成里夹了英文过程说明时换掉。正文本身是英文论文则留下。"""
    raw = text or ""
    if not raw or not _CJK.search(hint or ""):
        return raw
    if not _ASIDE.search(raw):
        return raw
    return "这次写成了英文说明，没有采用。请再生成一次。"


def _is_roster_question(question: str) -> bool:
    q = question or ""
    if any(tok in q for tok in ("我的导师", "我导师", "谁带我", "我的档案")):
        return False
    if "导师" in q and any(tok in q for tok in ("谁", "哪", "有没有", "是不是")):
        return True
    return any(tok in q for tok in ("成员有谁", "有哪些人", "组里有谁", "都有谁"))


def _is_direction_question(question: str) -> bool:
    q = question or ""
    if _is_roster_question(q) or "方向" not in q:
        return False
    return any(tok in q for tok in ("做", "有哪些", "什么", "研究"))


def _is_intro_question(question: str) -> bool:
    q = question or ""
    if _is_roster_question(q) or _is_direction_question(q):
        return False
    return any(
        tok in q
        for tok in (
            "怎么称呼",
            "怎么说话",
            "组里约定",
            "要记住",
            "组里写过",
            "对外怎么",
            "称呼",
            "组里前提",
            "组里的前提",
            "什么前提",
            "有什么前提",
        )
    )


def _is_count_question(question: str) -> bool:
    q = question or ""
    if _is_roster_question(q) or _is_direction_question(q) or _is_intro_question(q):
        return False
    return any(
        tok in q
        for tok in ("多少篇", "几篇论文", "论文数量", "按年份分布", "统计一下组里", "一共有多少篇", "一共收了", "收了多少篇")
    )


def _is_catalog_question(question: str) -> bool:
    q = question or ""
    if _is_roster_question(q) or _is_direction_question(q) or _is_intro_question(q) or _is_count_question(q):
        return False
    return any(
        tok in q
        for tok in (
            "有哪些文献",
            "库里有哪些",
            "核心文献",
            "我还没读",
            "没有pdf",
            "没有 pdf",
            "文献目录",
            "年有哪些",
            "年的文献",
            "会上有哪些",
        )
    )


def _is_work_question(question: str) -> bool:
    q = question or ""
    if any(
        (
            _is_roster_question(q),
            _is_direction_question(q),
            _is_intro_question(q),
            _is_count_question(q),
            _is_catalog_question(q),
        )
    ):
        return False
    if any(tok in q for tok in ("这个组", "组里", "实验室")) and not any(
        tok in q for tok in ("谁在做", "同学在做", "成员在做")
    ):
        return False
    return any(tok in q for tok in ("谁在做", "同学在做", "成员在做", "在做什么"))


def _is_readers_question(question: str) -> bool:
    q = question or ""
    if any(tok in q for tok in ("我读到哪", "我的进度", "我读完了", "这篇我读")):
        return False
    return any(tok in q for tok in ("谁在读", "有谁读", "谁读过", "在读这篇", "这篇谁在读", "组里谁在读"))


def _blocked_by_earlier(q: str) -> bool:
    return any(
        (
            _is_roster_question(q),
            _is_direction_question(q),
            _is_intro_question(q),
            _is_count_question(q),
            _is_catalog_question(q),
            _is_work_question(q),
            _is_readers_question(q),
        )
    )


def _is_task_question(question: str) -> bool:
    q = question or ""
    if _blocked_by_earlier(q):
        return False
    return any(
        tok in q
        for tok in ("我的任务", "待办任务", "派给我", "看板任务", "过期任务", "任务到期", "看板截止", "谁的任务", "组会派了", "看板上")
    )


def _is_activity_question(question: str) -> bool:
    q = question or ""
    if _blocked_by_earlier(q) or _is_task_question(q):
        return False
    return any(
        tok in q
        for tok in ("工作日志", "组里动态", "最近干什么", "谁交了作业", "最近做了什么", "我的周志", "我写的日志", "我的工作日志")
    )


def _is_glossary_question(question: str) -> bool:
    q = question or ""
    if _blocked_by_earlier(q) or _is_task_question(q) or _is_activity_question(q):
        return False
    if any(tok in q for tok in ("页译文", "这一页中文", "这页中文", "这页译")):
        return False
    return any(tok in q for tok in ("术语表", "怎么译", "锁定不译", "组里术语", "这个词译", "术语怎么"))


def _is_meeting_question(question: str) -> bool:
    q = question or ""
    if _blocked_by_earlier(q) or _is_task_question(q) or _is_activity_question(q) or _is_glossary_question(q):
        return False
    return any(
        tok in q
        for tok in ("最近组会", "什么时候开会", "组会时间", "组会定了", "组会行动", "下次组会", "有没有组会", "有组会", "组会纪要", "上次组会")
    )


def _echoes_tools(answer: str, sources) -> bool:
    text = re.sub(r"\[S\d+\]", "", answer or "")
    text = re.sub(r"\s+", "", text)
    if len(text) < 40:
        return False
    blob = ""
    for row in getattr(sources, "items", None) or []:
        blob += re.sub(r"\s+", "", f"{row.get('excerpt') or ''}{row.get('title') or ''}")
    if len(blob) < 40:
        return False
    step = 12
    windows = 0
    hit = 0
    for i in range(0, len(text) - step + 1, step):
        windows += 1
        if text[i : i + step] in blob:
            hit += 1
    return windows >= 3 and hit / windows >= 0.6


def _teacher_rows(sources) -> list[dict]:
    return [s for s in (getattr(sources, "items", None) or []) if s.get("kind") == "member" and s.get("role") == "teacher"]


def _compose_web(question: str, sources, steps: list[dict]) -> str:
    """没有模型时的可读笔记。有模型时不走这里，让模型按出处写。"""
    web_tools = {
        "web_search",
        "web_read",
        "scholar_search",
        "scholar_find_person",
        "scholar_person_profile",
        "get_member_identity",
        "scholar_topic_leaders",
        "get_paper_landscape",
        "get_direction_frontier",
        "sweep_plan",
        "sweep_search",
        "sweep_expand",
        "sweep_screen",
        "import_paper",
        "find_lab_expert",
    }
    used = {s.get("tool") for s in steps or []}
    if not (used & web_tools):
        return ""
    notes = [str(s.get("result_summary") or "").strip() for s in steps or [] if s.get("result_summary")]
    lead = ""
    for note in notes:
        if any(tok in note for tok in ("检索说明", "已经确认", "还没确认", "可能是", "不排名", "收进组里", "限流", "没开", "没连上")):
            lead = note
    if not lead and notes:
        lead = notes[-1]
    lines = [lead] if lead else []
    papers = [
        s
        for s in sources.items
        if s.get("kind") in {"external", "scholar", "web", "web_page"} and s.get("title") and s.get("title") != "检索说明"
    ]
    if papers:
        lines.append("查到这些：")
        for src in papers[:8]:
            why = str(src.get("why") or "").strip()
            site = str(src.get("site") or src.get("provider") or "").strip()
            label = src.get("label") or ""
            bit = f"{src.get('ordinal') or ''}. 《{src.get('title')}》".lstrip(". ")
            extra = "，".join(x for x in (why, site) if x)
            if extra:
                bit += f"：{extra}"
            if label:
                bit += f" [{label}]"
            lines.append(bit)
    people = [s for s in sources.items if s.get("kind") in {"identity", "member"} and s.get("title")]
    for src in people[:6]:
        label = src.get("label") or ""
        line = src.get("title") or ""
        if src.get("kind") == "identity" and not (src.get("identity") or {}).get("confirmed"):
            line += "（待确认）"
        if label:
            line += f" [{label}]"
        if line not in "\n".join(lines):
            lines.append(line)
    if papers and used & {
        "scholar_search",
        "get_paper_landscape",
        "get_direction_frontier",
        "sweep_screen",
        "sweep_search",
        "web_search",
    }:
        lines.append("这些外面的还没放进这个组。")
    return "\n".join(line for line in lines if line).strip()[:1600]


def _page_lines(pages: list[dict]) -> list[str]:
    lines = []
    for i, src in enumerate(pages[:4], 1):
        bit = f"{i}. {src.get('title') or '网页'}"
        site = str(src.get("site") or src.get("provider") or "").strip()
        if site and "cnki" not in site:
            bit += f"（{site}）"
        excerpt = str(src.get("excerpt") or "").strip()
        if excerpt and not _SCRATCH.search(excerpt):
            bit += "：" + excerpt[:72]
        if src.get("label"):
            bit += f" [{src['label']}]"
        lines.append(bit)
    return lines


def _year_of(src: dict) -> int:
    raw = src.get("year") or src.get("publish_year") or 0
    try:
        return int(str(raw)[:4])
    except ValueError:
        return 0


def _asks_recent(question: str) -> bool:
    return any(tok in (question or "") for tok in ("最近", "近期", "新的", "进展", "怎么样", "现状"))


def _direction_parts(src: dict) -> tuple[str, list[str], str]:
    """从方向树上的说明里拆出正文、关键词和负责人。"""
    raw = str(src.get("excerpt") or "").replace("问句点名。", "").replace("你的方向。", "")
    desc, _, rest = raw.partition("关键词：")
    keys, _, tail = rest.partition("负责人：")
    owner = tail.split("子课题")[0].strip(" ：")
    keywords = []
    for bit in re.split(r"[、,，\s]+", keys):
        bit = bit.strip()
        if len(bit) >= 2 and bit not in keywords:
            keywords.append(bit)
    return desc.strip(" 。"), keywords[:4], owner


def _source_overlap(topic: str, src: dict) -> int:
    from fengsuan.app.services.sweep import _overlap

    if not topic or topic == "这个方向":
        return 1
    return _overlap(
        topic,
        {
            "title": src.get("title") or "",
            "abstract": src.get("excerpt") or "",
            "snippet": src.get("excerpt") or "",
            "citation_count": src.get("citation_count") or 0,
        },
    )


def _repeated_focus(texts: list[str], skip: str = "") -> list[str]:
    """从几篇题目里找出一起出现的说法，用来写「主要在做」。"""
    from collections import Counter

    counts: Counter[str] = Counter()
    for text in texts:
        cjk = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
        seen: set[str] = set()
        for size in (3, 2):
            for i in range(0, max(0, len(cjk) - size + 1)):
                gram = cjk[i : i + size]
                if gram in seen or gram == skip or skip and gram in skip:
                    continue
                seen.add(gram)
                counts[gram] += 1
    ranked = [gram for gram, n in counts.most_common() if n >= 2]
    picked: list[str] = []
    for gram in sorted(ranked, key=len, reverse=True):
        if any(gram in chosen or chosen in gram for chosen in picked):
            continue
        picked.append(gram)
        if len(picked) >= 2:
            break
    return picked


def _compose_person(question: str, sources, steps: list[dict], turn: dict | None = None) -> str:
    """问人时直接写公开记录。不把工具摘要和草稿交给模型再写一遍。"""
    items = getattr(sources, "items", None) or []
    identities = [s for s in items if s.get("kind") == "identity" and s.get("title")]
    papers = [
        s
        for s in items
        if s.get("kind") in {"external", "scholar"}
        and s.get("who") != "person"
        and s.get("title")
        and s.get("title") not in {"检索说明", "没有对上这个作者"}
    ]
    pages = [s for s in items if s.get("kind") in {"web", "web_page"} and s.get("title")]
    lines: list[str] = []
    if any("署名里没有" in str(s.get("result_summary") or "") for s in steps or []) and not (
        isinstance(turn, dict) and turn.get("scope") == "outside"
    ):
        lines.append("组里署名没有此人。")
    name = ""
    if identities:
        src = identities[0]
        ident = src.get("identity") if isinstance(src.get("identity"), dict) else {}
        name = src.get("title") or "这位"
        affs = [str(x) for x in (ident.get("affiliations") or []) if x][:2]
        mark = f" [{src['label']}]" if src.get("label") else ""
        if ident.get("confirmed"):
            head = f"{name} 的公开学术身份已经确认过{mark}。"
        else:
            topics = [str(x) for x in (ident.get("topics") or []) if x][:3]
            keys = [name] + [str(x) for x in (ident.get("alternatives") or []) if x]

            def _keep_title(src: dict) -> bool:
                authors = src.get("authors")
                text = " ".join(str(a) for a in authors if a) if isinstance(authors, list) else str(authors or "")
                blob = re.sub(r"\s+", "", (text + " " + str(src.get("title") or "")).lower())
                if text.strip() and not any(re.sub(r"\s+", "", k.lower()) in blob for k in keys if len(k) >= 2):
                    return False
                return True

            focus = _repeated_focus([str(p.get("title") or "") for p in papers if _keep_title(p)], skip="")
            research = [
                t
                for t in topics
                if any(k in t for k in ("研究", "方向", "隐藏", "计算", "学习", "图像", "安全", "水印"))
                and not any(bad in t for bad in ("名单", "公示", "招生", "考核", "拟录取"))
            ]
            role = [t for t in topics if t not in research]
            bio = str(ident.get("bio") or "").strip()
            if bio and name and name in bio:
                head = f"公开网页上写着，{bio[:90]}"
            elif research:
                head = f"从公开记录看，{name} 主要做{'、'.join(research)[:80]}"
                if role:
                    head += "，主页上的任职是" + "、".join(role)[:36]
            elif topics:
                head = f"从公开记录看，{name} 主要做{'、'.join(topics)[:80]}"
            elif focus:
                head = f"从查到的论文看，{name} 的题目里反复出现{'、'.join(focus)}"
            else:
                head = f"公开学术库对上了 {name}"
            if affs:
                head += "，单位写成" + "、".join(affs)
            if ident.get("h_index"):
                head += f"，h 指数 {ident['h_index']}"
            scholar = ident.get("scholar") if isinstance(ident.get("scholar"), dict) else {}
            if scholar.get("h_index"):
                head += f"，谷歌学术 h 指数 {scholar['h_index']}"
            head += f"，还没经本人或老师确认{mark}。"
        lines.append(head)
    if not name:
        from fengsuan.app.services.agent.understand import guard_person, understand

        hinted = ""
        if isinstance(turn, dict) and turn.get("person_name"):
            hinted = str(turn.get("person_name") or "")
        name = guard_person(hinted or str(understand(question).get("person") or ""))
    needles = []
    for bit in [name]:
        if bit and bit not in needles:
            needles.append(bit)
    if identities:
        ident = identities[0].get("identity") if isinstance(identities[0].get("identity"), dict) else {}
        for bit in [str(ident.get("name") or "")] + [str(x) for x in (ident.get("alternatives") or [])]:
            bit = bit.strip()
            if len(bit) >= 2 and bit not in needles:
                needles.append(bit)

    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", (text or "").lower())

    def _has_name(text: str) -> bool:
        from fengsuan.app.services.web.person_match import name_in

        return any(name_in(bit, text) for bit in needles if len(bit) >= 2)

    def _author_text(src: dict) -> str:
        authors = src.get("authors")
        if isinstance(authors, list):
            return " ".join(str(a) for a in authors if a)
        return str(authors or "")

    owned = []
    loose = []
    from fengsuan.app.services.web.person_match import looks_like_profile

    for src in papers:
        if looks_like_profile(src, name):
            src["kind"] = "web"
            continue
        byline = _author_text(src)
        if src.get("matched_person") or _has_name(byline):
            owned.append(src)
        else:
            src["drop_reason"] = src.get("drop_reason") or "名字对不上"
            loose.append(src)
    quiet_lab = isinstance(turn, dict) and turn.get("scope") == "outside"
    lab_rows = [] if quiet_lab else [s for s in items if s.get("kind") == "literature" and s.get("title")]
    byline = [s for s in lab_rows if name and name in str(s.get("authors") or "")]
    titled = [s for s in lab_rows if s not in byline and name and name in str(s.get("title") or "")]
    mentioned = [
        s
        for s in lab_rows
        if s not in byline and s not in titled and name and name in str(s.get("excerpt") or "")
    ]
    if byline:
        mark = f" [{byline[0]['label']}]" if byline[0].get("label") else ""
        lines.append(f"组里文献库里，署名对上这个名字的有这些{mark}。")
    if titled:
        mark = f" [{titled[0]['label']}]" if titled[0].get("label") else ""
        lines.append(f"组里文献库里，题名对上这个名字的有这些{mark}。")
    elif mentioned and not byline:
        mark = f" [{mentioned[0]['label']}]" if mentioned[0].get("label") else ""
        lines.append(f"组里有摘要提到这个名字的篇，不一定是他写的{mark}。")
    points: list[str] = []
    for src in (byline + titled)[:3]:
        bit = f"《{src.get('title')}》"
        if src.get("label"):
            bit += f" [{src['label']}]"
        points.append(bit)
    papers = sorted(owned, key=lambda src: (_year_of(src), int(src.get("citation_count") or 0)), reverse=True)
    shown_papers = papers[:8]
    total_works = 0
    if identities:
        ident_head = identities[0].get("identity") if isinstance(identities[0].get("identity"), dict) else {}
        try:
            total_works = int(ident_head.get("works_count") or 0)
        except (TypeError, ValueError):
            total_works = 0
    if total_works > len(shown_papers):
        lines.append(f"公开库记录约 {total_works} 篇。下面先列近作和被引较高的。")
    if shown_papers:
        head_src = shown_papers[0]
        head_mark = f" [{head_src['label']}]" if head_src.get("label") else ""
        checked = any(src.get("matched_person") and not _has_name(_author_text(src)) for src in shown_papers)
        if checked:
            lines.append(f"核对过、对得上这位的论文先是这几篇{head_mark}。")
        else:
            lines.append(f"近几年署名对上这个名字的论文先是这几篇{head_mark}。")
        for src in shown_papers:
            year = _year_of(src)
            bit = f"《{src.get('title')}》"
            if year:
                bit += f"（{year}）"
            excerpt = str(src.get("excerpt") or "").strip()
            if excerpt and excerpt not in {"可能是这位。"} and not _SCRATCH.search(excerpt):
                bit += "：" + excerpt[:48]
            if src.get("label"):
                bit += f" [{src['label']}]"
            points.append(bit)
        top = max(papers, key=lambda src: int(src.get("citation_count") or 0))
        if top not in shown_papers and int(top.get("citation_count") or 0) > 0:
            year = _year_of(top)
            extra = f"{year}，" if year else ""
            mark = f" [{top['label']}]" if top.get("label") else ""
            points.append(f"被引更高的是更早的一篇：《{top.get('title')}》（{extra}被引 {top.get('citation_count')}）{mark}")
    pages = []
    aff = ""
    try:
        from fengsuan.app.services.agent.understand import _org
        from fengsuan.app.services.web.person_match import affiliation_hit

        aff = _org(question)
    except Exception:
        aff = ""
        affiliation_hit = None
    for src in [s for s in items if s.get("kind") in {"web", "web_page"} and s.get("title")]:
        blob = f"{src.get('title') or ''} {src.get('excerpt') or ''} {src.get('url') or ''} {src.get('site') or ''}"
        if not _has_name(blob):
            src["drop_reason"] = "名字对不上"
            continue
        host = blob.lower()
        academic = any(bit in host for bit in ("cnki.net", "scholar.google", "openalex.org", "semanticscholar", "doi.org", "xueshu.baidu.com"))
        school_page = ".edu.cn" in host and any(tok in blob for tok in ("导师", "教授", "老师", "博士生导师"))
        if aff and not academic and not school_page and not (affiliation_hit and affiliation_hit(blob, [aff])):
            src["drop_reason"] = "单位对不上"
            continue
        pages.append(src)
    if pages:
        cn_pages = [src for src in pages if "cnki" in str(src.get("site") or src.get("url") or "")]
        other_pages = [src for src in pages if src not in cn_pages]
        page_bits = []
        for src in (cn_pages[:1] + other_pages[:2]):
            bit = str(src.get("title") or "网页")
            excerpt = str(src.get("excerpt") or "").strip()
            if excerpt and excerpt not in bit:
                bit += "：" + excerpt[:48]
            if src.get("label"):
                bit += f" [{src['label']}]"
            page_bits.append(bit)
        if page_bits:
            where = "知网学者页" if cn_pages and not other_pages else "公开网页"
            extra = f"{where}：" + "；".join(page_bits)
            if len(points) < 8:
                points.append(extra)
            else:
                points[-1] = points[-1].rstrip("。") + "。" + extra
    for i, point in enumerate(points[:8], 1):
        lines.append(f"{i}. {point}")
    if loose and not owned and not byline and not titled and not identities:
        return (
            f"这次没有核上同时对得上名字和单位的记录。排除了{len(loose)}条同名或名字相近的结果。"
            "可以补学院、英文名或主页链接再查。还不是这个组的成员。"
        )
    if lines:
        in_lab = any(s.get("kind") == "member" for s in items) or any(
            isinstance(s.get("identity"), dict) and s["identity"].get("user_id") for s in identities
        )
        ident = identities[0].get("identity") if identities and isinstance(identities[0].get("identity"), dict) else {}
        topics = [str(x) for x in (ident.get("topics") or []) if x]
        dirs = [] if quiet_lab else [s for s in items if s.get("kind") == "direction" and s.get("title")]
        matched = [
            s
            for s in dirs
            if any(tok and tok in f"{s.get('title') or ''}{s.get('excerpt') or ''}" for tok in topics)
        ]
        if matched:
            src = matched[0]
            mark = f" [{src['label']}]" if src.get("label") else ""
            desc, keys, owner = _direction_parts(src)
            paper_key = next(
                (
                    key
                    for key in keys
                    if any(key in str(p.get("title") or "") for p in owned)
                ),
                "",
            )
            extra = ""
            if desc:
                extra += f"说明是：{desc[:36]}"
            if owner:
                extra += f"，负责人是{owner}"
            if paper_key:
                extra += f"，查到的论文题目里也有「{paper_key}」"
            if extra:
                extra = "。" + extra
            lines.append(f"组里的方向「{src.get('title')}」和他公开记录里的主题对得上{extra}{mark}。")
        elif not quiet_lab and topics and any(s.get("tool") == "get_direction_tree" and not s.get("error") for s in steps or []):
            lines.append("组里方向没对上。")
        if not in_lab:
            lines.append("还不是这个组的成员。")
        return "\n".join(lines)[:2200]
    ran = {s.get("tool") for s in steps or []}
    if ran & {"scholar_find_person", "get_member_identity", "web_search", "scholar_person_profile", "find_author"}:
        lab = "组里署名没有这个人。" if any("署名里没有" in str(s.get("result_summary") or "") for s in steps or []) else ""
        return lab + "公开库这次也没对上。可以补学院、英文名或主页再查。"
    return ""


def _compose_reading_list(topic, papers, best_dir, desc, keywords, owner, lab_close, outside: bool = False, lead: str = "") -> str:
    """问方向时按综述、经典、近期、中文分组。没有分组标记的仍走原来的写法。"""
    lines: list[str] = []
    if best_dir and not outside:
        bits = []
        if desc:
            bits.append(f"说明是：{str(desc)[:48]}")
        if keywords:
            bits.append("关键词有" + "、".join(keywords[:3]))
        if owner:
            bits.append(f"负责人是{owner}")
        said = ("，" + "，".join(bits)) if bits else ""
        mark = f" [{best_dir['label']}]" if best_dir.get("label") else ""
        lines.append(f"组里已经有方向「{best_dir.get('title')}」{said}{mark}。")
    points = []
    if lab_close:
        bits = "、".join(f"《{src.get('title')}》" + (f" [{src['label']}]" if src.get("label") else "") for src in lab_close[:2])
        points.append(f"组里已经有和「{topic}」对得上的文献：{bits}。")
    order = ("入门综述", "奠基经典", "近两年代表", "中文综述")
    if lead == "recent":
        order = ("近两年代表", "入门综述", "奠基经典", "中文综述")
    elif lead == "classic":
        order = ("奠基经典", "入门综述", "近两年代表", "中文综述")
    elif lead == "reviews":
        order = ("入门综述", "中文综述", "奠基经典", "近两年代表")
    for shelf in order:
        group = [src for src in papers if src.get("shelf") == shelf][:3]
        if not group:
            continue
        bits = []
        for src in group:
            extra = []
            if _year_of(src):
                extra.append(str(_year_of(src)))
            if src.get("venue"):
                extra.append(str(src.get("venue"))[:24])
            if src.get("citation_count"):
                extra.append(f"被引 {src.get('citation_count')}")
            bit = f"《{src.get('title')}》"
            if extra:
                bit += "（" + "，".join(extra) + "）"
            why = str(src.get("why") or "").strip()
            if why:
                bit += "：" + why[:40]
            if src.get("label"):
                bit += f" [{src['label']}]"
            bits.append(bit)
        tail = "" if outside else "都还没放进这个组。"
        points.append(f"{shelf}：{'；'.join(bits)}。{tail}")
        if len(points) >= 4:
            break
    for i, point in enumerate(points[:4], 1):
        lines.append(f"{i}. {point}")
    if papers and not any(src.get("shelf") in {"入门综述", "中文综述"} for src in papers):
        lines.append("综述这次没分开。")
    return "\n".join(lines)[:1800]


def _compose_field(question: str, sources, steps: list[dict], turn: dict | None = None) -> str:
    """问方向时写公开论文和学者。口径写在句子里，不排名，不把库外论文说成组里的。"""
    from fengsuan.app.services.agent.understand import understand

    items = getattr(sources, "items", None) or []
    topic = ""
    for step in steps or []:
        args = step.get("args") if isinstance(step, dict) else {}
        topic = str((args or {}).get("topic") or (args or {}).get("q") or "").strip()
        if topic:
            topic = topic.replace(" 论文", "").replace(" 学者", "").strip()
            break
    if not topic and isinstance(turn, dict) and turn.get("topic_zh"):
        topic = str(turn.get("topic_zh") or "").strip()
    if not topic:
        topic = (understand(question).get("topic") or "").strip() or "这个方向"
    papers = []
    pages = []
    seen: set[str] = set()
    for src in items:
        if src.get("who") == "person" or not src.get("title"):
            continue
        if src.get("title") in {"检索说明", "没有对上这个作者"}:
            continue
        key = re.sub(r"\s+", "", str(src.get("title") or "").lower())
        if not key or key in seen:
            continue
        if src.get("kind") in {"web", "web_page"}:
            host = f"{src.get('site') or ''} {src.get('url') or ''} {src.get('href') or ''}".lower()
            if any(tok in host for tok in ("patent", "genius.", "deepwiki.", "scholar.google", "zhihu.", "csdn.")):
                continue
            if "github." in host and "代码" not in (question or "") and "复现" not in (question or ""):
                continue
            seen.add(key)
            pages.append(src)
            continue
        if src.get("kind") not in {"external", "scholar"}:
            continue
        lab_keys = {
            re.sub(r"\s+", "", str(row.get("title") or "").lower())
            for row in items
            if row.get("kind") == "literature" and row.get("title")
        }
        if key in lab_keys:
            continue
        seen.add(key)
        papers.append(src)
    papers.sort(key=lambda src: (_source_overlap(topic, src), int(src.get("citation_count") or 0)), reverse=True)
    pages.sort(key=lambda src: _source_overlap(topic, src), reverse=True)
    close = [src for src in papers if _source_overlap(topic, src) > 0]
    far = [src for src in papers if _source_overlap(topic, src) <= 0]
    if _asks_recent(question):
        close.sort(key=lambda src: (_year_of(src), int(src.get("citation_count") or 0)), reverse=True)
    close_pages = [src for src in pages if _source_overlap(topic, src) > 0]
    far_pages = [src for src in pages if _source_overlap(topic, src) <= 0]
    if topic == "这个方向":
        close, far = papers, []
        close_pages, far_pages = pages, []
    dirs = [s for s in items if s.get("kind") == "direction" and s.get("title")]
    dir_close = []
    if topic and topic != "这个方向":
        dir_close = [s for s in dirs if _source_overlap(topic, s) > 0 or topic in str(s.get("title") or "")]
        dir_close.sort(key=lambda src: _source_overlap(topic, src), reverse=True)
    best_dir = dir_close[0] if dir_close else None
    dir_desc, dir_keywords, dir_owner = _direction_parts(best_dir) if best_dir else ("", [], "")
    dir_keywords = [k for k in dir_keywords if k != topic]
    if dir_keywords and topic != "这个方向":

        def _about(src: dict) -> bool:
            if _source_overlap(topic, src) > 0:
                return True
            return any(_source_overlap(k, src) > 0 for k in dir_keywords)

        close = [src for src in papers if _about(src)]
        far = [src for src in papers if src not in close]
        if _asks_recent(question):
            close.sort(key=lambda src: (_year_of(src), int(src.get("citation_count") or 0)), reverse=True)
        close_pages = [src for src in pages if _about(src)]
        far_pages = [src for src in pages if src not in close_pages]
    in_papers = [s for s in items if s.get("who") == "person" and s.get("meter") == "in_papers" and s.get("title")]
    counted = [s for s in items if s.get("who") == "person" and s.get("meter") == "openalex_works" and s.get("title")]
    counted_close = [s for s in counted if _source_overlap(topic, s) > 0]
    counted_far = [s for s in counted if _source_overlap(topic, s) <= 0]
    if topic == "这个方向":
        counted_close, counted_far = counted, []
    elif dir_keywords:

        def _about_keyword(src: dict) -> bool:
            if _source_overlap(topic, src) > 0:
                return True
            return any(_source_overlap(k, src) > 0 for k in dir_keywords)

        counted_close = [src for src in counted if _about_keyword(src)]
        counted_far = [src for src in counted if src not in counted_close]
    lines: list[str] = []
    lab_rows = [s for s in items if s.get("kind") == "literature" and s.get("title")]
    if topic == "这个方向":
        lab_close = lab_rows
    elif dir_keywords:
        lab_close = [s for s in lab_rows if _source_overlap(topic, s) > 0 or any(_source_overlap(k, s) > 0 for k in dir_keywords)]
    else:
        lab_close = [s for s in lab_rows if _source_overlap(topic, s) > 0]
    quiet_lab = isinstance(turn, dict) and turn.get("scope") == "outside"
    if quiet_lab:
        best_dir = None
        lab_close = []
        dir_keywords = []
        dir_desc = ""
        dir_owner = ""
        in_papers = []
        counted_close = []
        counted_far = []
    shelved = [src for src in papers if src.get("shelf")]
    if shelved:
        listed = _compose_reading_list(
            topic,
            shelved,
            best_dir,
            dir_desc,
            dir_keywords,
            dir_owner,
            lab_close,
            outside=quiet_lab,
            lead="recent" if any(tok in (question or "") for tok in ("前沿", "近两年")) else "classic" if any(tok in (question or "") for tok in ("经典", "奠基")) else "reviews" if "综述" in (question or "") else "",
        )
        if listed:
            return listed
    members = [] if quiet_lab else [s for s in items if s.get("kind") == "member" and s.get("title")]
    if not quiet_lab and not lab_close and any(s.get("tool") == "search_lab_literature" for s in steps or []):
        lines.append("组里这次没对上。")
    if not quiet_lab and not members and any(s.get("tool") == "find_lab_expert" and s.get("empty") and not s.get("error") for s in steps or []):
        lines.append("组里档案没写到。")

    def _mark(src: dict) -> str:
        return f" [{src['label']}]" if src.get("label") else ""

    def _paper_bit(src: dict) -> str:
        extra = []
        if _year_of(src):
            extra.append(str(_year_of(src)))
        if src.get("citation_count"):
            extra.append(f"被引 {src.get('citation_count')}")
        bit = f"《{src.get('title')}》"
        if extra:
            bit += "（" + "，".join(extra) + "）"
        return bit + _mark(src)

    points: list[str] = []
    if lab_close:
        bits = "、".join(_paper_bit(src) for src in lab_close[:2])
        points.append(f"组里已经有和「{topic}」对得上的文献：{bits}。")
    shown = close or papers[:3]
    outside = ""
    named_in_opening = bool(best_dir and close)
    listed = close[1:] if named_in_opening else close
    if listed and close:
        lead = _paper_bit(listed[0])
        when = f"{_year_of(listed[0])} 年的" if _asks_recent(question) and _year_of(listed[0]) else ""
        focus = _repeated_focus([str(src.get("title") or "") for src in close], skip=topic)
        focus_bit = f"题目里还一起出现{'、'.join(focus)}。" if focus else ""
        if named_in_opening:
            older = "、".join(_paper_bit(src) for src in listed[:2])
            outside = f"更早但对得上的还有{older}。{focus_bit}"
        else:
            older = ""
            if len(close) > 1:
                older = "更早但对得上的还有" + "、".join(_paper_bit(src) for src in close[1:3]) + "。"
            matched_topic = _source_overlap(topic, shown[0]) > 0
            if matched_topic:
                head = f"和「{topic}」对得上的，先是{when}{lead}。"
            else:
                hit = next((k for k in dir_keywords if _source_overlap(k, shown[0]) > 0), topic)
                head = f"和「{hit}」对得上的，先是{when}{lead}。" if quiet_lab else f"和组里这个方向的关键词「{hit}」对得上的，先是{when}{lead}。"
            parked = "" if quiet_lab else "都还没放进这个组。"
            outside = f"{head}{focus_bit}{parked}{older}"
    elif shown and not named_in_opening:
        outside = (
            f"题目里没有直接出现「{topic}」。下面先按被引留下{_paper_bit(shown[0])}，先别当成这个方向的核心。"
        )
    if close and far:
        left = "、".join(_paper_bit(src) for src in far[:2])
        points.append(f"另有题目里没有直接出现「{topic}」的，先别当成这个方向的核心：{left}。")
    page_shown = close_pages[:2]
    page_point = ""
    if page_shown:
        where = "知网题录" if any("cnki" in str(src.get("site") or src.get("url") or "") for src in page_shown) else "公开网页"
        bits = "、".join(
            f"{src.get('title') or '网页'}{_mark(src)}" for src in page_shown[:2]
        )
        if close_pages:
            page_point = f"{where}里和「{topic}」对得上的题录：{bits}。"
        else:
            page_point = f"网页题录里也没有直接出现「{topic}」，下面只是搜到的页面：{bits}。"
    people_bits: list[str] = []
    if members:
        names = "、".join(f"{src.get('title')}{_mark(src)}" for src in members[:3])
        people_bits.append(f"组里档案里写到「{topic}」的有{names}，只是档案里有这个词，不是排名")
    if in_papers:
        names = "、".join(f"{src.get('title')}{_mark(src)}" for src in in_papers[:3])
        people_bits.append(f"这些对得上的论文里反复出现{names}，按出现次数说，不是排名")
    if counted_close:
        names = "、".join(f"{src.get('title')}{_mark(src)}" for src in counted_close[:3])
        people_bits.append(f"按 OpenAlex 近些年发文数，材料里写到「{topic}」的有{names}。这是发文数口径，不是权威排名")
    elif counted_far:
        names = "、".join(f"{src.get('title')}{_mark(src)}" for src in counted_far[:3])
        people_bits.append(
            f"英文库另按发文数统计到{names}，材料里没有直接写「{topic}」。这是发文数口径，不是权威排名"
        )
    people_point = "。".join(people_bits) + "。" if people_bits else ""
    if outside:
        points.insert(1 if lab_close else 0, outside)
    ordered = [bit for bit in (page_point, people_point) if bit]
    room = 4 - len(points)
    if room > 0:
        points.extend(ordered[:room])
        leftover = "".join(ordered[room:])
    else:
        leftover = "".join(ordered)
    if leftover and points:
        points[-1] = points[-1].rstrip("。") + "。" + leftover
    elif leftover:
        points.append(leftover)
    for i, point in enumerate(points[:4], 1):
        lines.append(f"{i}. {point}")
    if best_dir and not quiet_lab:
        src = best_dir
        bits = []
        if dir_desc:
            bits.append(f"说明是：{dir_desc[:48]}")
        if dir_keywords:
            bits.append("关键词有" + "、".join(dir_keywords[:3]))
        if dir_owner:
            bits.append(f"负责人是{dir_owner}")
        said = ("，" + "，".join(bits)) if bits else ""
        mark = f" [{src['label']}]" if src.get("label") else ""
        newest = ""
        if close:
            top = close[0]
            year = _year_of(top)
            when = f"{year} 年的" if year else ""
            cite = f" [{top['label']}]" if top.get("label") else ""
            newest = f"外面先看到{when}《{top.get('title')}》，都还没放进这个组{cite}。"
        lines.insert(0, f"组里已经有方向「{src.get('title')}」{said}{mark}。{newest}")
    elif not quiet_lab and topic and topic != "这个方向" and any(
        s.get("tool") == "get_direction_tree" and not s.get("error") for s in steps or []
    ):
        lines.insert(0, "组里还没有这个方向。")
    if lines:
        return "\n".join(lines)[:1800]
    ran = {s.get("tool") for s in steps or []}
    if ran & {"get_direction_frontier", "scholar_search", "scholar_topic_leaders", "sweep_screen", "get_paper_landscape", "web_search"}:
        return "这次公开库没对上这个方向。"
    return ""


def _compose_members(question: str, sources) -> str:
    """名册问题按角色字段写中文。没有导师就照实说，不用模型另编。"""
    rows = [s for s in (getattr(sources, "items", None) or []) if s.get("kind") == "member"]
    if not rows:
        return ""
    q = question or ""
    teachers = _teacher_rows(sources)

    def _name(row: dict) -> str:
        return (row.get("title") or "这位").strip()

    def _mark(row: dict) -> str:
        label = row.get("label") or ""
        return f" [{label}]" if label else ""

    if "导师" in q:
        if not teachers:
            cite = _mark(rows[0])
            line = f"名册里还没有人被标成导师。{cite}".strip()
        elif len(teachers) == 1:
            person = teachers[0]
            title = (person.get("member_title") or "").strip()
            extra = f"（{title}）" if title else ""
            line = f"名册里标成导师的是{_name(person)}{extra}。{_mark(person)}".strip()
        else:
            bits = "、".join(f"{_name(person)}{_mark(person)}" for person in teachers[:6])
            line = f"名册里标成导师的有{bits}。"
        intro = _pick_intro(sources)
        sections, structured = _intro_from_source(intro or {})
        address = sections.get("称呼") or ""
        if not structured:
            address = ((intro or {}).get("excerpt") or "").strip()
        if address and "导师" in address and all(_name(person) not in address for person in (teachers or rows[:1])):
            clip = re.sub(r"\s+", " ", address)[:80].rstrip("。")
            mark = _mark(intro or {})
            line = f"{line}组里另外写过：{clip}。{mark}".strip()
        return line
    shown = rows[:8]
    bits = []
    for row in shown:
        role = "导师" if row.get("role") == "teacher" else ("学生" if row.get("role") == "student" else "")
        title = (row.get("member_title") or "").strip()
        tail = "，".join(x for x in (role, title) if x)
        bits.append(f"{_name(row)}" + (f"（{tail}）" if tail else "") + _mark(row))
    return f"组里现在有 {len(rows)} 人。" + " ".join(bits)


def _pick_intro(sources):
    """同一篇组内说明优先用带分段的那条，避免被截断的检索片段盖住前提。"""
    rows = [s for s in (getattr(sources, "items", None) or []) if s.get("kind") == "lab_intro"]
    for row in rows:
        if any(str(row.get(key) or "").strip() for key in ("intro_address", "intro_custom", "intro_premise", "agent_voice")):
            return row
    return rows[0] if rows else None


def _intro_from_source(intro: dict) -> tuple[dict[str, str], bool]:
    from fengsuan.app.services.agent_standing import marked_sections

    sections: dict[str, str] = {}
    for key, field in (("称呼", "intro_address"), ("约定", "intro_custom"), ("前提", "intro_premise")):
        body = re.sub(r"\s+", " ", str(intro.get(field) or "")).strip()
        if body:
            sections[key] = body
    if sections:
        return sections, True
    parsed = marked_sections(intro.get("excerpt") or "")
    if parsed:
        return parsed, True
    return {}, False


def _compose_intro(sources, question: str = "") -> str:
    """组里写过的事按原文引用。问称呼就引称呼，缺了那一栏不拿别的栏来填。"""
    intro = _pick_intro(sources)
    if intro is None:
        return ""
    excerpt = re.sub(r"\s+", " ", (intro.get("excerpt") or "")).strip()
    sections, structured = _intro_from_source(intro)
    voice = re.sub(r"\s+", " ", str(intro.get("agent_voice") or "")).strip()
    if not excerpt and not sections and not voice:
        return ""
    q = question or ""
    if "怎么说话" in q:
        if not voice:
            return "还没写怎么说话。"
        body = voice
        body = re.sub(r"【(?:称呼|约定|前提)】", "", body)
        body = re.sub(r"\s+", " ", body).strip()[:180].rstrip("。")
        if not body:
            return "还没写怎么说话。"
        mark = f" [{intro['label']}]" if intro.get("label") else ""
        return f"组里写过：{body}。{mark}".strip()
    focus = ""
    if any(tok in q for tok in ("称呼", "对外怎么")):
        focus = "称呼"
    elif any(tok in q for tok in ("约定", "组会", "要记住")):
        focus = "约定"
    elif "前提" in q:
        focus = "前提"
    if focus and not sections.get(focus):
        if structured:
            return f"组里还没写{focus}。"
        body = excerpt
    elif focus:
        body = sections[focus]
    elif sections:
        body = " ".join(f"{key}：{sections[key]}" for key in ("称呼", "约定", "前提") if sections.get(key))
    else:
        body = excerpt
    body = re.sub(r"【(?:称呼|约定|前提)】", "", body)
    body = re.sub(r"\s+", " ", body).strip()[:180].rstrip("。")
    if not body:
        return ""
    mark = f" [{intro['label']}]" if intro.get("label") else ""
    return f"组里写过：{body}。{mark}".strip()


def _compose_counts(sources, steps=None) -> str:
    """篇数按库里的数字写中文。工具原文已经是结论，不再交给模型复述。"""
    row = next(
        (s for s in (getattr(sources, "items", None) or []) if str(s.get("id") or "").startswith("stats:")),
        None,
    )
    if row is not None:
        excerpt = re.sub(r"\s+", " ", (row.get("excerpt") or "")).strip()
        if excerpt:
            if not excerpt.endswith("。"):
                excerpt += "。"
            mark = f" [{row['label']}]" if row.get("label") else ""
            return f"{excerpt}{mark}".strip()
    for step in steps or []:
        if (step.get("tool") or "") != "get_library_stats":
            continue
        summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
        if summary and not summary.startswith("失败"):
            return summary if summary.endswith("。") else summary + "。"
    return ""


_READ_WORD = {
    "reading": "在读",
    "want_read": "想读",
    "read": "已读",
    "finished": "已读",
    "dropped": "放下",
}


def _compose_catalog(question: str, sources, steps=None) -> str:
    """文献目录按题名和在读状态列出来。空库用工具原文，不另编篇目。"""
    rows = []
    for row in getattr(sources, "items", None) or []:
        if not str(row.get("id") or "").startswith("litcat:"):
            continue
        title = (row.get("title") or "").strip()
        if not title or any(tok in title for tok in ("没有", "还没有")):
            continue
        rows.append(row)
    if not rows:
        for step in steps or []:
            if (step.get("tool") or "") != "get_lab_literature":
                continue
            summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
            if summary and not summary.startswith("失败") and not summary.startswith("文献目录"):
                return summary if summary.endswith("。") else summary + "。"
        return ""
    q = question or ""
    if "我还没读" in q:
        head = "你还没开读的先有这几篇。"
    elif "核心" in q:
        head = "标成核心的先有这几篇。"
    elif "pdf" in q.lower():
        head = "还没有 PDF 的先有这几篇。"
    else:
        head = "文献库里先列出这几篇。"
    bits = []
    for row in rows[:6]:
        year = row.get("year")
        year_s = str(year) if year else ""
        status = _READ_WORD.get(str(row.get("my_reading") or ""), "")
        tail = "，".join(x for x in (year_s, status) if x)
        mark = f" [{row['label']}]" if row.get("label") else ""
        bits.append(f"{row.get('title')}" + (f"（{tail}）" if tail else "") + mark)
    more = f" 还有 {len(rows) - 6} 篇在文献库里。" if len(rows) > 6 else ""
    return head + " ".join(bits) + more


def _compose_work(sources, steps=None) -> str:
    """谁在做，用成员简报原文。简报里没有的方向不要补。"""
    bits = []
    for row in getattr(sources, "items", None) or []:
        if row.get("kind") != "member" or not str(row.get("id") or "").startswith("member:"):
            continue
        line = re.sub(r"\s+", " ", (row.get("excerpt") or "")).strip()
        if not line:
            continue
        mark = f" [{row['label']}]" if row.get("label") else ""
        bits.append(f"{line}{mark}")
    if bits:
        text = " ".join(bits[:8])
        return text if text.endswith("。") else text + "。"
    for step in steps or []:
        if (step.get("tool") or "") != "get_member_work":
            continue
        summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
        if summary and not summary.startswith("失败"):
            return summary if summary.endswith("。") else summary + "。"
    return ""


def _compose_readers(sources, steps=None) -> str:
    """谁在读，按人和页码写。没有记录就用工具原文。"""
    rows = []
    for row in getattr(sources, "items", None) or []:
        if row.get("kind") != "reading":
            continue
        title = (row.get("title") or "").strip()
        if not title:
            continue
        rows.append(row)
    if not rows:
        for step in steps or []:
            if (step.get("tool") or "") != "get_paper_readers":
                continue
            summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
            if summary and not summary.startswith("失败"):
                return summary if summary.endswith("。") else summary + "。"
        return ""
    papers: list[str] = []
    for row in rows:
        paper = (row.get("paper_title") or "").strip()
        if paper and paper not in papers:
            papers.append(paper)
    bits = []
    for row in rows[:8]:
        title = (row.get("title") or "").strip()
        if any(tok in title for tok in ("没有", "还没")):
            bit = "问到的人还没有在读记录"
        else:
            try:
                page = int(row.get("page") or row.get("page_no") or 0)
            except (TypeError, ValueError):
                page = 0
            bit = f"{title}读到第 {page} 页" if page >= 2 else f"{title}在读"
            paper = (row.get("paper_title") or "").strip()
            if len(papers) > 1 and paper:
                bit = f"《{paper}》{bit}"
        mark = f" [{row['label']}]" if row.get("label") else ""
        bits.append(bit + mark)
    text = "，".join(bits)
    return text if text.endswith("。") else text + "。"


_TASK_WORD = {"todo": "待办", "doing": "进行中", "done": "已完成", "open": "待办"}


def _compose_tasks(question: str, sources, steps=None) -> str:
    """看板按题名、状态和指派人列出来。没有任务就用工具原文。"""
    rows = []
    for row in getattr(sources, "items", None) or []:
        sid = str(row.get("id") or "")
        title = (row.get("title") or "").strip()
        if not sid.startswith("task:") or sid.startswith("tasks:"):
            continue
        if not title or any(tok in title for tok in ("没有", "还没有")):
            continue
        rows.append(row)
    if not rows:
        for step in steps or []:
            if (step.get("tool") or "") != "get_lab_tasks":
                continue
            summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
            if summary and not summary.startswith("失败") and not summary.startswith("看板任务"):
                return summary if summary.endswith("。") else summary + "。"
        return ""
    q = question or ""
    head = "派给你的有这几条。" if any(tok in q for tok in ("我的任务", "派给我", "我的待办")) else "任务里有这几条。"
    bits = []
    for row in rows[:6]:
        status = _TASK_WORD.get(str(row.get("task_status") or ""), "")
        who = (row.get("assignee") or "").strip()
        due = (row.get("due_label") or "").replace("｜", "，")
        tail = "，".join(x for x in (status, who, due) if x)
        mark = f" [{row['label']}]" if row.get("label") else ""
        bits.append(f"{row.get('title')}" + (f"（{tail}）" if tail else "") + mark)
    more = f" 还有 {len(rows) - 6} 条任务。" if len(rows) > 6 else ""
    return head + " ".join(bits) + more


def _compose_activity(question: str, sources, steps=None) -> str:
    """最近的事按日志和动态原文列出来，并写明这不是排名。"""
    rows = []
    for row in getattr(sources, "items", None) or []:
        sid = str(row.get("id") or "")
        title = (row.get("title") or "").strip()
        if not (sid.startswith("wlog:") or sid.startswith("act:")):
            continue
        if not title or any(tok in title for tok in ("没有", "还没有")):
            continue
        rows.append(row)
    if not rows:
        for step in steps or []:
            if (step.get("tool") or "") != "get_lab_activity":
                continue
            summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
            if summary and not summary.startswith("失败") and not summary.startswith("周志") and not summary.startswith("最近动态"):
                return summary if summary.endswith("。") else summary + "。"
        return ""
    q = question or ""
    head = "你最近记下的有这几条，不是排名。" if any(tok in q for tok in ("我的周志", "我的日志", "我写的")) else "最近记下的有这几条，不是排名。"
    bits = []
    for row in rows[:6]:
        title = row.get("title") or "这条"
        actor = (row.get("actor") or "").strip()
        tail = f"（{actor}）" if actor and actor not in title else ""
        mark = f" [{row['label']}]" if row.get("label") else ""
        bits.append(f"{title}{tail}{mark}")
    more = f" 还有 {len(rows) - 6} 条。" if len(rows) > 6 else ""
    return head + " ".join(bits) + more


def _compose_glossary(question: str, sources, steps=None) -> str:
    """译名按术语表原文。表里没有的词就说还没定，不另编译法。"""
    rows = []
    for row in getattr(sources, "items", None) or []:
        sid = str(row.get("id") or "")
        title = (row.get("title") or "").strip()
        if not sid.startswith("term:"):
            continue
        if not title or any(tok in title for tok in ("没有", "还没有")):
            continue
        rows.append(row)
    if not rows:
        for step in steps or []:
            if (step.get("tool") or "") != "get_lab_glossary":
                continue
            summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
            if summary and not summary.startswith("失败") and not summary.startswith("术语 "):
                return summary if summary.endswith("。") else summary + "。"
        return ""
    q = question or ""
    named = [row for row in rows if (row.get("title") or "") and (row.get("title") or "") in q]
    show = (named or rows)[:6]

    def _bit(row: dict) -> str:
        if row.get("locked"):
            tail = "锁定不译"
        elif (row.get("term_tgt") or "").strip():
            tail = f"译成{row.get('term_tgt')}"
        else:
            tail = "还没定译"
        mark = f" [{row['label']}]" if row.get("label") else ""
        return f"「{row.get('title')}」{tail}。{mark}".strip()

    if len(show) == 1 and (named or any(tok in q for tok in ("怎么译", "这个词", "译成"))):
        return _bit(show[0])
    body = " ".join(_bit(row) for row in show)
    more = f" 还有 {len(rows) - len(show)} 条在术语表里。" if len(rows) > len(show) else ""
    return f"术语表里先有这几条。{body}{more}"


def _compose_meetings(sources, steps=None) -> str:
    """组会按时间、纪要和已入账的行动列出来。没有会就照实说。"""
    rows = []
    for row in getattr(sources, "items", None) or []:
        sid = str(row.get("id") or "")
        title = (row.get("title") or "").strip()
        if not sid.startswith("meet:") or sid.startswith("meetings:"):
            continue
        if not title or any(tok in title for tok in ("没有", "还没有")):
            continue
        rows.append(row)
    if not rows:
        for step in steps or []:
            if (step.get("tool") or "") != "get_recent_meetings":
                continue
            summary = re.sub(r"\s+", " ", (step.get("result_summary") or "")).strip()
            if summary and not summary.startswith("失败") and not summary.startswith("最近组会"):
                return summary if summary.endswith("。") else summary + "。"
        return ""
    bits = []
    for row in rows[:4]:
        when = (row.get("held_at") or "").strip() or "未定时间"
        minutes = "有纪要" if row.get("has_minutes") else "还没有纪要"
        actions = (row.get("actions") or "").strip()
        act = f"，行动：{actions}" if actions else "，还没有入账行动"
        mark = f" [{row['label']}]" if row.get("label") else ""
        bits.append(f"{row.get('title')}（{when}，{minutes}{act}）{mark}")
    more = f" 还有 {len(rows) - 4} 次。" if len(rows) > 4 else ""
    return "最近的组会是这几次。" + " ".join(bits) + more


def _compose_directions(sources) -> str:
    lines = []
    seen: set[str] = set()
    for row in getattr(sources, "items", None) or []:
        sid = str(row.get("id") or "")
        if not sid.startswith("dir:"):
            continue
        name = (row.get("title") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        excerpt = re.sub(r"\s+", " ", (row.get("excerpt") or "")).strip()
        if name and excerpt.startswith(name):
            excerpt = excerpt[len(name) :].lstrip("：:，,。 ")
        owner = ""
        found = re.search(r"负责人[:：]\s*([^关键词子课题文献｜|]{1,24})", excerpt)
        if "关键词" in excerpt or "文献 " in excerpt or "子课题" in excerpt:
            excerpt = ""
        if found:
            owner = found.group(1).strip(" ，。")
        cjk = len(_CJK.findall(excerpt))
        latin = len(_LATIN.findall(excerpt))
        if excerpt and cjk < 4 and latin >= 1:
            detail = f"组里放了这篇：{excerpt[:80]}"
        else:
            detail = excerpt[:80]
        label = row.get("label") or ""
        mark = f" [{label}]" if label else ""
        bit = name
        if detail:
            bit = f"{name}：{detail}"
        if owner:
            bit = f"{bit}。在做的人是{owner}"
        lines.append(f"{bit}{mark}")
    if not lines:
        return ""
    if len(lines) == 1:
        return f"组里现在主要在做{lines[0]}。"
    body = "\n".join(f"{i}. {line}" for i, line in enumerate(lines[:6], 1))
    return f"组里现在主要做这几件事。\n{body}"


def _compose_ask_back(pack: dict, sources) -> str:
    """同名不同人：一句话说明在哪找到的，人选交给下面的卡片，不再把同一份名单写两遍。"""
    options = pack.get("options") or []
    name = pack.get("name") or "这个名字"
    n = len(options)
    note = str(pack.get("note") or "").strip()
    if pack.get("kind") == "clarify":
        question = str(pack.get("question") or "").strip()
        if question:
            return question if question.endswith(("？", "?")) else question + "？"
        return "再说具体一点，我再查。"
    if pack.get("where") == "web":
        head = f"网上有 {n} 位名字接近「{name}」的学者。他们还不是这个组的成员，不用先拉进组。"
    else:
        head = f"论文署名里有 {n} 位接近「{name}」的作者。这是库里的作者，不一定是组里的成员。"
    if note:
        head = f"{head}{note}"
    # D-HONEST：这句也要能追溯到出处，标出候选来源的编号。
    cited = [str(s.get("label")) for s in getattr(sources, "items", []) or [] if s.get("label")][:3]
    tail = "".join(f"[{lab}]" for lab in cited if lab)
    return head + "是哪一位？" + (tail + "选一位，再看学术情况。" if tail else "选一位，再看学术情况。")


def _zh_from_sources(sources) -> str:
    lines = ["根据查到的材料："]
    for row in (getattr(sources, "items", None) or [])[:4]:
        title = row.get("title") or "材料"
        excerpt = (row.get("excerpt") or "").replace("\n", " ")[:80]
        label = row.get("label") or ""
        lines.append(f"{title}：{excerpt} {label}".strip())
    return "\n".join(lines) if len(lines) > 1 else ""


def stub_query(question: str) -> str:
    """stub 路径的检索词：取最长实词，而不是把整句问题当关键词。

    真模型该自己抽关键词，所以这条只影响 stub 与评测。
    """
    latin = _LATIN.findall(question or "")
    if latin:
        return max(latin, key=len)
    cjk = _CJK.findall(question or "")
    return max(cjk, key=len) if cjk else (question or "")[:40]


def _source_key(src: dict) -> str:
    return str(src.get("id") or "")


def _infer_source_href(src: dict, lab_id: str) -> str:
    if src.get("href"):
        return str(src.get("href") or "")
    item = src.get("item_id") or src.get("literature_item_id")
    page = src.get("page") or src.get("page_no")
    if item:
        qs = "?panel=tutor" + (f"&page={page}" if page else "")
        return f"/labs/{lab_id}/literature/{item}{qs}"
    kind = src.get("kind") or ""
    sid = str(src.get("id") or "")
    tail = sid.split(":", 1)[1] if ":" in sid else ""
    if kind == "research_run" and tail:
        return f"/labs/{lab_id}/runs/{tail}"
    if kind == "reproduce_run" and tail:
        return f"/labs/{lab_id}/reproduce/{tail}"
    if kind == "dataset" and tail:
        return f"/datasets/{tail}"
    if kind == "glossary":
        return f"/labs/{lab_id}/kb?tab=terms"
    if kind == "task" and tail:
        return f"/labs/{lab_id}/tasks?open={tail}"
    return ""


_GOAL_CHIPS = {
    "入门": ["下一步读哪一篇", "有没有更短的综述"],
    "找基线": ["这些方法的代码在哪", "指标是在哪个数据集上"],
    "写相关工作": ["按主题再分一组", "把上面这几篇收进组里"],
    "找数据集或代码": ["代码仓库是哪一个", "数据集公开了吗"],
    "复现": ["缺哪些实验细节", "有没有官方代码"],
    "选题": ["还有哪些没被做的点", "先读哪三篇"],
    "跟最新": ["再给近两年的代表作", "有没有今年的综述"],
    "查人": ["这位还写过什么", "同方向还有谁"],
}


def _research_followups(question: str, turn: dict | None, qtype: str) -> list[str]:
    """组外检索答完后，猜下一步，点回同一问答栏。"""
    from fengsuan.app.services.agent.parse import infer_goal

    asked = (question or "").strip()
    turn = turn or {}
    topic = str(turn.get("topic_zh") or "").strip()[:16]
    person = str(turn.get("person_name") or "").strip()[:12]
    wants = set(turn.get("wants") or [])
    out: list[str] = []

    def _add(q: str) -> None:
        text = (q or "").strip()
        if not text or text == asked or text in out:
            return
        out.append(text[:40])

    for bit in _GOAL_CHIPS.get(infer_goal(asked, turn), []):
        _add(bit)
        if len(out) >= 3:
            return out
    for bit in turn.get("inferred") or []:
        _add(str(bit))
        if len(out) >= 3:
            return out
    if qtype in {"frontier", "field_people", "sweep"}:
        label = f"「{topic}」" if topic else "这个方向"
        if "classic" not in wants:
            _add(f"{label}再给几篇奠基经典")
        if "people" not in wants and qtype != "field_people":
            _add(f"{label}近五年代表作者")
        _add("把上面这几篇收进组里")
        if "reviews" not in wants:
            _add(f"{label}有哪些综述")
    elif qtype == "landscape":
        _add("下一批引用")
        _add("把引用它的收进组里")
        _add("这篇作者还写过什么")
    elif qtype == "web_person":
        who = person or "这位"
        _add(f"{who}被引最高的论文")
        _add(f"{who}近五年的论文")
        _add("下一批")
    return out[:3]


def _followups(
    question: str,
    citations: list[dict],
    ungrounded: list[str],
    sources: list[dict] | None = None,
    turn: dict | None = None,
    qtype: str = "",
    paper_count: int = 0,
) -> list[str]:
    """从已查到来源长出下一问，只填现有问答栏，不另开聊天。"""
    batch = any(tok in (question or "") for tok in ("这几篇", "这批", "上面", "这些"))
    if qtype == "import" and batch and "再收下一批" not in (question or "") and int(paper_count or 0) > 10:
        return ["再收下一批"]
    habit_chip = []
    if str((turn or {}).get("habit_note") or "") and "这次不按习惯" not in (question or ""):
        habit_chip = ["这次不按习惯"]
    if qtype in {"web_person", "frontier", "field_people", "sweep", "landscape"}:
        guessed = _research_followups(question, turn, qtype)
        if guessed:
            return (habit_chip + guessed)[:3]
    asked = (question or "").strip()
    out: list[str] = []
    rows = list(citations or [])
    seen = {str(c.get("source_id") or c.get("id") or "") for c in rows}
    for s in sources or []:
        key = str(s.get("id") or "")
        if key and key not in seen:
            rows.append(s)
            seen.add(key)

    def _add(q: str) -> None:
        text = (q or "").strip()
        if not text or text == asked or text in out:
            return
        out.append(text[:40])

    for c in rows[:6]:
        kind = c.get("kind") or ""
        title = (c.get("title") or "").strip()[:28]
        if any(tok in title for tok in ("没有", "还没有", "对不上")):
            continue
        page = c.get("page") or c.get("page_no")
        sid = str(c.get("id") or c.get("source_id") or "")
        if sid.startswith("stats:"):
            _add("按方向各有多少篇？" if "年份" in asked else "组里论文按年份分布？")
            _add("组里有哪些文献？")
            continue
        if kind in {"page", "page_translation"} and page:
            _add(f"第{page}页还讲了什么？")
        elif kind == "literature" and title and not any(tok in asked for tok in ("领域", "方向", "好的文章", "有什么文章", "有哪些论文", "有什么论文")):
            short = re.split(r"[:：]", title, maxsplit=1)[0].strip()[:12] or title[:12]
            _add(f"谁在读《{short}》？")
        elif kind == "biblio" and title:
            _add(f"{title} 被谁引用？")
        elif kind == "dataset" and title:
            _add(f"{title} 被哪次作业用了？")
        elif kind in {"research_run", "reproduce_run", "queued"} and title:
            _add(f"{title} 卡在哪？")
        elif kind == "glossary" and title and "没有" not in title:
            _add(f"{title} 锁定不译吗？")
        elif kind == "method_card":
            _add("哪些主张没依据？")
        elif kind == "task" and title:
            _add(f"{title} 到期了吗？")
        elif kind == "milestone" and title:
            _add(f"{title} 到期了吗？")
        elif kind == "artifact" and title:
            _add(f"{title} 里写了什么？")
        elif kind == "lab_file" and title:
            _add(f"{title} 里写了什么？")
        elif kind == "note":
            _add("我还记过什么？")
        elif kind == "reproduce":
            _add("声称和实测差在哪？")
        elif kind == "reading":
            _add("这篇我读到哪了？")
        elif kind == "author" and title and "没有" not in title:
            _add(f"{title} 还写过什么？")
        elif kind == "meeting" and title and "没有" not in title:
            _add("这次组会定了什么？" if "定了" not in asked else "下次什么时候开会？")
        elif kind in {"work_log", "activity"}:
            _add("我的任务有哪些？" if "任务" not in asked else "组里最近做了什么？")
            _add("最近有组会吗？")
        elif kind == "member":
            if c.get("role") == "teacher":
                _add("这位导师还负责哪个方向？")
            elif title:
                _add(f"{title} 在做什么？")
            if any(tok in asked for tok in ("谁在做", "在做什么", "成员在做")):
                _add("最近做了什么？")
        elif kind == "lab_intro":
            _add("组里还写了什么？")
        elif kind == "related" and title:
            _add(f"《{title}》引了哪些？")
    pageish = any((c.get("kind") in {"page", "page_translation", "kb_chunk"}) for c in rows)
    if ungrounded and pageish:
        _add("这段依据在哪一页？")
    return (habit_chip + out)[:3]


def _empty_next_pack(data: dict | None) -> dict | None:
    """工具算好的空态下一步，原样交给问答栏点回现有页，不另开产品。"""
    nxt = (data or {}).get("empty_next")
    if not isinstance(nxt, dict):
        return None
    steps = [
        {
            "key": (s.get("key") or "")[:20],
            "label": (s.get("label") or "")[:40],
            "href": s.get("href") or "",
        }
        for s in (nxt.get("steps") or [])[:4]
        if isinstance(s, dict) and s.get("href")
    ]
    title = (nxt.get("title") or "")[:80]
    text = (nxt.get("text") or "")[:240]
    if not (title or text or steps):
        return None
    return {"title": title, "text": text, "steps": steps}


class _Sources:
    """给工具返回的片段编号，并保留 `source_id` 供接地追溯。"""

    def __init__(self, cap: int = 12, lab_id: str = "") -> None:
        self.cap = cap
        self.lab_id = lab_id
        self.items: list[dict] = []
        self._seen: set[str] = set()

    def add(self, src: dict, *, force: bool = False) -> str:
        key = _source_key(src)
        if not key or key in self._seen:
            return ""
        if len(self.items) >= self.cap:
            if not force or not self.items:
                return ""
            dropped = self.items.pop()
            self._seen.discard(_source_key(dropped))
        self._seen.add(key)
        label = f"S{len(self.items) + 1}"
        kind = kind_label(src.get("kind") or "")
        href = src.get("href") or _infer_source_href(src, self.lab_id)
        extra = {}
        if src.get("relation"):
            from fengsuan.app.services.agent.routing import relation_label

            extra["relation_label"] = relation_label(src["relation"])
        self.items.append({**src, "label": label, "kind_label": kind, "href": href, **extra})
        return label

    def by_label(self, label: str) -> dict | None:
        for s in self.items:
            if s.get("label") == label:
                return s
        return None

    def prompt_block(self, limit_each: int = 1400) -> str:
        lines = []
        for s in self.items:
            head = s.get("title") or ""
            page = f" p.{s['page']}" if s.get("page") else ""
            kind = f" ·{s.get('kind_label')}" if s.get("kind_label") else ""
            lines.append(f"[{s['label']}] {head}{page}{kind}\n{(s.get('excerpt') or '')[:limit_each]}")
        return "\n\n".join(lines)


def _mark_used(answer: str, sources) -> None:
    cited = set(re.findall(r"\[S(\d+)\]", answer or ""))
    reasons = {
        "direction": "和这个问题对不上",
        "member": "不是这次要找的人",
        "web": "网页里对不上",
        "web_page": "网页里对不上",
        "scholar": "题目或署名对不上",
        "external": "题目或署名对不上",
        "literature": "和这个问题对不上",
    }
    for src in getattr(sources, "items", None) or []:
        label = str(src.get("label") or "")
        num = label[1:] if label.startswith("S") else ""
        if num and num in cited:
            src["used"] = True
            src.pop("drop_reason", None)
        else:
            src["used"] = False
            if not src.get("drop_reason"):
                src["drop_reason"] = reasons.get(src.get("kind") or "", "这次没有用到")


def should_recover_evidence(ratio, ungrounded, steps, *, scope: str = "", item_id: str = "", stub: bool = False) -> bool:
    """依据不够、又还没在这一篇里查过时，先再查一次，不把弱句直接交出去。"""
    if stub or scope != "paper" or not item_id or not ungrounded:
        return False
    try:
        if float(ratio) >= 0.8:
            return False
    except (TypeError, ValueError):
        return False
    return not any(str((row or {}).get("tool") or "") == "search_in_paper" for row in steps or [])


def ground_by_source_id(answer: str, sources: _Sources) -> dict:
    """接地前移：逐句检查有没有指回某个 `source_id`。

    比事后正则子串匹配可靠，且天然产出可点的引用。
    """
    text = (answer or "").strip()
    sentences = [s for s in re.split(r"(?<=[。！？!?\n])\s*", text) if s.strip()]
    used: list[str] = []
    ungrounded: list[str] = []
    for sent in sentences:
        labels = _CITE.findall(sent)
        hits = [f"S{n}" for n in labels if sources.by_label(f"S{n}")]
        if hits:
            used.extend(hits)
            continue
        if len(sent.strip()) >= 12 and not sent.strip().startswith(("下一步", "建议", "我可以", "要不要", "我按", "这次没有核上", "排除了", "可以补")):
            ungrounded.append(sent.strip()[:120])
    cited = []
    for label in dict.fromkeys(used):
        src = sources.by_label(label)
        if src:
            cited.append(
                {
                    "label": label,
                    "source_id": src.get("id"),
                    "kind": src.get("kind"),
                    "kind_label": kind_label(src.get("kind") or ""),
                    "title": src.get("title"),
                    "page": src.get("page"),
                    "page_no": src.get("page"),
                    "href": src.get("href") or _infer_source_href(src, ""),
                    "item_id": src.get("item_id") or "",
                    "literature_item_id": src.get("item_id") or "",
                    "role": src.get("role") or "",
                    "member_title": src.get("member_title") or "",
                    "member_dirs": src.get("member_dirs") or "",
                    "my_reading": src.get("my_reading") or "",
                    "year": src.get("year"),
                    "task_status": src.get("task_status") or "",
                    "assignee": src.get("assignee") or "",
                    "due_label": src.get("due_label") or "",
                    "actor": src.get("actor") or "",
                    "term_tgt": src.get("term_tgt") or "",
                    "locked": bool(src.get("locked")),
                    "held_at": src.get("held_at") or "",
                    "has_minutes": bool(src.get("has_minutes")),
                }
            )
    return {
        "answer": text,
        "citations": cited,
        "ungrounded": ungrounded[:6],
        "grounded_ratio": round(1.0 - (len(ungrounded) / max(1, len(sentences))), 3),
    }


def _tool_messages(tool_call_id: str, name: str, result: ToolResult, *, excerpt_n: int = 600) -> dict:
    payload = {
        "ok": not result.error,
        "summary": result.summary(400),
        "empty": bool((result.data or {}).get("empty")) or (not result.sources and not result.error),
        "sources": [
            {
                "source_id": s.get("id"),
                "label": s.get("label"),
                "kind": s.get("kind"),
                "kind_label": kind_label(s.get("kind") or ""),
                "title": s.get("title"),
                "page": s.get("page"),
                "excerpt": (s.get("excerpt") or "")[:excerpt_n],
            }
            for s in result.sources[:8]
        ],
        "data": {k: v for k, v in (result.data or {}).items() if k not in {"card", "preview"}},
    }
    if result.error:
        payload["error"] = result.error
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": json.dumps(payload, ensure_ascii=False)[:4000],
    }


def _append_observed(messages: list[dict], step_no: int, name: str, args: dict, result: ToolResult) -> None:
    """把已跑过的种子/回放写进同一轮消息，模型不必再调一次。"""
    cid = f"seed_{step_no}_{name}"[:64]
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
                }
            ],
        }
    )
    messages.append(_tool_messages(cid, name, result))


def _steer_line(scope: str, question: str) -> str:
    names = steer_tools(scope, question)
    if not names:
        return ""
    return "本题更可能先用：" + "、".join(names) + "。已经取到的工具结果不要重调。"


def _run_tool(ctx: ToolContext, name: str, args: dict, budget: Budget) -> tuple[ToolResult, int]:
    spec = get_tool(name)
    started = time.monotonic()
    if spec is None:
        return ToolResult(error=f"没有这个工具：{name}"), 0
    if ctx.scope not in spec.scopes:
        return ToolResult(error=f"{name} 在当前场景不可用"), 0
    try:
        out = spec.fn(ctx, args or {})
    except ClassifiedHTTPException as exc:
        out = ToolResult(error=str(exc.detail)[:200])
    except Exception as exc:
        out = ToolResult(error=f"{type(exc).__name__}: {str(exc)[:160]}")
    if out.text:
        out.text = out.text[: budget.max_tool_chars]
    return out, int((time.monotonic() - started) * 1000)


def _system_prompt(scope: str, extra: str = "") -> str:
    parts = [SYSTEM_BASE, SCOPE_HINT.get(scope, "")]
    if extra:
        parts.append(extra)
    return "\n".join(p for p in parts if p)


def run_agent(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    scope: str,
    question: str,
    feature: str,
    item_id: str = "",
    manuscript_id: str = "",
    page_no: int | None = None,
    history: list[dict] | None = None,
    session_id: str = "",
    system_extra: str = "",
    seed_tools: list[tuple[str, dict]] | None = None,
    confirm: bool = False,
    max_steps: int | None = None,
    direction_id: str = "",
    excerpt: str = "",
    confirm_tools: list | None = None,
    default_direction_id: str = "",
) -> Iterator[dict]:
    """多轮 tool-calling 循环，逐步产出事件。

    事件形状：
    - `{"step": n, "tool": name, "args": {...}}`      工具将要执行
    - `{"step": n, "tool": name, "result_summary": s, "source_ids": [...]}`  工具结果
    - `{"delta": "..."}`                               回答增量
    - `{"done": True, "answer": ..., "citations": [...], "steps": [...]}`    收尾
    """
    from fengsuan.app.services.llm import (
        assert_lab_quota,
        complete_with_tools,
        iter_chat_deltas,
        ledger_usage,
        llm_stub_enabled,
    )

    scope = (scope or "paper").strip()
    sid = (session_id or "").strip() or uuid.uuid4().hex
    budget = budget_for(scope)
    if max_steps:
        budget = Budget(
            max_steps=max_steps,
            max_tool_calls=budget.max_tool_calls,
            tool_timeout_s=budget.tool_timeout_s,
            max_tool_chars=budget.max_tool_chars,
            max_sources=budget.max_sources,
        )
    assert_lab_quota(db, lab_id, feature)

    ctx = ToolContext(
        db=db,
        user_id=user_id,
        lab_id=lab_id,
        scope=scope,
        item_id=item_id,
        manuscript_id=manuscript_id,
        confirm=confirm,
        direction_id=(direction_id or "")[:64],
    )
    if default_direction_id:
        ctx.extra["default_direction_id"] = default_direction_id[:64]
    if question:
        ctx.extra["question"] = (question or "")[:800]
    if page_no:
        ctx.extra["page_no"] = int(page_no)
    if excerpt:
        ctx.extra["excerpt"] = excerpt[:4000]
    requested = normalize_confirm_tools(scope, confirm_tools) if confirm else []
    replay: list[tuple[str, dict]] = []
    if confirm:
        from fengsuan.app.services.agent.trace import session_pending_replays

        pending = session_pending_replays(db, session_id=sid, user_id=user_id, lab_id=lab_id)
        if requested:
            allow = {name for name, _ in requested}
            replay = [(name, args) for name, args in pending if name in allow]
        else:
            replay = pending
    sources = _Sources(cap=budget.max_sources, lab_id=lab_id)
    steps: list[dict] = []
    pending_confirm: list[dict] = []
    prompt_tokens = 0
    completion_tokens = 0
    model_used = "stub"
    degraded = ""
    fail_streak = 0
    memory: dict = {}
    turn: dict = {}
    if not confirm:
        try:
            from fengsuan.app.services.agent.trace import session_memory

            memory = session_memory(db, session_id=sid, user_id=user_id, lab_id=lab_id)
        except Exception:
            memory = {}
        if not llm_stub_enabled() and (question or "").strip():
            understanding = {
                "step": 0,
                "tool": "understand",
                "label": "理解问题",
                "args": {},
                "result_summary": "正在理解这个问题",
                "source_ids": [],
                "latency_ms": 0,
                "error": "",
            }
            steps.append(understanding)
            yield understanding
            try:
                from fengsuan.app.services.agent.parse import understand_turn

                turn = understand_turn(
                    db,
                    user_id,
                    lab_id,
                    question,
                    history=history,
                    memory=memory,
                    session_id=sid,
                )
            except Exception:
                turn = {}
            if turn.get("summary"):
                understood = {
                    "step": 0,
                    "tool": "understand",
                    "label": f"理解问题：{str(turn.get('summary'))[:40]}",
                    "args": {"task": turn.get("task") or ""},
                    "result_summary": str(turn.get("summary"))[:200],
                    "source_ids": [],
                    "latency_ms": 0,
                    "error": "",
                }
                steps.append(understood)
                yield understood
            from fengsuan.app.services.agent.parse import search_plan_text

            plan_text = search_plan_text(turn)
            if plan_text:
                planned = {
                    "step": 0,
                    "tool": "plan",
                    "label": "检索计划",
                    "args": {},
                    "result_summary": plan_text[:500],
                    "source_ids": [],
                    "latency_ms": 0,
                    "error": "",
                }
                steps.append(planned)
                yield planned
    from fengsuan.app.services.agent.parse import asks_outside_field

    if (
        not confirm
        and "这次不按习惯" not in (question or "")
        and (turn.get("scope") == "outside" or asks_outside_field(question))
    ):
        try:
            from fengsuan.app.services.agent.habits import user_habits

            habits = user_habits(db, user_id, lab_id)
        except Exception:
            habits = {}
        if habits.get("years") and not turn.get("years"):
            turn["years"] = habits["years"]
        if habits.get("note"):
            turn["habit_note"] = habits["note"]
    ctx.extra["memory"] = memory
    ctx.extra["turn"] = turn
    ctx.extra["session_id"] = sid
    qtype = classify_question(question, turn, memory)
    if qtype == "sweep":
        budget = Budget(
            max_steps=max(budget.max_steps, 12),
            max_tool_calls=max(budget.max_tool_calls, 16),
            tool_timeout_s=budget.tool_timeout_s,
            max_tool_chars=budget.max_tool_chars,
            max_sources=40,
        )
    elif qtype in {"web_person", "field_people", "frontier", "landscape", "import"}:
        budget = Budget(
            max_steps=max(budget.max_steps, 10),
            max_tool_calls=max(budget.max_tool_calls, 14),
            tool_timeout_s=budget.tool_timeout_s,
            max_tool_chars=budget.max_tool_chars,
            max_sources=budget.max_sources,
        )
    ask_back: dict | None = None

    def _use(result: ToolResult) -> list[str]:
        ids: list[str] = []
        for src in result.sources:
            label = sources.add(src)
            if label:
                src["label"] = label
                ids.append(str(src.get("id") or ""))
        return ids

    def _do(step_no: int, name: str, args: dict) -> ToolResult:
        yield_args = {k: v for k, v in (args or {}).items() if k != "text"}
        result, ms = _run_tool(ctx, name, args, budget)
        ids = _use(result)
        if (result.data or {}).get("pending_confirm"):
            data = result.data or {}
            pending_confirm.append(
                {
                    "tool": name,
                    "label": tool_label(name),
                    "args": yield_args,
                    "preview": data,
                    "preview_text": str(data.get("preview") or data.get("question") or "")[:180],
                }
            )
        nonlocal fail_streak, degraded, ask_back
        if name in {"find_author", "get_author_works", "scholar_find_person", "get_member_identity", "sweep_plan"}:
            pack = (result.data or {}).get("ask_back")
            if isinstance(pack, dict):
                ask_back = pack
            elif not result.error and not (result.data or {}).get("miss"):
                ask_back = None
        missed = bool(result.error) or bool((result.data or {}).get("miss"))
        if missed:
            fail_streak += 1
            if fail_streak >= 2:
                degraded = degraded or "tool_failures"
        else:
            fail_streak = 0
        row = {
            "step": step_no,
            "tool": name,
            "label": str((result.data or {}).get("phase_label") or "") or tool_label(name),
            "args": yield_args,
            "result_summary": result.summary(),
            "source_ids": ids,
            "latency_ms": ms,
            "error": result.error,
            "empty": bool((result.data or {}).get("empty")),
        }
        nxt = _empty_next_pack(result.data)
        if nxt:
            row["empty_next"] = nxt
        remembered = (result.data or {}).get("remember")
        if isinstance(remembered, dict) and remembered:
            trace.record(
                db,
                lab_id=lab_id,
                user_id=user_id,
                session_id=sid,
                feature=feature,
                scope=scope,
                step=step_no,
                kind="memory",
                tool=name,
                args=remembered,
            )
        steps.append(row)
        trace.record(
            db,
            lab_id=lab_id,
            user_id=user_id,
            session_id=sid,
            feature=feature,
            scope=scope,
            step=step_no,
            kind="tool",
            tool=name,
            args=yield_args,
            result_summary=(
                (result.summary(400) + (" pending_confirm" if (result.data or {}).get("pending_confirm") else ""))[:2000]
            ),
            source_ids=ids,
            latency_ms=ms,
            error=result.error,
        )
        return result

    step = 0
    tool_calls = 0
    observed: list[tuple[int, str, dict, ToolResult]] = []

    # 种子工具：调用方指定的，再加上这类问题固定要查的那几步。
    from fengsuan.app.services.agent.understand import followups, recipe_for

    queue: list[tuple[str, dict]] = list(seed_tools or [])
    queued: set[str] = {_tool_seed_key(name, args) for name, args in queue}
    seen_seed: set[str] = set()
    # 确认这一轮只回放用户点过的写入，不再按原问题重查一遍。
    if not ctx.confirm:
        for name, args in recipe_for(question, memory, turn):
            key = _tool_seed_key(name, args)
            if key in queued:
                continue
            queued.add(key)
            queue.append((name, args))
    while queue:
        if budget.exhausted(step=step, tool_calls=tool_calls) or degraded == "tool_failures":
            break
        name, args = queue.pop(0)
        key = _tool_seed_key(name, args)
        if key in seen_seed:
            continue
        seen_seed.add(key)
        step += 1
        tool_calls += 1
        result = _do(step, name, args)
        observed.append((step, name, args, result))
        yield {
            "step": step,
            "tool": name,
            "label": tool_label(name),
            "args": args,
            "result_summary": result.summary(),
            "source_ids": [s.get("id") for s in result.sources],
        }
        if (result.data or {}).get("miss") and name in {"find_author", "get_author_works"}:
            payload = public_person_args(question, args, turn)
            person_key = _tool_seed_key("scholar_find_person", payload)
            if payload and person_key not in seen_seed and person_key not in queued:
                queued.add(person_key)
                queue.append(("scholar_find_person", payload))
        if not (result.data or {}).get("ask_back"):
            for nxt in followups(name, result.data):
                nxt_key = _tool_seed_key(nxt[0], nxt[1])
                if nxt_key in seen_seed or nxt_key in queued:
                    continue
                queued.add(nxt_key)
                queue.append(nxt)

    for name, args in replay:
        if budget.exhausted(step=step, tool_calls=tool_calls):
            break
        step += 1
        tool_calls += 1
        result = _do(step, name, args)
        observed.append((step, name, args, result))
        yield {
            "step": step,
            "tool": name,
            "label": tool_label(name),
            "args": args,
            "result_summary": result.summary(),
            "source_ids": [s.get("id") for s in result.sources],
        }
        if degraded == "tool_failures":
            break

    if scope in {"lab", "onboarding"} and _is_roster_question(question):
        if not any(name == "get_lab_members" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_lab_members", {})
                observed.append((step, "get_lab_members", {}, result))
                yield {
                    "step": step,
                    "tool": "get_lab_members",
                    "label": tool_label("get_lab_members"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_direction_question(question):
        if not any(name == "get_direction_tree" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_direction_tree", {})
                observed.append((step, "get_direction_tree", {}, result))
                yield {
                    "step": step,
                    "tool": "get_direction_tree",
                    "label": tool_label("get_direction_tree"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_count_question(question):
        if not any(name == "get_library_stats" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                stats_dir = (direction_id or default_direction_id or "")[:64]
                stats_args = {"direction_id": stats_dir} if stats_dir else {}
                result = _do(step, "get_library_stats", stats_args)
                observed.append((step, "get_library_stats", stats_args, result))
                yield {
                    "step": step,
                    "tool": "get_library_stats",
                    "label": tool_label("get_library_stats"),
                    "args": stats_args,
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_catalog_question(question):
        if not any(name == "get_lab_literature" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                cat_dir = (direction_id or default_direction_id or "")[:64]
                cat_args = {"direction_id": cat_dir} if cat_dir else {}
                result = _do(step, "get_lab_literature", cat_args)
                observed.append((step, "get_lab_literature", cat_args, result))
                yield {
                    "step": step,
                    "tool": "get_lab_literature",
                    "label": tool_label("get_lab_literature"),
                    "args": cat_args,
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_work_question(question):
        if not any(name == "get_member_work" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_member_work", {})
                observed.append((step, "get_member_work", {}, result))
                yield {
                    "step": step,
                    "tool": "get_member_work",
                    "label": tool_label("get_member_work"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding", "paper"} and _is_readers_question(question):
        if not any(name == "get_paper_readers" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_paper_readers", {})
                observed.append((step, "get_paper_readers", {}, result))
                yield {
                    "step": step,
                    "tool": "get_paper_readers",
                    "label": tool_label("get_paper_readers"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_task_question(question):
        if not any(name == "get_lab_tasks" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_lab_tasks", {})
                observed.append((step, "get_lab_tasks", {}, result))
                yield {
                    "step": step,
                    "tool": "get_lab_tasks",
                    "label": tool_label("get_lab_tasks"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_activity_question(question):
        if not any(name == "get_lab_activity" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_lab_activity", {})
                observed.append((step, "get_lab_activity", {}, result))
                yield {
                    "step": step,
                    "tool": "get_lab_activity",
                    "label": tool_label("get_lab_activity"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_glossary_question(question):
        if not any(name == "get_lab_glossary" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_lab_glossary", {})
                observed.append((step, "get_lab_glossary", {}, result))
                yield {
                    "step": step,
                    "tool": "get_lab_glossary",
                    "label": tool_label("get_lab_glossary"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
    if scope in {"lab", "onboarding"} and _is_meeting_question(question):
        if not any(name == "get_recent_meetings" for _step, name, _args, _result in observed):
            if not budget.exhausted(step=step, tool_calls=tool_calls):
                step += 1
                tool_calls += 1
                result = _do(step, "get_recent_meetings", {})
                observed.append((step, "get_recent_meetings", {}, result))
                yield {
                    "step": step,
                    "tool": "get_recent_meetings",
                    "label": tool_label("get_recent_meetings"),
                    "args": {},
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }

    from fengsuan.app.services.agent_standing import standing_for_ask

    voice_line, intro_src, voice = standing_for_ask(db, lab_id, scope)
    if (
        intro_src is None
        and voice
        and scope in {"lab", "onboarding"}
        and _is_intro_question(question)
        and "怎么说话" in (question or "")
    ):
        intro_src = {
            "id": f"labvoice:{lab_id}",
            "kind": "lab_intro",
            "title": "怎么说话",
            "excerpt": voice[:400],
            "agent_voice": voice[:400],
            "href": f"/labs/{lab_id}/assistant",
        }
    about_intro = _is_intro_question(question) or _is_roster_question(question)
    if intro_src:
        hit = next((s for s in sources.items if s.get("id") == intro_src.get("id")), None)
        if hit:
            for field in ("excerpt", "intro_address", "intro_custom", "intro_premise", "agent_voice", "title"):
                if intro_src.get(field):
                    hit[field] = intro_src[field]
        elif about_intro:
            sources.add(intro_src, force=True)

    extra = system_extra
    if voice_line:
        extra = (extra + "\n" if extra else "") + voice_line
    if direction_id:
        extra = (extra + "\n" if extra else "") + "用户选了研究方向，只引用该方向（含小课题）已入库的材料。"
    steer = _steer_line(scope, question)
    if steer:
        extra = (extra + "\n" if extra else "") + steer
    if TYPE_HINT.get(qtype):
        extra = (extra + "\n" if extra else "") + TYPE_HINT[qtype]
    messages: list[dict] = [{"role": "system", "content": _system_prompt(scope, extra)}]
    for turn in (history or [])[-6:]:
        q = (turn.get("question") or "").strip()
        a = (turn.get("answer") or "").strip()
        if q:
            messages.append({"role": "user", "content": q[:1200]})
        if a:
            messages.append({"role": "assistant", "content": a[:1200]})
    messages.append({"role": "user", "content": question[:4000]})
    seen_tools: set[str] = set()
    for obs_step, obs_name, obs_args, obs_result in observed:
        seen_tools.add(obs_name)
        _append_observed(messages, obs_step, obs_name, obs_args, obs_result)

    stub = llm_stub_enabled()
    person_ready = qtype == "web_person" and bool(seen_tools & {"scholar_find_person", "get_member_identity", "scholar_person_profile"})
    field_ready = qtype in {"frontier", "field_people", "sweep", "landscape"} and bool(
        seen_tools & {"get_direction_frontier", "scholar_topic_leaders", "scholar_search", "sweep_plan", "sweep_screen", "get_paper_landscape"}
    )
    if not stub and not person_ready and not field_ready:
        schemas = openai_tools(scope, include_writes=not confirm)
        while True:
            if degraded == "tool_failures":
                break
            reason = budget.exhausted(step=step, tool_calls=tool_calls)
            if reason:
                degraded = degraded or f"budget:{reason}"
                break
            try:
                out = complete_with_tools(
                    db,
                    user_id=user_id,
                    lab_id=lab_id,
                    messages=messages,
                    tools=schemas,
                    feature=feature,
                )
            except ClassifiedHTTPException:
                degraded = "tool_calling_unavailable"
                break
            usage = out.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("completion_tokens") or 0)
            model_used = out.get("model") or model_used
            calls = out.get("tool_calls") or []
            web_hits = out.get("web_hits") or []
            if web_hits:
                web_result = ToolResult(
                    text="联网搜了：" + "；".join(str(q) for q in (out.get("web_queries") or [])[:3]),
                    sources=[
                        {
                            "id": f"web:{hit.get('url')}",
                            "kind": "web",
                            "title": hit.get("title") or hit.get("url"),
                            "href": hit.get("url") or "",
                            "url": hit.get("url") or "",
                            "site": "",
                            "excerpt": str(hit.get("snippet") or "")[:400],
                            "provider": "minimax",
                            "external": True,
                        }
                        for hit in web_hits[:6]
                        if hit.get("url")
                    ],
                )
                _use(web_result)
                step += 1
                tool_calls += 1
                steps.append(
                    {
                        "step": step,
                        "tool": "native_web",
                        "label": "联网搜了",
                        "args": {"queries": out.get("web_queries") or []},
                        "result_summary": web_result.summary(),
                        "source_ids": [s.get("id") for s in web_result.sources],
                        "latency_ms": 0,
                        "error": "",
                    }
                )
            if not calls:
                messages.append({"role": "assistant", "content": out.get("text") or ""})
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": out.get("text") or "",
                    "tool_calls": [
                        {
                            "id": c["id"] or f"call_{i}",
                            "type": "function",
                            "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)},
                        }
                        for i, c in enumerate(calls)
                    ],
                }
            )
            step += 1
            for i, call in enumerate(calls):
                if tool_calls >= budget.max_tool_calls:
                    break
                tool_calls += 1
                result = _do(step, call["name"], call["args"])
                yield {
                    "step": step,
                    "tool": call["name"],
                    "label": tool_label(call["name"]),
                    "args": call["args"],
                    "result_summary": result.summary(),
                    "source_ids": [s.get("id") for s in result.sources],
                }
                messages.append(_tool_messages(call["id"] or f"call_{i}", call["name"], result))
                if (
                    call["name"] in {"find_author", "get_author_works"}
                    and (result.data or {}).get("miss")
                    and "scholar_find_person" not in seen_tools
                ):
                    payload = public_person_args(question, call["args"], turn)
                    if payload and tool_calls < budget.max_tool_calls:
                        tool_calls += 1
                        step += 1
                        seen_tools.add("scholar_find_person")
                        pub = _do(step, "scholar_find_person", payload)
                        yield {
                            "step": step,
                            "tool": "scholar_find_person",
                            "label": tool_label("scholar_find_person"),
                            "args": payload,
                            "result_summary": pub.summary(),
                            "source_ids": [s.get("id") for s in pub.sources],
                        }
                        messages.append(_tool_messages(f"call_pub_{i}", "scholar_find_person", pub))
                        for nxt_name, nxt_args in followups("scholar_find_person", pub.data):
                            if tool_calls >= budget.max_tool_calls:
                                break
                            tool_calls += 1
                            step += 1
                            nxt = _do(step, nxt_name, nxt_args)
                            yield {
                                "step": step,
                                "tool": nxt_name,
                                "label": tool_label(nxt_name),
                                "args": nxt_args,
                                "result_summary": nxt.summary(),
                                "source_ids": [s.get("id") for s in nxt.sources],
                            }
                            messages.append(_tool_messages(f"call_pub_{i}_{nxt_name}", nxt_name, nxt))
                if degraded == "tool_failures":
                    break
    elif not confirm and not person_ready and not field_ready:
        for name in stub_plan(scope, question):
            spec = get_tool(name)
            if confirm and spec and spec.writes:
                continue
            if name in seen_tools:
                continue
            if degraded == "tool_failures" or budget.exhausted(step=step, tool_calls=tool_calls):
                break
            step += 1
            tool_calls += 1
            args: dict = {}
            if name == "get_paper_page_text":
                from fengsuan.app.services.agent.tools import _page_in_question

                page = ctx.extra.get("page_no") or _page_in_question(question or "")
                if page:
                    args = {"page_no": int(page)}
            elif name == "get_page_translation":
                from fengsuan.app.services.agent.tools import _page_in_question

                page = ctx.extra.get("page_no") or _page_in_question(question or "") or 1
                args = {"page_no": int(page), "lang": "zh"}
            elif name in {"search_lab_kb", "search_lab_literature", "search_open_sources", "search_passages"}:
                args = {"query": stub_query(question)}
            elif name == "get_job_preflight":
                kind = "reproduce" if ctx.scope == "paper" or "复现" in (question or "") else "research"
                args = {"kind": kind}
                if ctx.item_id:
                    args["item_id"] = ctx.item_id
            elif name == "submit_research_run":
                args = {"question": question[:500]}
            elif name == "submit_reproduce_run" and ctx.item_id:
                args = {"item_id": ctx.item_id}
            elif name == "writeback_manuscript":
                args = {"text": question[:1200], "manuscript_id": ctx.manuscript_id}
            elif name == "write_note":
                from fengsuan.app.services.agent.tools import _page_in_question

                page = ctx.extra.get("page_no") or _page_in_question(question or "") or 1
                args = {"content_md": question[:800], "page_no": int(page)}
            elif name == "write_highlight":
                from fengsuan.app.services.agent.tools import _page_in_question

                quote = (ctx.extra.get("excerpt") or "").strip() or question[:200]
                page = ctx.extra.get("page_no") or _page_in_question(question or "") or 1
                args = {
                    "page_no": int(page),
                    "quote": quote[:200],
                    "content_md": question[:400],
                }
            elif name == "get_my_notes":
                from fengsuan.app.services.agent.tools import _page_in_question

                page = ctx.extra.get("page_no") or _page_in_question(question or "")
                args = {"page_no": int(page)} if page else {}
            elif name == "get_member_profile":
                q = question or ""
                if any(tok in q for tok in ("我的档案", "本人", "我自己", "我的导师", "我导师", "谁带我")):
                    args = {"user_id": user_id}
            elif name == "get_lab_tasks":
                if any(tok in (question or "") for tok in ("我的任务", "派给我", "我的待办")):
                    args = {"mine": True}
            elif name == "get_lab_literature":
                q = question or ""
                if "核心文献" in q and "写进" not in q and "点名" not in q:
                    args["core"] = True
                if any(tok in q for tok in ("我还没读", "还没读哪些")):
                    args["unread"] = True
                if "没有pdf" in q.lower() or "没有 pdf" in q.lower():
                    args["has_pdf"] = False
            elif name == "get_lab_activity":
                if any(tok in (question or "") for tok in ("我的周志", "我的日志", "我写的日志", "我的工作日志")):
                    args = {"mine": True}
            elif name == "get_lab_manuscripts":
                if any(tok in (question or "") for tok in ("我的稿", "我在写的稿")):
                    args = {"mine": True}
            elif name == "get_run_detail":
                q = question or ""
                if "复现" in q and "调研" not in q:
                    args = {"kind": "reproduce"}
                elif any(tok in q for tok in ("调研", "综述作业")):
                    args = {"kind": "research"}
            elif name == "get_lab_glossary":
                token = stub_query(question or "")
                if (
                    token
                    and token not in {"怎么译", "术语", "术语表", "锁定不译"}
                    and "术语" not in token
                    and "怎么译" not in token
                    and len(token) <= 24
                ):
                    args = {"src": token}
            if not args:
                from fengsuan.app.services.agent.understand import stub_args

                extra_args = stub_args(name, question, ctx.extra.get("memory") or {})
                if extra_args:
                    args = extra_args
            result = _do(step, name, args)
            yield {
                "step": step,
                "tool": name,
                "label": tool_label(name),
                "args": args,
                "result_summary": result.summary(),
                "source_ids": [s.get("id") for s in result.sources],
            }

    # 收尾作答：同一套观察 + 编号来源，不再另开一轮丢掉工具空结果。
    corpus = budget_block(sources.items, qtype)
    groups = relation_groups(sources.items)
    observations = [
        {
            "tool": s.get("tool") or "",
            "summary": s.get("result_summary") or "",
            "error": s.get("error") or "",
        }
        for s in steps[:16]
    ]
    history_lines = []
    for turn in (history or [])[-4:]:
        hq = (turn.get("question") or "")[:300]
        ha = (turn.get("answer") or "")[:300]
        if hq:
            history_lines.append(f"问：{hq}\n答：{ha}")
    observed = []
    for row in observations:
        if row.get("error"):
            observed.append(f"{row.get('tool') or '工具'}失败：{row.get('error')}")
        elif row.get("summary"):
            observed.append(str(row.get("summary")))
    group_rule = ""
    if len(groups) >= 2 or (groups and groups[0]["kind"] != "own_work"):
        group_rule = (
            "按关系分组写，每组一个小标题（"
            + " / ".join(g["label"] for g in groups)
            + "），组内列篇名并标 [S#]；只按名字对上的作者要说明可能不是同一人。"
        )
    final_user = (
        f"问题：\n{question[:2000]}\n\n"
        f"查到的材料：\n{corpus[:40000] or '（没有查到可引用的材料）'}\n\n"
        f"查的时候发生了什么：\n" + ("\n".join(observed) or "没有额外说明") + "\n\n"
        + (f"之前问过：\n" + "\n".join(history_lines) + "\n\n" if history_lines else "")
        + "只输出给用户看的中文结论。每句结论后标 [S#]。不要写草稿，不要复述这段要求，不要写工具名。组里没有这个人时，直接写公开学术库查到的身份和论文。没有来源就说查不到，不要问要不要再去查。"
        + group_rule
    )
    final_messages = [
        {"role": "system", "content": _system_prompt(scope, extra)},
        {"role": "user", "content": final_user},
    ]
    composed = ""
    labish = scope in {"lab", "onboarding"}
    if confirm and not steps:
        composed = "没有待确认的写入。先让我查出要收的论文，或要确认的身份。"
    elif not ask_back and qtype == "web_person":
        composed = _compose_person(question, sources, steps, turn) or _compose_web(question, sources, steps)
    elif not ask_back and qtype in {"frontier", "field_people", "sweep", "landscape"}:
        composed = _compose_field(question, sources, steps, turn) or _compose_web(question, sources, steps)
    elif stub and not ask_back and qtype == "import":
        composed = _compose_web(question, sources, steps)
    if not composed and not ask_back and labish and _is_roster_question(question):
        composed = _compose_members(question, sources)
    elif not ask_back and labish and _is_direction_question(question):
        composed = _compose_directions(sources)
    elif not ask_back and labish and _is_intro_question(question):
        composed = _compose_intro(sources, question) or "还没写要记住的事。"
    elif not ask_back and labish and _is_count_question(question):
        composed = _compose_counts(sources, steps)
    elif not ask_back and labish and _is_catalog_question(question):
        composed = _compose_catalog(question, sources, steps)
    elif not ask_back and labish and _is_work_question(question):
        composed = _compose_work(sources, steps)
    elif not ask_back and scope in {"lab", "onboarding", "paper"} and _is_readers_question(question):
        composed = _compose_readers(sources, steps)
    elif not ask_back and labish and _is_task_question(question):
        composed = _compose_tasks(question, sources, steps)
    elif not ask_back and labish and _is_activity_question(question):
        composed = _compose_activity(question, sources, steps)
    elif not ask_back and labish and _is_glossary_question(question):
        composed = _compose_glossary(question, sources, steps)
    elif not ask_back and labish and _is_meeting_question(question):
        composed = _compose_meetings(sources, steps)
    from fengsuan.app.services.agent.parse import clarify_should_ask, infer_goal, shape_by_goal, understanding_note

    clarify_ask = not ask_back and clarify_should_ask(turn)
    spoken = ""
    if not stub and not ask_back and not clarify_ask and composed and qtype in {"web_person", "frontier", "field_people"}:
        spoken = _write_from_evidence(db, user_id, lab_id, question, composed, sources, turn, sid)
    pieces: list[str] = []
    if ask_back:
        pieces.append(_compose_ask_back(ask_back, sources))
    elif clarify_ask:
        pieces.append(
            _compose_ask_back(
                {"kind": "clarify", "question": str(turn.get("clarify") or ""), "options": []},
                sources,
            )
        )
    elif spoken:
        pieces.append(shape_by_goal(infer_goal(question, turn), spoken, sources))
    elif composed:
        pieces.append(shape_by_goal(infer_goal(question, turn), composed, sources))
    else:
        for delta in iter_chat_deltas(
            db,
            user_id=user_id,
            lab_id=lab_id,
            messages=final_messages,
            feature=feature,
            run_id=sid,
            ledger=False,
        ):
            pieces.append(delta)
    answer = _drop_author_hint(_strip_process_notes("".join(pieces)))
    if not ask_back and not clarify_ask and _looks_like_scratchpad(answer):
        answer = (
            _compose_person(question, sources, steps, turn)
            or _compose_web(question, sources, steps)
            or _zh_from_sources(sources)
            or "这次写成了内部草稿，没有采用。公开记录在下面的出处里。"
        )
    if not ask_back and not clarify_ask and not composed and (_needs_zh_rewrite(question, answer) or _echoes_tools(answer, sources)):
        rebuilt = (
            _compose_members(question, sources)
            or _compose_directions(sources)
            or (_compose_counts(sources, steps) if _is_count_question(question) else "")
            or (_compose_catalog(question, sources, steps) if _is_catalog_question(question) else "")
            or (_compose_work(sources, steps) if _is_work_question(question) else "")
            or (_compose_readers(sources, steps) if _is_readers_question(question) else "")
            or (_compose_tasks(question, sources, steps) if _is_task_question(question) else "")
            or (_compose_activity(question, sources, steps) if _is_activity_question(question) else "")
            or (_compose_glossary(question, sources, steps) if _is_glossary_question(question) else "")
            or (_compose_meetings(sources, steps) if _is_meeting_question(question) else "")
            or _compose_person(question, sources, steps, turn)
            or _compose_field(question, sources, steps, turn)
            or _zh_from_sources(sources)
        )
        if _needs_zh_rewrite(question, answer):
            answer = rebuilt if rebuilt and not _needs_zh_rewrite(question, rebuilt) else "这次写成了英文说明，没有采用。请再问一次。"
        else:
            answer = rebuilt or answer
    if not answer.strip() and not sources.items and not clarify_ask:
        answer = "现在查不到可引用的材料，先别当结论用。可以补充一下具体想问哪一点。"
    if not ask_back and not clarify_ask:
        note = understanding_note(turn)
        habit = str((turn or {}).get("habit_note") or "").strip()
        if habit and habit not in answer:
            answer = (answer.rstrip() + "\n" + habit + "。").strip()
        if note and note not in answer:
            answer = (answer.rstrip() + "\n" + note).strip()

    grounded = ground_by_source_id(answer, sources)
    weak = grounded.get("ungrounded") or []
    ratio = grounded.get("grounded_ratio")
    try:
        ratio_n = float(ratio)
    except (TypeError, ValueError):
        ratio_n = 1.0
    if should_recover_evidence(ratio_n, weak, steps, scope=scope, item_id=item_id or "", stub=stub):
        step += 1
        query = re.sub(r"\[S\d+\]", "", str(weak[0] or ""))
        query = re.sub(r"\s+", " ", query).strip()[:80] or (question or "")[:80]
        recovered = None
        try:
            recovered = _do(step, "search_in_paper", {"query": query, "item_id": item_id})
        except Exception:
            recovered = None
        pages = []
        for src in (getattr(recovered, "sources", None) or [])[:3]:
            label = src.get("label") or ""
            page_no = src.get("page")
            if page_no and label:
                pages.append(f"第 {page_no} 页 [{label}]")
        extra = "又查了一次这一篇。"
        extra += ("补上" + "、".join(pages) + "。") if pages else "新摘录仍对不上原先那几句。"
        answer = ((grounded.get("answer") or answer).rstrip() + "\n" + extra).strip()
        grounded = ground_by_source_id(answer, sources)
        weak = grounded.get("ungrounded") or []
        try:
            ratio_n = float(grounded.get("grounded_ratio"))
        except (TypeError, ValueError):
            ratio_n = 1.0
    if weak and ratio_n < 0.8:
        shown = "；".join(str(bit)[:48] for bit in weak[:3])
        note = f"有 {len(weak)} 句依据不足：{shown}"
        if note not in (grounded.get("answer") or ""):
            grounded["answer"] = ((grounded.get("answer") or "").rstrip() + "\n" + note).strip()
    _mark_used(grounded["answer"], sources)
    ledger_usage(
        db,
        lab_id=lab_id,
        user_id=user_id,
        feature=feature,
        model=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        run_id=sid,
        credential_scope="agent",
    )
    hits = []
    from fengsuan.app.services.agent.understand import _org as _org_of
    from fengsuan.app.services.agent.understand import _person as _person_of
    # 记忆先落，answer 后落；同一 step 时按写入顺序排，追踪的最后一轮仍是答案。

    for src in sources.items:
        label = str(src.get("label") or "")
        if not label.startswith("S") or not label[1:].isdigit():
            continue
        blob = f"{src.get('title') or ''} {src.get('excerpt') or ''}"
        hits.append(
            {
                "n": int(label[1:]),
                "title": str(src.get("title") or "")[:120],
                "excerpt": str(src.get("excerpt") or "")[:180],
                "person": (
                    str(turn.get("person_name") or "")
                    if turn.get("person_name") and str(turn.get("person_name")) in blob
                    else (_person_of(blob) or "")
                ),
                "affiliation": (
                    str(turn.get("affiliation") or "")
                    if turn.get("affiliation") and str(turn.get("affiliation")) in blob
                    else (_org_of(blob) or "")
                ),
            }
        )
    if hits:
        trace.record(
            db,
            lab_id=lab_id,
            user_id=user_id,
            session_id=sid,
            feature=feature,
            scope=scope,
            # 与 answer 同一步：记忆是这一轮的一部分，不该排到答案后面。
            step=step + 1,
            kind="memory",
            args={"hits": hits[:12]},
        )
    trace.record(
        db,
        lab_id=lab_id,
        user_id=user_id,
        session_id=sid,
        feature=feature,
        scope=scope,
        step=step + 1,
        kind="answer",
        args={"question": (question or "")[:500]},
        result_summary=grounded["answer"][:2000],
        source_ids=[c["source_id"] for c in grounded["citations"]],
    )
    try:
        from fengsuan.app.services.observability import inc

        inc("agent_steps", len(steps))
        if pending_confirm:
            inc("agent_pending_confirm", len(pending_confirm))
        if degraded:
            inc("agent_degraded")
    except Exception:
        pass
    yield {
        "done": True,
        "session_id": sid,
        "trace_id": sid,
        "answer": grounded["answer"],
        "text": grounded["answer"],
        "citations": grounded["citations"],
        "ungrounded": grounded["ungrounded"],
        "grounded_ratio": grounded["grounded_ratio"],
        "sources": [
            {k: v for k, v in s.items() if k != "excerpt"} | {"excerpt": (s.get("excerpt") or "")[:300]}
            for s in sources.items
        ],
        "steps": steps,
        "tool_calls": tool_calls,
        "degraded": degraded,
        "degraded_to": "chat" if degraded else "",
        "pending_confirm": pending_confirm,
        "question_type": qtype,
        "ask_back": ask_back or ({"kind": "clarify", "question": str(turn.get("clarify") or "")} if clarify_ask else None),
        "relation_groups": groups,
        "followups": [] if ask_back or clarify_ask else _followups(
            question,
            grounded["citations"],
            grounded["ungrounded"],
            sources.items,
            turn,
            qtype,
            len(memory.get("papers") or []) if isinstance(memory, dict) else 0,
        ),
        "available_tools": [t.name for t in list_tools(scope)],
        "empty_next": (
            next((s["empty_next"] for s in reversed(steps) if s.get("empty_next")), None)
            if not any((not s.get("empty")) and s.get("source_ids") for s in steps)
            else None
        ),
    }


def agent_answer(db: Session, **kwargs) -> dict:
    """非流式调用。内部仍走同一循环，保证两条路径口径一致。"""
    out: dict = {}
    for ev in run_agent(db, **kwargs):
        if ev.get("done"):
            out = {k: v for k, v in ev.items() if k != "done"}
    return out
