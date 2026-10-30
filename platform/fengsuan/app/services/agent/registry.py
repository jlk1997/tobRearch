from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

SCOPES = ("paper", "lab", "onboarding", "writing")

TOOL_LABELS = {
    "search_lab_kb": "搜知识库",
    "search_lab_literature": "搜文献库",
    "search_open_sources": "搜外面的论文",
    "get_direction_tree": "看方向",
    "get_lab_members": "看名册",
    "get_member_profile": "看档案",
    "get_milestones": "看节点",
    "get_my_next_step": "看下一步",
    "get_recent_meetings": "看组会",
    "get_lab_activity": "看动态",
    "get_lab_glossary": "看术语",
    "get_lab_tasks": "看任务",
    "get_lab_kb_docs": "看知识库目录",
    "get_lab_files": "看资料柜",
    "get_starter_reading": "看先读",
    "get_lab_literature": "看文献目录",
    "get_paper_readers": "看谁在读",
    "get_my_reading": "看我的进度",
    "get_my_tutor_history": "看辅导记录",
    "get_lab_manuscripts": "看手稿目录",
    "get_lab_runs": "看作业",
    "get_job_preflight": "看预检",
    "get_unused_research": "看没写到的综述",
    "get_manuscript_brief": "看手稿摘要",
    "get_manuscript_comments": "看稿评",
    "get_manuscript_versions": "看稿版本",
    "get_method_compare": "对照方法卡",
    "get_core_coverage": "核核心文献",
    "get_lab_datasets": "看数据集",
    "get_run_detail": "看作业详情",
    "get_related_work": "看相关工作",
    "get_honesty_brief": "看诚实口径",
    "get_citation_links": "看引用关系",
    "get_paper_page_text": "取页正文",
    "get_page_translation": "取页译文",
    "get_paper_structure": "看章节",
    "get_paper_biblio": "看书目",
    "get_method_card": "看方法卡",
    "get_reproduce_evidence": "看复现证据",
    "get_my_notes": "看我的笔记",
    "write_note": "写笔记",
    "write_highlight": "划高亮",
    "writeback_manuscript": "写回手稿",
    "submit_research_run": "提交调研",
    "submit_reproduce_run": "提交复现",
    "get_lab_overview": "看了组里的介绍",
    "get_direction_brief": "看了这个方向",
    "get_paper_digests": "看了组里的论文",
    "get_paper_digest": "看了这一篇",
    "get_member_work": "看了谁在做什么",
    "get_direction_frontier": "看了外面的论文",
    "find_author": "认了作者",
    "get_author_works": "看了这位作者的论文",
    "get_paper_references": "看了它引了谁",
    "get_citing_papers": "看了谁引了它",
    "get_related_papers": "看了相关论文",
    "search_passages": "翻了正文",
    "get_survey_digests": "翻了组里的笔记",
    "get_library_stats": "数了组里的文献",
    "web_search": "联网搜了",
    "web_read": "读了网页",
    "scholar_search": "查了谷歌学术",
    "scholar_find_person": "认了人",
    "scholar_person_profile": "看了这位的公开学术身份",
    "get_member_identity": "看了成员的公开身份",
    "bind_member_identity": "确认了学术身份",
    "find_lab_expert": "看了组里谁做过",
    "scholar_topic_leaders": "看了领域学者",
    "get_paper_landscape": "看了这篇在外面",
    "sweep_plan": "拟了检索式",
    "sweep_search": "全面检索了",
    "sweep_expand": "顺着引用找了",
    "sweep_screen": "筛了检索结果",
    "import_paper": "收进组里",
}

