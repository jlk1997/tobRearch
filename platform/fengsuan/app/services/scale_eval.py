"""规模评测：一组自带标准答案的论文，测找人、引用、细节、综述、统计五类题。

- `seed_eval_lab` 在一个组里放 10 篇论文：两个同名的 Wei Zhang（一个有 ORCID、一个没有）、
  互相引用、每篇一段只有它有的正文，分在两个方向里。
- `EVAL_SET` 30 道题，每题写明第一步该走的工具、该找到哪几篇、该带什么关系。
- `run_eval` 直接调路由和工具，不调模型（不花 token），返回四个数：
  route（工具选对的比例）、recall_at_10（细节题前 10 条里有没有对的那篇）、
  people（同名分合对不对）、relation（引用和相关题找到的篇带没带对的关系）。

改了检索、切块、认人或路由，就跑一遍；分数掉了先别上线。
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from fengsuan.app.models.lab import ResearchDirection
from fengsuan.app.models.literature import Author, Authorship, LiteratureDirectionLink, LiteratureItem

WM, ST = "图像水印", "隐写与伪造检测"
TSINGHUA, ZJU, PKU, FUDAN = "Tsinghua University", "Zhejiang University", "Peking University", "Fudan University"
ORCID_WZ = "0000-0001-5000-0001"

PAPERS: list[dict] = [
    {
        "key": "P1", "dir": WM, "year": 2021, "venue": "CVPR",
        "title": "Camera Resilient Watermarking for Printed Images",
        "authors": [("Wei Zhang", TSINGHUA, ORCID_WZ), ("Na Li", TSINGHUA, "")],
        "abstract": "A watermark that survives printing and camera capture.",
        "page": 3, "section": "3 Method",
        "body": "我们用可微噪声层模拟打印拍摄带来的模糊、色偏和透视变化，编码器和解码器端到端训练。",
        "cites": [],
    },
    {
        "key": "P2", "dir": WM, "year": 2022, "venue": "ICCV",
        "title": "Robust Watermark Decoding under Perspective Distortion",
        "authors": [("Wei Zhang", TSINGHUA, ORCID_WZ), ("Hao Chen", PKU, "")],
        "abstract": "Decoding watermarks after strong perspective warps.",
        "page": 4, "section": "4 Geometric Correction",
        "body": "解码前先做几何校正：检测四个角点，把透视畸变拉回正面视角，再读出比特。",
        "cites": ["P1"],
    },
    {
        "key": "P3", "dir": ST, "year": 2020, "venue": "NeurIPS",
        "title": "Deep Steganography with Invertible Networks",
        "authors": [("Wei Zhang", ZJU, ""), ("Bob Stone", ZJU, "")],
        "abstract": "Hiding images inside images with invertible neural networks.",
        "page": 2, "section": "2 Capacity",
        "body": "可逆网络让隐藏容量提高到每像素 24 比特，同时恢复出的秘密图像几乎无损。",
        "cites": [],
    },
    {
        "key": "P4", "dir": WM, "year": 2023, "venue": "CVPR",
        "title": "StegaStamp Revisited: Hyperlinks in Physical Photos",
        "authors": [("Na Li", TSINGHUA, ""), ("Hao Chen", PKU, "")],
        "abstract": "Encoding hyperlinks in physical photographs.",
        "page": 5, "section": "5 Deployment",
        "body": "把超链接编码进实体照片，手机扫描海报就能打开网页，在二十种手机上测试。",
        "cites": ["P1", "P2"],
    },
    {
        "key": "P5", "dir": ST, "year": 2022, "venue": "CVPR",
        "title": "Deepfake Face Detection with Frequency Cues",
        "authors": [("Mei Wang", FUDAN, "")],
        "abstract": "Detecting face forgeries from frequency artifacts.",
        "page": 3, "section": "3 Frequency Artifacts",
        "body": "换脸视频在高频段留下网格状伪影，我们用频域伪影训练检测器，跨数据集也有效。",
        "cites": ["P3"],
    },
    {
        "key": "P6", "dir": WM, "year": 2024, "venue": "TPAMI",
        "title": "A Survey of Image Watermarking",
        "authors": [("Hao Chen", PKU, ""), ("Mei Wang", FUDAN, "")],
        "abstract": "A survey of robust image watermarking.",
        "page": 6, "section": "6 Open Problems",
        "body": "综述把鲁棒水印分成空域、频域和学习型三类，并指出打印拍摄仍是最难的信道。",
        "cites": ["P1", "P2", "P4"],
    },
    {
        "key": "P7", "dir": ST, "year": 2023, "venue": "ECCV",
        "title": "Frequency Domain Forensics for Face Swaps",
        "authors": [("Mei Wang", FUDAN, ""), ("Jun Liu", FUDAN, "")],
        "abstract": "Frequency forensics for face swap videos.",
        "page": 4, "section": "4 Temporal Consistency",
        "body": "除了单帧频谱，我们还检查相邻帧之间的时间一致性，压缩视频上仍能发现换脸。",
        "cites": ["P5"],
    },
    {
        "key": "P8", "dir": WM, "year": 2024, "venue": "AAAI",
        "title": "Learning Template-Free Watermarks",
        "authors": [("Na Li", TSINGHUA, "")],
        "abstract": "Watermarks that need no sync template.",
        "page": 2, "section": "2 Synchronization",
        "body": "不再嵌入同步模板，而是让解码器自己学会对齐，省下的容量用来加纠错码。",
        "cites": ["P2"],
    },
    {
        "key": "P9", "dir": ST, "year": 2021, "venue": "ICLR",
        "title": "Adversarial Attacks on Steganalysis",
        "authors": [("Bob Stone", ZJU, "")],
        "abstract": "Fooling steganalysis detectors with adversarial noise.",
        "page": 3, "section": "3 Attack",
        "body": "在载体图像上加对抗扰动，隐写分析器的检出率从九成掉到一成以下。",
        "cites": ["P3"],
    },
    {
        "key": "P10", "dir": WM, "year": 2023, "venue": "ACM MM",
        "title": "Screen-Shooting Resilient Watermarking",
        "authors": [("Jun Liu", FUDAN, ""), ("Na Li", TSINGHUA, "")],
        "abstract": "Watermarks that survive photographing a screen.",
        "page": 3, "section": "3 Moire",
        "body": "拍屏幕时会出现摩尔纹，我们在训练时加入摩尔纹模拟，让水印在拍屏后还能读出。",
        "cites": ["P1"],
    },
]

PEOPLE = {
    "Wei Zhang": [{"P1", "P2"}, {"P3"}],
    "Na Li": [{"P1", "P4", "P8", "P10"}],
    "Hao Chen": [{"P2", "P4", "P6"}],
    "Mei Wang": [{"P5", "P6", "P7"}],
    "Jun Liu": [{"P7", "P10"}],
    "Bob Stone": [{"P3", "P9"}],
}

T = {p["key"]: p["title"] for p in PAPERS}

EVAL_SET: list[dict] = [
    # 找人
    {"id": "person-1", "kind": "person", "q": "Na Li 还写过什么？", "tool": "get_author_works", "items": {"P1", "P4", "P8", "P10"}},
    {"id": "person-2", "kind": "person", "q": "Wei Zhang 还写过哪些论文？", "tool": "get_author_works", "ask_back": True},
    {"id": "person-3", "kind": "person", "q": f"写《{T['P3']}》的 Wei Zhang 还写过什么？", "tool": "get_author_works", "items": {"P3"}, "not_items": {"P1", "P2"}},
    {"id": "person-4", "kind": "person", "q": f"写《{T['P1']}》的 Wei Zhang 还写过什么？", "tool": "get_author_works", "items": {"P1", "P2"}, "not_items": {"P3"}},
    {"id": "person-5", "kind": "person", "q": "Mei Wang 的论文有哪些？", "tool": "get_author_works", "items": {"P5", "P6", "P7"}},
    {"id": "person-6", "kind": "person", "q": "Hao Chen 的合作者有谁", "tool": "get_author_works", "items": {"P2", "P4", "P6"}},
    {"id": "person-7", "kind": "person", "q": "Bob Stone 发过哪些论文？", "tool": "get_author_works", "items": {"P3", "P9"}},
    {"id": "person-8", "kind": "person", "q": "Jun Liu 写过哪些？", "tool": "get_author_works", "items": {"P7", "P10"}},
    # 引用
    {"id": "cite-1", "kind": "citation", "q": f"《{T['P6']}》引了哪些？", "tool": "get_paper_references", "items": {"P1", "P2", "P4"}, "relation": "cites"},
    {"id": "cite-2", "kind": "citation", "q": f"谁引了《{T['P1']}》？", "tool": "get_citing_papers", "items": {"P2", "P4", "P6", "P10"}, "relation": "cited_by"},
    {"id": "cite-3", "kind": "citation", "q": f"哪些论文引了《{T['P3']}》", "tool": "get_citing_papers", "items": {"P5", "P9"}, "relation": "cited_by"},
    {"id": "cite-4", "kind": "citation", "q": f"《{T['P4']}》的参考文献", "tool": "get_paper_references", "items": {"P1", "P2"}, "relation": "cites"},
    {"id": "cite-5", "kind": "citation", "q": f"谁引了《{T['P5']}》？", "tool": "get_citing_papers", "items": {"P7"}, "relation": "cited_by"},
    {"id": "cite-6", "kind": "citation", "q": f"《{T['P8']}》引了哪些？", "tool": "get_paper_references", "items": {"P2"}, "relation": "cites"},
    {"id": "cite-7", "kind": "citation", "q": f"和《{T['P1']}》相关的论文", "tool": "get_related_papers", "items": {"P2", "P10"}, "relation": "cited_by"},
    {"id": "cite-8", "kind": "citation", "q": f"和《{T['P5']}》相关的论文有哪些", "tool": "get_related_papers", "items": {"P3", "P7"}},
    # 细节在哪一页
    {"id": "passage-1", "kind": "passage", "q": "原文里哪篇提到可微噪声层模拟打印拍摄？", "tool": "search_passages", "passage": "P1"},
    {"id": "passage-2", "kind": "passage", "q": "哪篇里提到透视畸变的几何校正？", "tool": "search_passages", "passage": "P2"},
    {"id": "passage-3", "kind": "passage", "q": "原文怎么说可逆网络的隐藏容量？", "tool": "search_passages", "passage": "P3"},
    {"id": "passage-4", "kind": "passage", "q": "哪篇里提到把超链接编码进实体照片？", "tool": "search_passages", "passage": "P4"},
    {"id": "passage-5", "kind": "passage", "q": "原文里哪篇写过频域伪影训练检测器？", "tool": "search_passages", "passage": "P5"},
    {"id": "passage-6", "kind": "passage", "q": "哪篇里提到相邻帧的时间一致性？", "tool": "search_passages", "passage": "P7"},
    {"id": "passage-7", "kind": "passage", "q": "原文里不嵌入同步模板怎么对齐？", "tool": "search_passages", "passage": "P8"},
    {"id": "passage-8", "kind": "passage", "q": "哪篇里提到对抗扰动让隐写分析器检出率下降？", "tool": "search_passages", "passage": "P9"},
    {"id": "passage-9", "kind": "passage", "q": "原文里拍屏幕的摩尔纹怎么处理？", "tool": "search_passages", "passage": "P10"},
    # 综述
    {"id": "survey-1", "kind": "survey", "q": "总结一下 watermark 的主要方法", "tool": "get_survey_digests", "min_relevant": 4},
    {"id": "survey-2", "kind": "survey", "q": "综述一下 steganography 的脉络", "tool": "get_survey_digests", "min_relevant": 2},
    {"id": "survey-3", "kind": "survey", "q": "face 伪造检测的发展趋势", "tool": "get_survey_digests", "min_relevant": 2},
    # 目录和统计
    {"id": "stats-1", "kind": "stats", "q": "组里有多少篇论文？", "tool": "get_library_stats", "contains": "一共 {total} 篇"},
    {"id": "stats-2", "kind": "stats", "q": "这个组在做什么方向？", "tool": "get_direction_tree"},
]


def seed_eval_lab(db: Session, lab_id: str, user_id: str) -> dict[str, str]:
    """放进 10 篇评测论文，返回 {P1: item_id, ...}。重复调用只放一次。"""
    from fengsuan.app.services.biblio import save_references, sync_authorships
    from fengsuan.app.services.kb import index_literature_structured

    dirs: dict[str, ResearchDirection] = {}
    for name in (WM, ST):
        row = (
            db.query(ResearchDirection)
            .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.name == name, ResearchDirection.deleted_at.is_(None))
            .first()
        )
        if row is None:
            row = ResearchDirection(lab_id=lab_id, name=name)
            db.add(row)
            db.flush()
        dirs[name] = row
    keymap: dict[str, str] = {}
    for p in PAPERS:
        it = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.title == p["title"], LiteratureItem.deleted_at.is_(None))
            .first()
        )
        if it is None:
            it = LiteratureItem(
                lab_id=lab_id,
                title=p["title"],
                abstract=p["abstract"],
                authors_json="[" + ",".join(f'"{a[0]}"' for a in p["authors"]) + "]",
                doi=f"10.5555/scale-eval.{p['key'].lower()}",
                venue=p["venue"],
                publish_year=p["year"],
                added_by=user_id,
                source_channel="manual",
                read_level=1,
            )
            db.add(it)
            db.flush()
            sync_authorships(
                db,
                it,
                [{"name": n, "affiliation": aff, "orcid": orcid} for n, aff, orcid in p["authors"]],
                evidence_source="eval",
            )
            db.add(LiteratureDirectionLink(item_id=it.id, direction_id=dirs[p["dir"]].id))
            pages = [f"{p['title']}\n{p['abstract']}"] + [""] * (p["page"] - 2) + [p["body"]]
            pages = [x or "（空白页）" for x in pages]
            chunks = [
                {"content": f"{p['title']}. {p['abstract']}", "page_no": 1, "kind": "abstract", "section": "Abstract"},
                {"content": p["body"], "page_no": p["page"], "kind": "body", "section": p["section"], "heading_path": p["section"]},
            ]
            index_literature_structured(db, it, pages, chunks)
        keymap[p["key"]] = it.id
    for p in PAPERS:
        if not p["cites"]:
            continue
        it = db.get(LiteratureItem, keymap[p["key"]])
        refs = [
            {"title": T[k], "doi": f"10.5555/scale-eval.{k.lower()}", "year": next(x["year"] for x in PAPERS if x["key"] == k)}
            for k in p["cites"]
        ]
        save_references(db, it, refs, source="grobid")
    db.flush()
    return keymap


def _people_score(db: Session, lab_id: str, keymap: dict[str, str]) -> tuple[float, list[str]]:
    back = {v: k for k, v in keymap.items()}
    wrong = []
    for name, want in PEOPLE.items():
        from fengsuan.app.services.authors import name_key

        rows = (
            db.query(Author.id, Authorship.item_id)
            .join(Authorship, Authorship.author_id == Author.id)
            .filter(Author.lab_id == lab_id, Author.name_key == name_key(name), Author.merged_into.is_(None))
            .all()
        )
        got: dict[str, set[str]] = {}
        for aid, iid in rows:
            if iid in back:
                got.setdefault(aid, set()).add(back[iid])
        if sorted(map(sorted, got.values())) != sorted(map(sorted, want)):
            wrong.append(f"{name}: {sorted(map(sorted, got.values()))}")
    return round(1 - len(wrong) / len(PEOPLE), 3), wrong


def run_eval(db: Session, lab_id: str, user_id: str, keymap: dict[str, str], *, role: str = "teacher") -> dict:
    """跑一遍评测集。返回总分和每道没过的题，方便对照。"""
    from fengsuan.app.services.agent.registry import ToolContext, get_tool, stub_plan
    from fengsuan.app.services.kb import query_kb

    from sqlalchemy import func

    back = {v: k for k, v in keymap.items()}
    total = (
        db.query(func.count(LiteratureItem.id))
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .scalar()
    )
    route_ok = recall_hits = top3_hits = recall_n = rel_ok = rel_n = 0
    misses: list[dict] = []
    timings: list[float] = []
    for case in EVAL_SET:
        plan = stub_plan("lab", case["q"])
        first = plan[0] if plan else ""
        ok_route = first == case["tool"]
        route_ok += int(ok_route)
        spec = get_tool(case["tool"])
        ctx = ToolContext(db=db, user_id=user_id, lab_id=lab_id, scope="lab", role=role, extra={"question": case["q"]})
        args = {"query": case["q"]} if case["tool"] in {"search_passages", "get_survey_digests"} else {}
        t0 = time.perf_counter()
        res = spec.fn(ctx, args) if spec else None
        timings.append(time.perf_counter() - t0)
        problems = [] if ok_route else [f"路由到 {first or '无'}"]
        if res is None or res.error:
            problems.append(f"工具出错：{getattr(res, 'error', '找不到工具')}")
        else:
            found = {back[s.get("item_id")] for s in res.sources if s.get("item_id") in back}
            if case.get("ask_back") and not (res.data or {}).get("ambiguous"):
                problems.append("同名没有反问")
            if case.get("items") and not case["items"] <= found:
                problems.append(f"少了 {sorted(case['items'] - found)}")
            if case.get("not_items") and case["not_items"] & found:
                problems.append(f"混进了 {sorted(case['not_items'] & found)}")
            if case.get("relation"):
                rel_n += 1
                tagged = {
                    back[s["item_id"]]
                    for s in res.sources
                    if s.get("item_id") in back
                    and case["relation"] in (s.get("relations") or [s.get("relation")])
                }
                if case["items"] <= tagged:
                    rel_ok += 1
                else:
                    problems.append(f"关系没标上 {sorted(case['items'] - tagged)}")
            if case.get("min_relevant") and int((res.data or {}).get("relevant") or 0) < case["min_relevant"]:
                problems.append(f"只找到 {(res.data or {}).get('relevant')} 篇")
            want_text = (case.get("contains") or "").replace("{total}", str(total))
            if want_text and want_text not in (res.text or ""):
                problems.append(f"没说「{want_text}」")
        if case.get("passage"):
            recall_n += 1
            hits, _ = query_kb(db, lab_id, case["q"], top_k=10, user_id=user_id, source_kinds=["paper"])
            top = [back.get(h.get("literature_item_id") or "") for h in hits[:10]]
            if case["passage"] in top:
                recall_hits += 1
            if case["passage"] in top[:3]:
                top3_hits += 1
            else:
                problems.append(f"前 10 条没有 {case['passage']}")
        if problems:
            misses.append({"id": case["id"], "q": case["q"], "problems": problems})
    people, people_wrong = _people_score(db, lab_id, keymap)
    timings.sort()
    return {
        "questions": len(EVAL_SET),
        "route": round(route_ok / len(EVAL_SET), 3),
        "recall_at_10": round(recall_hits / recall_n, 3) if recall_n else None,
        "recall_at_3": round(top3_hits / recall_n, 3) if recall_n else None,
        "people": people,
        "relation": round(rel_ok / rel_n, 3) if rel_n else None,
        "passed": len(EVAL_SET) - len(misses),
        "tool_p95_ms": round(1000 * timings[int(0.95 * (len(timings) - 1))], 1) if timings else None,
        "misses": misses,
        "people_wrong": people_wrong,
    }
