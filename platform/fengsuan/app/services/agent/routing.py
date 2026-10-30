"""按问题类型分配材料：问人多给作者和篇目，问原文多给段落，问综述多给笔记。

材料按 token 算，不按条数算；同一篇的片段排在一起。
"""

from __future__ import annotations

import re

MATERIAL_TOKENS = 12000
PER_SOURCE_TOKENS = 1400

_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sweep", ("全面检索", "系统检索", "中英文都")),
    ("import", ("收进组里", "导入这篇", "加到论文库", "再收下一批")),
    ("landscape", ("学术环境", "谁在跟进", "在外面的影响", "被引曲线", "谁引用", "引用了这篇", "引用它的", "上下游")),
    ("field_people", ("代表学者", "大牛", "顶尖团队", "谁做得好")),
    ("web_person", ("在网上", "网上的", "学术情况", "科研情况", "学术成果", "研究成果", "研究什么", "做什么研究", "履历", "h指数", "h 指数")),
    ("frontier", ("值得读", "最新进展", "推荐论文", "前沿")),
    ("relation", ("谁引了", "引了哪些", "引了这篇", "参考文献", "它引了", "被引", "相关的论文", "相近的论文", "类似的论文", "和这篇相关", "跟这篇像", "有关系的论文")),
    ("person", ("还写过", "写过哪些", "的论文有哪些", "发过哪些", "这个作者", "作者是谁", "同名", "是同一个人", "一起署名", "合作者", "orcid")),
    ("passage", ("原文", "原话", "哪一页", "哪页", "哪段", "怎么说的", "实验设置", "超参", "公式", "具体数值", "哪篇写过", "哪篇里提到")),
    ("survey", ("综述", "总结一下", "整体", "都在做", "主要方法", "有哪些方法", "脉络", "趋势", "发展", "对比一下", "都讲了")),
    ("direction", ("方向",)),
    ("progress", ("进度", "读到", "作业", "排队", "任务", "节点", "下一步")),
)

GROUP_OF_KIND = {
    "page": "passage",
    "page_translation": "passage",
    "kb_chunk": "passage",
    "structure": "passage",
    "abstract": "digest",
    "digest": "digest",
    "method_card": "digest",
    "method_card_claim": "digest",
    "literature": "digest",
    "biblio": "digest",
    "author": "people",
    "identity": "people",
    "web": "other",
    "web_page": "other",
    "scholar": "other",
    "sweep": "other",
    "member": "people",
    "profile": "people",
    "citation": "relation",
    "related": "relation",
}

QUOTAS: dict[str, dict[str, float]] = {
    "person": {"people": 0.35, "digest": 0.35, "relation": 0.15, "passage": 0.1, "other": 0.05},
    "relation": {"relation": 0.4, "digest": 0.3, "people": 0.1, "passage": 0.15, "other": 0.05},
    "passage": {"passage": 0.65, "digest": 0.2, "relation": 0.05, "people": 0.02, "other": 0.08},
    "survey": {"digest": 0.55, "passage": 0.3, "relation": 0.1, "people": 0.02, "other": 0.03},
    "direction": {"other": 0.35, "digest": 0.35, "passage": 0.2, "people": 0.1, "relation": 0.0},
    "progress": {"other": 0.6, "digest": 0.2, "passage": 0.1, "people": 0.1, "relation": 0.0},
    "general": {"passage": 0.4, "digest": 0.3, "other": 0.15, "people": 0.075, "relation": 0.075},
    "sweep": {"other": 0.45, "digest": 0.35, "relation": 0.1, "people": 0.05, "passage": 0.05},
    "web_person": {"people": 0.45, "other": 0.35, "digest": 0.15, "relation": 0.05, "passage": 0.0},
    "field_people": {"people": 0.4, "other": 0.4, "digest": 0.15, "relation": 0.05, "passage": 0.0},
    "frontier": {"other": 0.5, "digest": 0.3, "people": 0.15, "relation": 0.05, "passage": 0.0},
    "landscape": {"relation": 0.35, "digest": 0.25, "other": 0.3, "people": 0.1, "passage": 0.0},
    "import": {"digest": 0.5, "other": 0.4, "people": 0.05, "relation": 0.05, "passage": 0.0},
}

TYPE_HINT = {
    "person": "这是问人的问题：先认清是哪位作者，同名拿不准就请对方选，不要把同名的人当成一个。",
    "relation": "这是问论文之间关系的问题：按关系分组说（它引了 / 引了它 / 同一作者 / 常一起被引 / 引的文献相同 / 内容相近 / 同一方向），只报查到的关系。",
    "passage": "这是找原文的问题：引用原段落，说清在哪篇、哪一节、第几页。",
    "survey": "这是要整体概括的问题：先按主题归几类，每类点两三篇代表，再说各自差在哪。",
    "web_person": "这是问人的网上学术身份：先认人；拿不准就请对方选；每条事实写出处和日期；不写邮箱电话；没确认的写「可能是」。",
    "field_people": "这是问领域学者：写明统计口径和年份，列出凭据。不要说这是权威排名，也不要给组里的人排名。",
    "frontier": "这是问外面的新论文：说明为什么值得读，并写明还没放进这个组。",
    "landscape": "这是问一篇论文在外面的位置：发表处、被引、谁在跟进、其他路线。数字要带来源。",
    "sweep": "这是全面检索：先写检索说明（检索式、来源、各步篇数、没连上的源），再分主题，再说组里已有和还缺的。",
    "import": "这是要把论文收进组里：先给预览，等确认后再写入。付费全文不要假装已经拿到。",
}

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9]+")