KIND_LABELS = {
    "page": "正文",
    "abstract": "摘要",
    "structure": "章节",
    "biblio": "书目",
    "method_card": "方法卡",
    "method_card_claim": "无依据主张",
    "page_translation": "页译文",
    "note": "笔记",
    "highlight": "高亮",
    "literature": "文献",
    "kb_chunk": "知识库",
    "kb_doc": "文档",
    "kb": "知识库",
    "lab_file": "资料",
    "reading": "在读",
    "tutor": "辅导",
    "manuscript": "手稿",
    "manuscript_comment": "稿评",
    "manuscript_version": "稿版本",
    "citation": "引用",
    "reproduce": "复现",
    "reproduce_gaps": "复现差距",
    "claimed_metrics": "论文声称",
    "research_run": "文献调研",
    "reproduce_run": "复现作业",
    "queued": "排队",
    "job_preflight": "预检",
    "unused_research": "没写到",
    "dataset": "数据集",
    "artifact": "产物",
    "honesty": "诚实口径",
    "related": "组内相关",
    "direction": "方向",
    "member": "成员",
    "profile": "档案",
    "milestone": "节点",
    "next_step": "下一步",
    "meeting": "组会",
    "work_log": "工作日志",
    "activity": "动态",
    "glossary": "术语",
    "task": "任务",
    "external": "还没进组",
    "lab_intro": "组里写的",
    "digest": "笔记",
    "core_mentioned": "已点名核心",
    "core_missing": "未点名核心",
    "author": "作者",
    "web": "网页",
    "web_page": "网上",
    "scholar": "公开学术",
    "identity": "学术身份",
    "sweep": "检索说明",
}


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name or "") or (name or "查证")


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind or "") or ""


@dataclass
class ToolContext:
    """工具执行上下文。权限校验的唯一事实来源，提示词不参与授权。"""

    db: Session
    user_id: str
    lab_id: str
    scope: str = "paper"
    item_id: str = ""
    manuscript_id: str = ""
    role: str = "student"
    confirm: bool = False
    direction_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """工具返回。`sources` 里的 `id` 就是接地用的 `source_id`。"""

    text: str = ""
    sources: list[dict] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    error: str = ""

    def summary(self, limit: int = 160) -> str:
        if self.error:
            return f"失败：{self.error}"[:limit]
        head = self.text.strip().replace("\n", " ")
        if head:
            return head[:limit]
        if self.sources:
            return f"命中 {len(self.sources)} 条来源"
        return "无结果"


@dataclass
class ToolSpec:
    name: str
    scopes: tuple[str, ...]
    description: str
    params: dict
    fn: Callable[[ToolContext, dict], ToolResult]
    writes: bool = False
    needs_confirm: bool = False
    stub_keywords: tuple[str, ...] = ()

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.params.get("properties") or {},
                    "required": self.params.get("required") or [],
                },
            },
        }


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    *,
    scopes: tuple[str, ...] | list[str],
    description: str,
    params: dict | None = None,
    writes: bool = False,
    needs_confirm: bool = False,
    stub_keywords: tuple[str, ...] | list[str] = (),
):
    """注册一个工具。重复注册同名工具直接覆盖（热重载友好）。"""

    bad = [s for s in scopes if s not in SCOPES]
    if bad:
        raise ValueError(f"unknown tool scope: {bad}")

    def deco(fn: Callable[[ToolContext, dict], ToolResult]) -> Callable[[ToolContext, dict], ToolResult]:
        _REGISTRY[name] = ToolSpec(
            name=name,
            scopes=tuple(scopes),
            description=description,
            params=params or {},
            fn=fn,
            writes=writes,
            needs_confirm=needs_confirm,
            stub_keywords=tuple(stub_keywords),
        )
        return fn

    return deco


_LOADED = False


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    from fengsuan.app.services.agent import tools, tools_scale, tools_web  # noqa: F401  注册副作用

    _LOADED = True


def list_tools(scope: str, *, include_writes: bool = True) -> list[ToolSpec]:
    _ensure_loaded()
    scope = (scope or "paper").strip()
    out = [t for t in _REGISTRY.values() if scope in t.scopes]
    if not include_writes:
        out = [t for t in out if not t.writes]
    return sorted(out, key=lambda t: t.name)


def get_tool(name: str) -> ToolSpec | None:
    _ensure_loaded()
    return _REGISTRY.get(name)


def openai_tools(scope: str, *, include_writes: bool = True) -> list[dict]:
    return [t.openai_schema() for t in list_tools(scope, include_writes=include_writes)]


def normalize_confirm_tools(scope: str, raw) -> list[tuple[str, dict]]:
    """用户点确认后回放的写入工具。只认本域 needs_confirm，参数截断，拒绝只读工具。"""
    _ensure_loaded()
    scope = (scope or "paper").strip()
    out: list[tuple[str, dict]] = []
    items = raw if isinstance(raw, list) else []
    for item in items[:3]:
        if hasattr(item, "tool"):
            name = str(getattr(item, "tool", "") or getattr(item, "name", "") or "").strip()
            raw_args = getattr(item, "args", None)
        elif isinstance(item, dict):
            name = str(item.get("tool") or item.get("name") or "").strip()
            raw_args = item.get("args")
        else:
            continue
        spec = _REGISTRY.get(name)
        if spec is None or scope not in spec.scopes or not spec.needs_confirm:
            continue
        raw_args = raw_args if isinstance(raw_args, dict) else {}
        clean: dict = {}
        for key, val in list(raw_args.items())[:12]:
            k = str(key)[:40]
            if isinstance(val, str):
                clean[k] = val[:4000]
            elif isinstance(val, bool):
                clean[k] = val
            elif isinstance(val, int):
                clean[k] = val
            elif isinstance(val, float):
                clean[k] = val
            elif isinstance(val, list):
                kept = []
                for item in val[:10]:
                    if isinstance(item, str):
                        kept.append(item[:300])
                    elif isinstance(item, dict):
                        kept.append({str(a)[:40]: (str(b)[:300] if not isinstance(b, (int, float)) else b) for a, b in list(item.items())[:8]})
                clean[k] = kept
            elif val is None:
                continue
            else:
                clean[k] = str(val)[:400]
        out.append((name, clean))
    return out