def classify_question(question: str, turn: dict | None = None, memory: dict | None = None) -> str:
    from fengsuan.app.services.agent.understand import turn_ready, understand

    heuristic = understand(question, memory).get("intent") or ""
    if heuristic and any(
        tok in (question or "")
        for tok in ("这篇作者还写过", "作者还写过", "他还写过", "她还写过", "再收下一批", "下一批引用", "把引用它的", "把上面这几篇")
    ):
        return heuristic
    if turn_ready(turn):
        intent = str((turn or {}).get("intent") or "")
    else:
        intent = heuristic
    if intent in {"sweep", "import", "landscape", "field_people", "web_person", "frontier"}:
        return intent
    q = (question or "").lower()
    for name, toks in _TYPES:
        if any(t in q for t in toks):
            return name
    return "general"


def approx_tokens(text: str) -> int:
    s = text or ""
    return len(_CJK.findall(s)) + int(len(_WORD.findall(s)) * 1.3) + 1


def _clip_to_tokens(text: str, tokens: int) -> str:
    s = text or ""
    need = approx_tokens(s)
    if need <= tokens:
        return s
    keep = max(0, int(len(s) * tokens / max(1, need)))
    return s[:keep].rstrip() + "…"


def allocate(items: list[dict], qtype: str, total: int = MATERIAL_TOKENS) -> dict[str, int]:
    """每条来源能放多少 token。先按类型配额，剩下的再按顺序补给被截的。"""
    quotas = {g: int(total * share) for g, share in QUOTAS.get(qtype, QUOTAS["general"]).items()}
    used: dict[str, int] = {}
    alloc: dict[str, int] = {}
    need: dict[str, int] = {}
    for s in items:
        label = s.get("label") or ""
        n = min(approx_tokens(s.get("excerpt") or ""), PER_SOURCE_TOKENS)
        need[label] = n
        group = GROUP_OF_KIND.get(s.get("kind") or "", "other")
        room = max(0, quotas.get(group, 0) - used.get(group, 0))
        give = min(n, room)
        alloc[label] = give
        used[group] = used.get(group, 0) + give
    spare = total - sum(alloc.values())
    for s in items:
        if spare <= 0:
            break
        label = s.get("label") or ""
        add = min(need[label] - alloc[label], spare)
        if add > 0:
            alloc[label] += add
            spare -= add
    return alloc


def source_place(src: dict) -> str:
    """交给模型的材料里写清：第几篇、在不在组里、为什么选、来自哪里。"""
    bits: list[str] = []
    if src.get("ordinal"):
        bits.append(f"第{src.get('ordinal')}篇")
    kind = src.get("kind") or ""
    if kind in {"external", "scholar", "web", "web_page"}:
        bits.append("还没放进这个组")
    elif kind == "literature" and src.get("sweep_id"):
        bits.append("组里已有")
    why = str(src.get("why") or "").strip()
    if why:
        bits.append("理由：" + why[:80])
    site = str(src.get("site") or src.get("provider") or "").strip()
    if site:
        bits.append("来自" + site[:40])
    return (" ·" + " ·".join(bits)) if bits else ""


def budget_block(items: list[dict], qtype: str, total: int = MATERIAL_TOKENS) -> str:
    """把来源写成材料：同一篇的放一起，每条按配额截断。给不到 40 token 的只留标题。"""
    alloc = allocate(items, qtype, total)
    papers: dict[str, list[dict]] = {}
    order: list[str] = []
    for s in items:
        key = str(s.get("item_id") or "") or f"~{s.get('label')}"
        if key not in papers:
            papers[key] = []
            order.append(key)
        papers[key].append(s)
    blocks: list[str] = []
    for key in order:
        rows = papers[key]
        lines = []
        if not key.startswith("~") and len(rows) > 1:
            lines.append(f"── 同一篇：《{(rows[0].get('title') or '')[:80]}》")
        for s in rows:
            head = s.get("title") or ""
            page = f" p.{s['page']}" if s.get("page") else ""
            kind = f" ·{s.get('kind_label')}" if s.get("kind_label") else ""
            rel = f" ·{s['relation_label']}" if s.get("relation_label") else ""
            tokens = alloc.get(s.get("label") or "", 0)
            body = _clip_to_tokens(s.get("excerpt") or "", tokens) if tokens >= 40 else ""
            lines.append(f"[{s['label']}] {head}{page}{kind}{rel}{source_place(s)}" + (f"\n{body}" if body else ""))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def relation_label(kind: str) -> str:
    from fengsuan.app.services.relations import KIND_LABEL

    if kind == "own_work":
        return "这个人自己的工作"
    return KIND_LABEL.get(kind, "")


def relation_groups(items: list[dict]) -> list[dict]:
    """给界面按关系分组显示：每组列出来源号。"""
    groups: dict[str, list[str]] = {}
    for s in items:
        kinds = s.get("relations") or ([s["relation"]] if s.get("relation") else [])
        for k in kinds:
            groups.setdefault(k, [])
            if s.get("label") and s["label"] not in groups[k]:
                groups[k].append(s["label"])
    order = ("own_work", "cites", "cited_by", "same_author", "co_cited", "bib_coupled", "topic", "same_direction")
    return [{"kind": k, "label": relation_label(k) or k, "labels": groups[k]} for k in order if groups.get(k)]