def stub_plan(scope: str, question: str, *, max_tools: int = 3) -> list[str]:
    """stub 环境的确定性工具选择。

    评测夹具（`test_agent_eval.py`）断言的就是这条路径：它检验运行时的
    选择 / 接地 / 预算逻辑，而不是某个云端模型的水平。
    """
    q = (question or "").lower()
    scored: list[tuple[int, str]] = []
    # 写入类只在问句点名时入选：这样 stub 也能走出 pending_confirm，而不会静默烧算力。
    for spec in list_tools(scope, include_writes=True):
        hits = sum(1 for kw in spec.stub_keywords if kw and kw.lower() in q)
        if hits:
            scored.append((hits, spec.name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = [name for _, name in scored[:max_tools]]
    if "还写过" in q:
        picked = [name for name in picked if name != "get_paper_biblio"]
    if "在做" in q and not any(tok in q for tok in ("组里", "这个组", "方向", "实验室", "论文都")):
        spec = get_tool("get_member_work")
        if spec is not None and scope in spec.scopes:
            picked = ["get_member_work"] + [n for n in picked if n != "get_member_work"]
            picked = picked[:max_tools]
    if any(tok in q for tok in ("我的导师", "我导师", "谁带我")):
        spec = get_tool("get_member_profile")
        if spec is not None and scope in spec.scopes:
            picked = ["get_member_profile"] + [n for n in picked if n != "get_member_profile"]
            picked = picked[:max_tools]
    if any(tok in q for tok in ("谁在读", "谁读过", "读这篇", "在读这篇")) and not any(
        tok in q for tok in ("先读", "读起", "哪几篇")
    ):
        spec = get_tool("get_paper_readers")
        if spec is not None and scope in spec.scopes:
            picked = ["get_paper_readers"] + [n for n in picked if n != "get_paper_readers"]
            picked = picked[:max_tools]
    forced: list[tuple[tuple[str, ...], str]] = [
        (("全面检索", "系统检索", "中英文都"), "sweep_plan"),
        (("收进组里", "导入这篇", "加到论文库"), "import_paper"),
        (("学术环境", "谁在跟进", "在外面的影响", "被引曲线"), "get_paper_landscape"),
        (("代表学者", "大牛", "顶尖团队", "谁做得好"), "scholar_topic_leaders"),
        (("谁做过", "谁懂", "谁熟悉"), "find_lab_expert"),
        (("在网上", "网上的", "学术情况", "履历", "h指数", "h 指数"), "get_member_identity"),
        (("值得读", "最新进展", "推荐论文"), "get_direction_frontier"),
        (("谷歌学术",), "scholar_search"),
        (("综述好了", "跑完了", "还在排队", "作业状态", "等算力", "等gpu", "缺数据停", "算力排队"), "get_lab_runs"),
        (("还能跑", "能提交吗", "工人开了", "提交前", "还缺数据", "算力关了", "引擎装了", "还剩多少token", "token预算", "这个月token"), "get_job_preflight"),
        (("没用上", "没写到", "未写进综述", "未使用的结果"), "get_unused_research"),
        (("写进正文", "点名核心", "核心写进"), "get_core_coverage"),
        (("组里有哪些文献", "库里有哪些", "核心文献有哪些", "我还没读", "没有pdf的篇", "文献目录", "年有哪些", "年的文献", "会上有哪些"), "get_lab_literature"),
        (("哪些主张没依据", "无依据主张", "主张没依据"), "get_method_card"),
        (("方法差", "方法对照", "这几篇方法", "方法对比"), "get_method_compare"),
        (("稿子评论", "手稿评论", "导师批了", "这篇稿批", "稿上的评论", "有什么评论"), "get_manuscript_comments"),
        (("改了几版", "上次改了", "版本历史", "稿子版本", "这篇稿改了"), "get_manuscript_versions"),
        (("引用还没实测", "稿里哪些", "稿的复现差距", "稿里还没实测"), "get_manuscript_brief"),
        (("这篇稿", "手稿引用", "引用了谁", "稿子引用"), "get_manuscript_brief"),
        (("上次辅导", "我问过这篇", "辅导记录", "上次问过", "辅导说过"), "get_my_tutor_history"),
        (("我读到哪", "我读完了", "我的进度", "这篇我读", "读到第几页"), "get_my_reading"),
        (("哪页说", "是哪页的", "这句话在哪", "这段在哪页", "这个数在哪页"), "get_paper_page_text"),
        (("在第几页", "哪一节", "方法节", "实验节", "引言在第", "定位一下章节", "哪张图", "图1在", "figure 1", "fig. 1", "表1在", "第2节", "第3节", "公式1", "式1在", "算法1", "equation 1"), "get_paper_structure"),
        (("我该做", "该做什么", "下一步做什么"), "get_my_next_step"),
        (("为什么失败", "卡在哪", "任务详情", "作业失败", "那个任务", "作业产出", "产物文件", "工件", "那个失败", "失败的作业", "actual_metrics", "literature_evidence"), "get_run_detail"),
        (("数据集", "什么数据", "哪份数据", "用哪份", "有没有数据", "这份数据", "挂了哪份", "用了哪份数据", "数据被哪次", "哪次作业用了这"), "get_lab_datasets"),
        (("相关工作该", "相关工作点名", "该写谁", "该引用哪些方法"), "get_related_work"),
        (("还没做完", "写成成功", "没复现就写", "无人在读", "还没写方法", "诚实口径"), "get_honesty_brief"),
        (("这篇doi", "哪年发", "发在哪个", "这篇作者", "有没有pdf", "这篇书目", "arxiv号", "什么期刊", "发表年份", "年那篇", "会上那篇", "期刊那篇", "那篇是哪篇", "这篇是哪篇", "pmid", "pubmed"), "get_paper_biblio"),
        (("被哪篇稿引用", "被谁引用", "引用关系", "谁连着这篇", "图谱里谁"), "get_citation_links"),
        (("我的周志", "我写的日志", "我的工作日志"), "get_lab_activity"),
        (("工作日志", "组里动态", "最近干什么", "谁交了作业", "最近做了什么"), "get_lab_activity"),
        (("我的稿", "我在写的稿"), "get_lab_manuscripts"),
        (("的稿",), "get_lab_manuscripts"),
        (("术语表", "锁定不译", "怎么译", "这个词译", "组里术语"), "get_lab_glossary"),
        (("过期任务", "任务到期", "看板截止"), "get_lab_tasks"),
        (("看板任务", "我的任务", "待办任务", "派给我", "组会派了", "的任务", "谁的任务"), "get_lab_tasks"),
        (("的档案", "介绍一下他", "介绍一下她"), "get_member_profile"),
        (("页译文", "这一页中文", "这页中文", "这页译", "译文缓存"), "get_page_translation"),
        (("知识库有哪些", "知识库文档", "上传了什么文档", "索引好了", "哪些文档入库"), "get_lab_kb_docs"),
        (("开题报告", "开题节点", "中期节点", "答辩节点"), "get_milestones"),
        (("这份文件写了", "文件里写了"), "get_lab_files"),
        (("共享文件", "组里文件", "实验室文件", "文件柜"), "get_lab_files"),
        (("这份文档写了", "文档正文"), "get_lab_kb_docs"),
        (("组会定了", "组会行动", "那次组会定"), "get_recent_meetings"),
        (("介绍这个实验室", "组里一页纸", "全组概况"), "get_lab_overview"),
        (("讲讲这个方向", "方向简报"), "get_direction_brief"),
        (("组里的论文都", "论文都讲了", "有没有做过"), "get_paper_digests"),
        (("大白话讲这篇", "讲讲这篇", "这篇讲了什么"), "get_paper_digest"),
        (("谁在做", "同学在做", "成员在做"), "get_member_work"),
        (("外面在做", "前沿", "相关作者", "该读什么", "推荐阅读"), "get_direction_frontier"),
        (("谁引了", "哪些论文引了", "引了这篇", "被引了多少", "被组里哪篇引"), "get_citing_papers"),
        (("参考文献", "引了哪些", "它引了", "这篇引了"), "get_paper_references"),
        (("相关的论文", "相近的论文", "类似的论文", "和这篇相关", "跟这篇像", "有关系的论文"), "get_related_papers"),
        (("还写过", "写过哪些", "的论文有哪些", "发过哪些", "一起署名", "合作者"), "get_author_works"),
        (("这个作者", "作者是谁", "同名", "是同一个人", "是不是同一个"), "find_author"),
        (("原文怎么说", "原话", "哪篇里提到", "哪篇写过", "原文里"), "search_passages"),
        (("综述一下", "总结一下", "主要方法有哪些", "有哪些做法", "脉络", "发展趋势", "整体上在做"), "get_survey_digests"),
        (("组里有多少篇", "一共有多少篇", "一共收了", "收了多少篇", "有几篇论文", "多少篇论文", "论文数量", "按年份分布", "统计一下组里"), "get_library_stats"),
    ]
    for toks, name in forced:
        if any(tok in q for tok in toks):
            if name == "get_paper_biblio" and "还写过" in q:
                continue
            spec = get_tool(name)
            if spec is not None and scope in spec.scopes:
                picked = [name] + [n for n in picked if n != name]
                picked = picked[:max_tools]
                break
    if any(n in picked for n in ("submit_research_run", "submit_reproduce_run")):
        spec = get_tool("get_job_preflight")
        if spec is not None and scope in spec.scopes:
            picked = ["get_job_preflight"] + [n for n in picked if n != "get_job_preflight"]
            picked = picked[:max_tools]
    from fengsuan.app.services.agent.tools import _ids_in_question

    dois, arxivs, pmids, cnkis = _ids_in_question(question or "")
    head = picked[0] if picked else ""
    if (
        (dois or arxivs or pmids or cnkis)
        and not any(tok in q for tok in ("有哪些文献", "年有哪些", "年的文献", "会上有哪些"))
        and head in {"", "search_lab_kb", "search_lab_literature", "search_open_sources", "get_paper_biblio"}
    ):
        spec = get_tool("get_paper_biblio")
        if spec is not None and scope in spec.scopes:
            picked = ["get_paper_biblio"] + [n for n in picked if n != "get_paper_biblio"]
            picked = picked[:max_tools]
    if picked:
        return picked
    defaults = {
        "paper": ["get_paper_page_text"],
        "lab": ["search_lab_kb", "search_lab_literature"],
        "onboarding": ["get_direction_tree", "get_lab_members"],
        "writing": ["search_lab_literature"],
    }
    available = {t.name for t in list_tools(scope, include_writes=False)}
    return [n for n in defaults.get(scope, []) if n in available]


def steer_tools(scope: str, question: str, *, max_tools: int = 3) -> list[str]:
    """给模型的注意力提示：更可能先用哪些工具。不从目录里删工具。"""
    return stub_plan(scope, question, max_tools=max_tools)
