"""Agent 运行时评测夹具。

这是「世界级」与「能跑」的分水岭：阈值进 CI，回归即失败。
跑在 stub 模型下，所以衡量的是运行时的工具选择 / 接地 / 预算 / 拒答，
不是某个云端模型的水平。换模型不影响这几条底线。
"""

from __future__ import annotations

import json
from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.agent import agent_answer, budget_for, list_tools
from fengsuan.app.services.agent.loop import _Sources, ground_by_source_id
from fengsuan.app.services.agent.registry import normalize_confirm_tools, stub_plan

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "agent_golden" / "cases.json").read_text(encoding="utf-8"))

PAGES = [
    "Introduction. We study graph convolution for node classification on citation networks.",
    "Method. Our model stacks two graph convolution layers with symmetric normalization and dropout 0.5.",
    "Experiments. On Cora the model reaches 81.5 accuracy, above the GCN baseline of 79.0.",
]


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _world(client) -> tuple[str, str, str]:
    """一个够回答八个 golden 问题的最小实验室。"""
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agenteval", "code": "agenteval", "description": "图学习"}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]

    d = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "图神经网络", "description": "节点分类与图卷积", "keywords": ["graph", "gnn"]},
        headers=h,
    )
    assert d.status_code == 200, d.text
    direction_id = d.json()["id"]

    ms = client.post(
        f"/api/v1/labs/{lab_id}/directions/{direction_id}/milestones",
        json={"kind": "proposal", "title": "开题报告"},
        headers=h,
    )
    assert ms.status_code in {200, 201}, ms.text

    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={
            "title": "Graph convolution for node classification",
            "abstract": "We study graph convolution.",
            "language": "en",
            "authors": ["Thomas N. Kipf", "Max Welling"],
            "venue": "ICLR",
            "publish_year": 2017,
            "doi": "10.48550/arXiv.1606.04615",
            "arxiv_id": "1606.04615",
            "pubmed_id": "28875934",
            "direction_ids": [direction_id],
        },
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]

    ds = client.post(
        f"/api/v1/labs/{lab_id}/datasets",
        json={"name": "Cora citation", "description": "节点分类", "literature_item_ids": [item_id]},
        headers=h,
    )
    assert ds.status_code == 200, ds.text
    ds_id = ds.json()["id"]

    client.post(
        f"/api/v1/labs/{lab_id}/kb/documents",
        files={"file": ("intro.md", "\n".join(PAGES).encode("utf-8"), "text/markdown")},
        headers=h,
    )

    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureFile
        from fengsuan.app.services.pagecache import store_pages

        f = LiteratureFile(
            item_id=item_id,
            file_path=f"/tmp/{item_id}.pdf",
            filename="paper.pdf",
            mime="application/pdf",
            size=1024,
            parse_status="parsed",
        )
        db.add(f)
        db.flush()
        store_pages(db, f, PAGES)
        f.structure_json = json.dumps(
            {
                "title": "Graph convolution for node classification",
                "sections": [
                    {"title": "Introduction", "page": 1},
                    {"title": "Method", "page": 2},
                    {"title": "Experiments", "page": 3},
                ],
                "figures": [
                    {"label": "Figure 1", "page": 3, "caption": "Cora accuracy"},
                ],
                "equations": [
                    {"label": "Equation 1", "page": 2, "caption": "symmetric normalization"},
                ],
                "algorithms": [
                    {"label": "Algorithm 1", "page": 2, "caption": "two-layer GCN"},
                ],
            },
            ensure_ascii=False,
        )
        from fengsuan.app.db.base import utcnow
        from fengsuan.app.models.activity import WorkLog
        from fengsuan.app.models.identity import User
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation, ManuscriptComment, ManuscriptDirectionLink
        from fengsuan.app.models.runs import ResearchRun

        link = (
            db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.item_id == item_id, LiteratureDirectionLink.direction_id == direction_id)
            .first()
        )
        if link is None:
            db.add(LiteratureDirectionLink(item_id=item_id, direction_id=direction_id, is_core=True))
        else:
            link.is_core = True
        admin = db.query(User).filter(User.email == "admin@example.com").first()
        uid = admin.id if admin is not None else ""
        if uid:
            ms = Manuscript(
                lab_id=lab_id,
                owner_id=uid,
                title="GNN 综述稿",
                content_md="We cite Graph convolution for node classification as the core method.",
                status="draft",
            )
            db.add(ms)
            db.flush()
            db.add(ManuscriptDirectionLink(manuscript_id=ms.id, direction_id=direction_id))
            db.add(
                ManuscriptCitation(
                    manuscript_id=ms.id,
                    literature_item_id=item_id,
                    citation_key="gcn",
                )
            )
            db.add(
                ManuscriptComment(
                    manuscript_id=ms.id,
                    user_id=uid,
                    body="方法节还没点名核心文献。",
                )
            )
            db.add(
                ResearchRun(
                    lab_id=lab_id,
                    requester_id=uid,
                    question="图卷积综述",
                    status="succeeded",
                )
            )
            db.add(
                ResearchRun(
                    lab_id=lab_id,
                    requester_id=uid,
                    question="节点分类实验",
                    status="failed",
                    error="worker timeout",
                    dataset_id=ds_id,
                )
            )
            db.add(
                WorkLog(
                    lab_id=lab_id,
                    user_id=uid,
                    log_date=utcnow(),
                    content="本周读完图卷积。",
                )
            )
            db.add(
                LiteratureAnalysis(
                    item_id=item_id,
                    analysis_type="tutor",
                    created_by=uid,
                    result_json=json.dumps(
                        {
                            "question": "这一页的方法怎么说的？",
                            "answer": "两层图卷积加 dropout 0.5。",
                            "page_no": 2,
                            "session_id": "evaltutor",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        db.commit()
    finally:
        db.close()

    card = client.post(f"/api/v1/literature/{item_id}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert card.status_code == 200, card.text
    marked = client.post(f"/api/v1/literature/{item_id}/mark-read", json={"status": "reading"}, headers=h)
    assert marked.status_code == 200, marked.text
    prog = client.post(
        f"/api/v1/literature/{item_id}/progress",
        json={"page_no": 2, "page_count": 3, "progress_pct": 66},
        headers=h,
    )
    assert prog.status_code == 200, prog.text
    gloss = client.post(
        f"/api/v1/labs/{lab_id}/glossary",
        json={"src": "GCN", "tgt": "图卷积网络", "do_not_translate": False, "lang_pair": "en-zh"},
        headers=h,
    )
    assert gloss.status_code == 200, gloss.text
    task = client.post(
        f"/api/v1/labs/{lab_id}/tasks",
        json={"title": "读完图卷积", "description": "本周看板"},
        headers=h,
    )
    assert task.status_code == 200, task.text
    inv = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        json={"email": "zhangsan@example.com", "role": "student"},
        headers=h,
    )
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": "zhangsan@example.com",
            "password": "student99",
            "display_name": "张三",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    zhang = client.get("/api/v1/auth/me", headers=_h(reg.json()["access"]))
    assert zhang.status_code == 200, zhang.text
    zhang_task = client.post(
        f"/api/v1/labs/{lab_id}/tasks",
        json={"title": "张三读方法卡", "assignee_id": zhang.json()["id"]},
        headers=h,
    )
    assert zhang_task.status_code == 200, zhang_task.text
    meet = client.post(
        f"/api/v1/labs/{lab_id}/meetings",
        json={"title": "开题预演组会", "notes_raw": "下周开题预演。"},
        headers=h,
    )
    assert meet.status_code == 200, meet.text
    return lab_id, item_id, direction_id


def _admin_user_id(db) -> str:
    from fengsuan.app.models.identity import User

    row = db.query(User).filter(User.email == "admin@example.com").first()
    assert row is not None
    return row.id


def test_agent_tool_choice_and_citation_traceability(client):
    lab_id, item_id, _direction_id = _world(client)
    db = SessionLocal()
    try:
        user_id = _admin_user_id(db)
        cases = GOLDEN["cases"]
        thresholds = GOLDEN["thresholds"]
        chose_ok = 0
        traceable_total = 0
        traceable_ok = 0
        max_steps = 0
        for case in cases:
            out = agent_answer(
                db,
                user_id=user_id,
                lab_id=lab_id,
                scope=case["scope"],
                question=case["question"],
                feature="tutor",
                item_id=item_id,
                page_no=2,
            )
            used = {s["tool"] for s in out["steps"] if s.get("tool")}
            if used & set(case["expect_tools_any"]):
                chose_ok += 1
            max_steps = max(max_steps, len(out["steps"]))
            source_ids = {s["id"] for s in out["sources"]}
            for cite in out["citations"]:
                traceable_total += 1
                if cite["source_id"] in source_ids:
                    traceable_ok += 1
            if case.get("expect_sources"):
                # D-HONEST：源限流时允许没有出处，但必须写明是没查成，不能装作「没有」。
                degraded = any("退避" in (s.get("result_summary") or "") or "没连上" in (s.get("result_summary") or "") for s in out["steps"])
                assert out["sources"] or degraded, f"{case['id']} 应当取到来源，实际为空：{out['steps']}"

        accuracy = chose_ok / len(cases)
        assert accuracy >= thresholds["tool_choice_accuracy"], f"工具选择正确率 {accuracy}"
        if traceable_total:
            rate = traceable_ok / traceable_total
            assert rate >= thresholds["citation_traceable"], f"引用可追溯率 {rate}"
        assert max_steps <= thresholds["max_steps_observed"], f"步数 {max_steps} 超预算"
    finally:
        db.close()


def test_agent_refuses_when_corpus_is_empty(client):
    lab_id, item_id, _d = _world(client)
    db = SessionLocal()
    try:
        user_id = _admin_user_id(db)
        for case in GOLDEN["refusal_cases"]:
            out = agent_answer(
                db,
                user_id=user_id,
                lab_id=lab_id,
                scope=case["scope"],
                question=case["question"],
                feature="tutor",
                item_id=item_id,
            )
            assert not out["sources"], f"{case['id']} 不该检索到材料：{out['sources']}"
            assert "查不到" in out["answer"], f"{case['id']} 必须明确拒答：{out['answer']}"
            assert out["citations"] == []
    finally:
        db.close()


def test_agent_trace_is_replayable(client):
    lab_id, item_id, _d = _world(client)
    db = SessionLocal()
    try:
        from fengsuan.app.services.agent.trace import session_steps

        user_id = _admin_user_id(db)
        out = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="paper",
            question="这一页的方法怎么说的？",
            feature="tutor",
            item_id=item_id,
            page_no=2,
        )
        rows = session_steps(db, out["session_id"])
        assert rows, "追踪为空，无法复算答案来源"
        assert any(r["kind"] == "tool" for r in rows)
        assert rows[-1]["kind"] == "answer"
        tool_rows = [r for r in rows if r["kind"] == "tool"]
        assert all(r["latency_ms"] >= 0 for r in tool_rows)
        assert any((s.get("label") or "") == "取页正文" for s in out.get("steps") or []), out.get("steps")
        src = next((s for s in out.get("sources") or [] if s.get("href")), None)
        assert src, "来源必须带可点链接"
        assert src.get("kind_label") or src.get("kind")
        assert out.get("followups"), "答完应给出可点的下一问"
    finally:
        db.close()


def test_named_dataset_glossary_run_and_followups(client):
    """点名数据 / 术语 / 作业编号要对上；对不上走空态下一步，不要拿最近一条顶上。"""
    lab_id, item_id, _d = _world(client)
    db = SessionLocal()
    try:
        from fengsuan.app.models.runs import ResearchRun

        user_id = _admin_user_id(db)
        ds = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="Kipf 那篇用了哪份数据？",
            feature="ask",
        )
        assert any("Cora" in (s.get("title") or "") for s in ds.get("sources") or []), ds.get("sources")
        assert ds.get("followups")

        by_run = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="节点分类实验用了哪份数据？",
            feature="ask",
        )
        assert any("Cora" in (s.get("title") or "") for s in by_run.get("sources") or []), by_run.get("sources")

        eq = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="paper",
            question="公式1在第几页",
            feature="tutor",
            item_id=item_id,
            page_no=3,
        )
        assert any((s.get("page") == 2) and ("Equation" in (s.get("title") or "") or "公式" in (s.get("excerpt") or "") or s.get("page") == 2) for s in eq.get("sources") or []), eq.get("sources")

        files = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="syllabus.pdf 这份文件写了什么？",
            feature="ask",
        )
        fnxt = files.get("empty_next") or {}
        assert "没有对上" in (fnxt.get("title") or "") or any(
            "没有对上" in ((s.get("empty_next") or {}).get("title") or "") for s in files.get("steps") or []
        ), (files.get("empty_next"), files.get("sources"))

        gloss = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="GraphSAGE 这个词怎么译？",
            feature="ask",
        )
        nxt = gloss.get("empty_next") or {}
        assert "术语表没有" in (nxt.get("title") or "") or any(
            (s.get("empty_next") or {}).get("title", "").startswith("术语表没有") for s in gloss.get("steps") or []
        ), (gloss.get("empty_next"), gloss.get("steps"), gloss.get("answer"))

        miss = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="ImageNet 这份数据在哪？",
            feature="ask",
        )
        miss_nxt = miss.get("empty_next") or {}
        assert "没有对上" in (miss_nxt.get("title") or "") or any(
            "没有对上" in ((s.get("empty_next") or {}).get("title") or "") for s in miss.get("steps") or []
        ), (miss.get("empty_next"), miss.get("sources"), miss.get("answer"))
        assert not any("Cora" in (s.get("title") or "") for s in miss.get("sources") or [])

        pinned = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="节点分类实验还在排队吗？",
            feature="ask",
        )
        titles = [s.get("title") or "" for s in pinned.get("sources") or []]
        assert any("节点分类" in t for t in titles), titles

        failed = (
            db.query(ResearchRun)
            .filter(ResearchRun.lab_id == lab_id, ResearchRun.question == "节点分类实验")
            .first()
        )
        assert failed is not None
        by_id = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question=f"作业 {failed.id} 卡在哪？",
            feature="ask",
        )
        assert any(
            failed.id in str(s.get("id") or "") or failed.id in str(s.get("href") or "")
            for s in by_id.get("sources") or []
        ), by_id.get("sources")
        ghost = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="lab",
            question="作业 deadbeef12 卡在哪？",
            feature="ask",
        )
        ghost_nxt = ghost.get("empty_next") or {}
        assert "没有对上" in (ghost_nxt.get("title") or "") or any(
            "没有对上" in ((s.get("empty_next") or {}).get("title") or "") for s in ghost.get("steps") or []
        ), (ghost.get("empty_next"), ghost.get("sources"))
    finally:
        db.close()


def test_agent_scope_isolation_and_write_confirm(client):
    """权限域在工具内部强制：paper 域没有跨篇检索，写入类必须确认。"""
    lab_id, item_id, _d = _world(client)
    paper_tools = {t.name for t in list_tools("paper")}
    onboarding_tools = {t.name for t in list_tools("onboarding")}
    assert "search_open_sources" not in paper_tools
    assert "writeback_manuscript" not in paper_tools
    assert "get_lab_members" not in paper_tools
    assert "write_note" not in onboarding_tools
    assert "submit_research_run" not in onboarding_tools
    assert "get_lab_runs" not in paper_tools
    assert "get_unused_research" not in onboarding_tools
    assert "get_manuscript_brief" not in paper_tools
    assert "get_core_coverage" not in paper_tools
    assert "get_lab_datasets" not in paper_tools
    assert "get_run_detail" not in paper_tools
    assert "get_related_work" not in paper_tools
    assert "get_related_work" not in onboarding_tools
    assert "get_honesty_brief" not in paper_tools
    assert "get_citation_links" in paper_tools
    assert "get_lab_activity" not in paper_tools
    assert "get_lab_glossary" not in paper_tools
    assert "get_lab_tasks" not in paper_tools
    assert "get_lab_glossary" in onboarding_tools
    assert "get_lab_tasks" in onboarding_tools
    assert "get_lab_kb_docs" not in paper_tools
    assert "get_lab_files" not in paper_tools
    assert "get_manuscript_comments" not in paper_tools
    assert "get_page_translation" in paper_tools
    assert "get_my_reading" in paper_tools
    assert "get_manuscript_versions" not in paper_tools
    assert "get_job_preflight" in paper_tools
    assert "get_job_preflight" in onboarding_tools
    assert "get_job_preflight" in {t.name for t in list_tools("lab")}
    assert "get_job_preflight" in {t.name for t in list_tools("writing")}
    assert "get_paper_biblio" in paper_tools
    assert "get_paper_biblio" not in onboarding_tools
    assert "get_paper_biblio" in {t.name for t in list_tools("writing")}
    assert "get_lab_literature" not in paper_tools
    assert "get_lab_literature" in onboarding_tools
    assert "get_lab_literature" in {t.name for t in list_tools("lab")}
    assert "get_lab_literature" in {t.name for t in list_tools("writing")}
    assert "get_my_tutor_history" in paper_tools
    assert "get_my_tutor_history" not in onboarding_tools
    assert "get_my_tutor_history" in {t.name for t in list_tools("lab")}
    assert "get_lab_kb_docs" in onboarding_tools
    assert "get_lab_files" in onboarding_tools
    assert "get_page_translation" not in onboarding_tools
    assert "get_my_next_step" in {t.name for t in list_tools("lab")}

    db = SessionLocal()
    try:
        user_id = _admin_user_id(db)
        out = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="paper",
            question="提交复现这篇",
            feature="tutor",
            item_id=item_id,
            seed_tools=[("submit_reproduce_run", {})],
        )
        assert out["pending_confirm"], "重型作业必须先确认，不允许静默烧算力"
        assert out["pending_confirm"][0]["tool"] == "submit_reproduce_run"
    finally:
        db.close()


def test_agent_cross_lab_access_is_denied(client):
    """越库访问由工具挡住，不靠提示词。"""
    lab_a, item_a, _d = _world(client)
    h = _h(_admin(client))
    other = client.post("/api/v1/labs", json={"name": "agentevalb", "code": "agentevalb", "description": ""}, headers=h)
    assert other.status_code == 200, other.text
    lab_b = other.json()["id"]
    db = SessionLocal()
    try:
        user_id = _admin_user_id(db)
        out = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_b,
            scope="paper",
            question="这一页说了什么？",
            feature="tutor",
            item_id=item_a,
            page_no=1,
            seed_tools=[("get_paper_page_text", {"page_no": 1})],
        )
        errs = [s for s in out["steps"] if s.get("error")]
        assert errs, "跨库取页必须失败"
        assert "不属于本实验室" in errs[0]["error"]
        assert not out["sources"]
    finally:
        db.close()


def test_grounding_marks_uncited_sentences():
    src = _Sources()
    src.add({"id": "page:x:1", "kind": "page", "title": "T", "page": 1, "excerpt": "abc"})
    out = ground_by_source_id("模型在 Cora 上达到 81.5 [S1]。这个方法一定能推广到所有图数据集上。", src)
    assert out["citations"][0]["source_id"] == "page:x:1"
    assert out["ungrounded"], "没有来源的断言必须被标出来"
    assert out["grounded_ratio"] < 1.0
    clean = ground_by_source_id("模型在 Cora 上达到 81.5 [S1]。", src)
    assert clean["ungrounded"] == []
    assert clean["grounded_ratio"] == 1.0
    fake = ground_by_source_id("引用一个不存在的来源 [S9]。", src)
    assert fake["citations"] == []
    assert fake["ungrounded"]


def test_stub_plan_is_deterministic_and_scoped():
    assert stub_plan("paper", "这一页的方法怎么说的？")[0] == "get_paper_page_text"
    assert stub_plan("onboarding", "这个组在做什么方向？")[0] == "get_direction_tree"
    assert "get_lab_members" in stub_plan("onboarding", "谁负责什么，哪位同学能帮我？")
    assert stub_plan("paper", "随便问问") == ["get_paper_page_text"]
    assert stub_plan("paper", "提交复现这篇")[0] == "get_job_preflight"
    assert "submit_reproduce_run" in stub_plan("paper", "提交复现这篇")
    assert stub_plan("lab", "提交调研：图卷积")[0] == "get_job_preflight"
    assert "submit_research_run" in stub_plan("lab", "提交调研：图卷积")
    assert stub_plan("lab", "现在能提交吗，工人开了吗")[0] == "get_job_preflight"
    assert stub_plan("paper", "提交前还缺什么")[0] == "get_job_preflight"
    assert stub_plan("lab", "这份数据被哪次作业用了？")[0] == "get_lab_datasets"
    assert stub_plan("paper", "哪些主张没依据？")[0] == "get_method_card"
    assert stub_plan("paper", "这篇DOI是什么，哪年发的？")[0] == "get_paper_biblio"
    assert stub_plan("lab", "这个月token还剩多少？")[0] == "get_job_preflight"
    assert stub_plan("writing", "稿里哪些引用还没实测？")[0] == "get_manuscript_brief"
    assert stub_plan("paper", "这篇复现跑通了吗，有实测吗？")[0] == "get_reproduce_evidence"
    assert stub_plan("paper", "这篇我读到哪了？")[0] == "get_my_reading"
    assert stub_plan("lab", "组里有什么数据、用哪份？")[0] == "get_lab_datasets"
    assert stub_plan("onboarding", "我该从哪几篇文献读起？")[0] == "get_starter_reading"
    assert stub_plan("onboarding", "组里在写什么手稿？")[0] == "get_lab_manuscripts"
    assert stub_plan("paper", "记下来：方法是剪枝")[0] == "write_note"
    assert stub_plan("paper", "我写过什么批注？")[0] == "get_my_notes"
    assert stub_plan("paper", "高亮这段")[0] == "write_highlight"
    assert stub_plan("onboarding", "最近组会什么时候开？")[0] == "get_recent_meetings"
    assert stub_plan("onboarding", "组会纪要写了什么")[0] == "search_lab_kb"
    assert stub_plan("onboarding", "我的档案里写了什么兴趣？")[0] == "get_member_profile"
    assert stub_plan("onboarding", "我的导师是谁？")[0] == "get_member_profile"
    assert stub_plan("onboarding", "我该做什么？")[0] == "get_my_next_step"
    assert stub_plan("paper", "这篇谁在读？")[0] == "get_paper_readers"
    assert stub_plan("onboarding", "组里谁在读？")[0] == "get_paper_readers"
    assert stub_plan("lab", "综述作业跑完了吗，还在排队吗？")[0] == "get_lab_runs"
    assert stub_plan("lab", "还有没用上的综述结果吗？")[0] == "get_unused_research"
    assert stub_plan("lab", "这几篇方法差在哪？")[0] == "get_method_compare"
    assert stub_plan("writing", "这篇稿引用了谁？")[0] == "get_manuscript_brief"
    assert stub_plan("writing", "核心文献写进正文了吗？")[0] == "get_core_coverage"
    assert stub_plan("lab", "组里有哪些文献？")[0] == "get_lab_literature"
    assert stub_plan("lab", "核心文献有哪些？")[0] == "get_lab_literature"
    assert stub_plan("onboarding", "我还没读哪些？")[0] == "get_lab_literature"
    assert stub_plan("lab", "看板上有没有过期任务？")[0] == "get_lab_tasks"
    assert stub_plan("paper", "上次辅导我说了什么？")[0] == "get_my_tutor_history"
    assert stub_plan("lab", "复现还在等算力吗？")[0] == "get_lab_runs"
    assert stub_plan("lab", "现在能提交吗，工人开了吗")[0] == "get_job_preflight"
    assert stub_plan("lab", "节点分类实验为什么失败？")[0] == "get_run_detail"
    assert stub_plan("lab", "我的稿在写什么？")[0] == "get_lab_manuscripts"
    assert stub_plan("writing", "这篇稿引用了谁？")[0] == "get_manuscript_brief"
    assert stub_plan("lab", "GCN 这个词怎么译？")[0] == "get_lab_glossary"
    assert stub_plan("lab", "我的周志写了什么？")[0] == "get_lab_activity"
    assert stub_plan("lab", "组里2017年有哪些文献？")[0] == "get_lab_literature"
    assert stub_plan("lab", "核心文献写进正文了吗？")[0] == "get_core_coverage"
    assert stub_plan("lab", "Kipf 那篇哪年发的？")[0] == "get_paper_biblio"
    assert stub_plan("paper", "这篇DOI是什么，哪年发的？")[0] == "get_paper_biblio"
    assert stub_plan("lab", "ICLR 会上有哪些？")[0] == "get_lab_literature"
    assert stub_plan("paper", "这一页的方法怎么说的？")[0] == "get_paper_page_text"
    assert stub_plan("paper", "方法在第几页，帮我定位一下章节")[0] == "get_paper_structure"
    assert stub_plan("paper", "我读到第几页了？")[0] == "get_my_reading"
    assert stub_plan("paper", "第2页怎么说的？")[0] == "get_paper_page_text"
    assert stub_plan("paper", "图1在第几页")[0] == "get_paper_structure"
    assert stub_plan("lab", "1606.04615 那篇是哪篇？")[0] == "get_paper_biblio"
    assert stub_plan("lab", "组里2017年有哪些文献？")[0] == "get_lab_literature"
    assert stub_plan("lab", "gcn 那篇是哪篇？")[0] == "get_paper_biblio"
    assert stub_plan("lab", "PMID 28875934 是哪篇？")[0] == "get_paper_biblio"
    assert stub_plan("paper", "81.5 是哪页说的？")[0] == "get_paper_page_text"
    assert stub_plan("paper", "第2节在哪")[0] == "get_paper_structure"
    assert stub_plan("lab", "1606.04615 谁在读？")[0] == "get_paper_readers"
    assert stub_plan("lab", "1606.04615 我读到哪了？")[0] == "get_my_reading"
    assert stub_plan("lab", "Kipf 那篇被谁引用？")[0] == "get_citation_links"
    assert stub_plan("lab", "上次辅导 Kipf 那篇说了什么？")[0] == "get_my_tutor_history"
    assert stub_plan("lab", "Kipf 那篇用了哪份数据？")[0] == "get_lab_datasets"
    assert stub_plan("lab", "1606.04615 用了哪份数据？")[0] == "get_lab_datasets"
    assert stub_plan("lab", "GraphSAGE 这个词怎么译？")[0] == "get_lab_glossary"
    assert stub_plan("lab", "ImageNet 这份数据在哪？")[0] == "get_lab_datasets"
    assert stub_plan("lab", "节点分类实验还在排队吗？")[0] == "get_lab_runs"
    assert stub_plan("lab", "作业 deadbeef 卡在哪？")[0] == "get_run_detail"
    assert stub_plan("lab", "1606.04615 那篇是哪篇？")[0] == "get_paper_biblio"
    assert stub_plan("lab", "张三的任务有哪些？")[0] == "get_lab_tasks"
    assert stub_plan("lab", "那个任务为什么失败、卡在哪？")[0] == "get_run_detail"
    assert stub_plan("lab", "图神经网络还没做完什么？")[0] == "get_honesty_brief"
    assert stub_plan("lab", "张三的档案写了什么？")[0] == "get_member_profile"
    assert stub_plan("onboarding", "我的档案里写了什么兴趣？")[0] == "get_member_profile"
    assert stub_plan("onboarding", "开题预演组会定了什么？")[0] == "get_recent_meetings"
    assert stub_plan("lab", "张三的稿在写什么？")[0] == "get_lab_manuscripts"
    assert stub_plan("writing", "这篇稿引用了谁？")[0] == "get_manuscript_brief"
    assert stub_plan("onboarding", "我该做什么？")[0] == "get_my_next_step"
    assert stub_plan("paper", "我写过什么批注？")[0] == "get_my_notes"
    assert stub_plan("lab", "那个任务为什么失败、卡在哪？")[0] == "get_run_detail"
    assert stub_plan("lab", "组里有什么数据、用哪份？")[0] == "get_lab_datasets"
    assert stub_plan("onboarding", "组里有什么数据、用哪份？")[0] == "get_lab_datasets"
    assert stub_plan("writing", "相关工作该写谁？")[0] == "get_related_work"
    assert stub_plan("lab", "组里还没做完什么，哪篇把没复现写成成功了？")[0] == "get_honesty_brief"
    assert stub_plan("paper", "这篇被哪篇稿引用了？")[0] == "get_citation_links"
    assert stub_plan("lab", "这篇被谁引用，引用关系是什么？")[0] == "get_citation_links"
    assert stub_plan("lab", "组里最近干什么了？")[0] == "get_lab_activity"
    assert stub_plan("onboarding", "最近工作日志写了什么？")[0] == "get_lab_activity"
    assert stub_plan("lab", "组里术语怎么译，哪些锁定不译？")[0] == "get_lab_glossary"
    assert stub_plan("writing", "这个词锁定不译吗？")[0] == "get_lab_glossary"
    assert stub_plan("lab", "看板上我的任务有哪些？")[0] == "get_lab_tasks"
    assert stub_plan("onboarding", "组会派给我的待办任务？")[0] == "get_lab_tasks"
    assert stub_plan("onboarding", "我该做什么？")[0] == "get_my_next_step"
    assert stub_plan("lab", "那个任务为什么失败、卡在哪？")[0] == "get_run_detail"
    assert stub_plan("lab", "这次作业产出了哪些产物文件？")[0] == "get_run_detail"
    assert stub_plan("paper", "这一页中文怎么说？")[0] == "get_page_translation"
    assert stub_plan("paper", "这一页的方法怎么说的？")[0] == "get_paper_page_text"
    assert stub_plan("lab", "知识库有哪些文档，索引好了吗？")[0] == "get_lab_kb_docs"
    assert stub_plan("lab", "组里共享文件在文件柜吗？")[0] == "get_lab_files"
    assert stub_plan("writing", "这篇稿导师批了什么？")[0] == "get_manuscript_comments"
    assert stub_plan("writing", "这篇稿引用了谁？")[0] == "get_manuscript_brief"
    assert stub_plan("onboarding", "组会纪要写了什么")[0] == "search_lab_kb"
    assert stub_plan("onboarding", "那次组会定了什么行动？")[0] == "get_recent_meetings"
    assert stub_plan("onboarding", "组会派给我的待办任务？")[0] == "get_lab_tasks"
    assert stub_plan("paper", "这篇我读到哪了？")[0] == "get_my_reading"
    assert stub_plan("paper", "这篇谁在读？")[0] == "get_paper_readers"
    assert stub_plan("writing", "这篇稿改了几版？")[0] == "get_manuscript_versions"
    assert stub_plan("writing", "这篇稿引用了谁？")[0] == "get_manuscript_brief"
    assert stub_plan("lab", "我该做什么？")[0] == "get_my_next_step"
    assert stub_plan("onboarding", "我该从哪几篇文献读起？")[0] == "get_starter_reading"
    assert stub_plan("lab", "Kipf 那篇用了哪份数据？")[0] == "get_lab_datasets"
    assert stub_plan("lab", "1606.04615 用了哪份数据？")[0] == "get_lab_datasets"
    assert stub_plan("lab", "GraphSAGE 这个词怎么译？")[0] == "get_lab_glossary"
    assert stub_plan("lab", "ImageNet 这份数据在哪？")[0] == "get_lab_datasets"
    assert stub_plan("lab", "节点分类实验还在排队吗？")[0] == "get_lab_runs"
    assert stub_plan("lab", "作业 deadbeef 卡在哪？")[0] == "get_run_detail"
    assert stub_plan("paper", "公式1在第几页")[0] == "get_paper_structure"
    assert stub_plan("paper", "算法1在第几页")[0] == "get_paper_structure"
    assert stub_plan("onboarding", "开题报告什么时候？")[0] == "get_milestones"
    assert stub_plan("lab", "syllabus.pdf 这份文件写了什么？")[0] == "get_lab_files"
    assert stub_plan("lab", "节点分类实验用了哪份数据？")[0] == "get_lab_datasets"
    for scope in ("paper", "lab", "onboarding", "writing"):
        picked = stub_plan(scope, "方法 成员 方向 复现 知识库 文献")
        names = {t.name for t in list_tools(scope, include_writes=True)}
        assert set(picked) <= names, f"{scope} 选到了域外工具：{picked}"
        writes = {t.name for t in list_tools(scope) if t.writes}
        assert not (set(picked) & writes), f"{scope} 未点名却选了写入类：{picked}"
    assert normalize_confirm_tools("paper", [{"tool": "get_paper_page_text", "args": {}}]) == []
    assert normalize_confirm_tools("paper", [{"tool": "write_note", "args": {"content_md": "x"}}])[0][0] == "write_note"
    assert normalize_confirm_tools("onboarding", [{"tool": "write_note", "args": {"content_md": "x"}}]) == []


def test_budget_caps_steps_per_scope():
    for scope in ("paper", "lab", "onboarding", "writing"):
        b = budget_for(scope)
        assert 1 <= b.max_steps <= 8
        assert b.exhausted(step=b.max_steps, tool_calls=0) == "steps"
        assert b.exhausted(step=0, tool_calls=b.max_tool_calls) == "tool_calls"
        assert b.exhausted(step=0, tool_calls=0) == ""


def test_seed_page_text_is_not_fetched_twice(client):
    """精读已种子取页后，stub 不得再调一次同一工具，把预算浪费在重复观察上。"""
    lab_id, item_id, _d = _world(client)
    db = SessionLocal()
    try:
        user_id = _admin_user_id(db)
        out = agent_answer(
            db,
            user_id=user_id,
            lab_id=lab_id,
            scope="paper",
            question="这一页的方法怎么说的？",
            feature="tutor",
            item_id=item_id,
            page_no=2,
            seed_tools=[("get_paper_page_text", {"page_no": 2})],
        )
        page_steps = [s for s in out.get("steps") or [] if s.get("tool") == "get_paper_page_text"]
        assert len(page_steps) == 1
        assert out.get("sources")
    finally:
        db.close()


def test_steer_points_at_likely_tools_without_hiding_catalog():
    from fengsuan.app.services.agent.loop import _steer_line
    from fengsuan.app.services.agent.registry import openai_tools, steer_tools

    assert steer_tools("onboarding", "这个组在做什么方向？")[0] == "get_direction_tree"
    line = _steer_line("onboarding", "这个组在做什么方向？")
    assert "get_direction_tree" in line
    names = {t["function"]["name"] for t in openai_tools("onboarding")}
    assert "get_lab_members" in names
    assert "get_direction_tree" in names
    assert "get_lab_glossary" in names
    assert "get_lab_tasks" in names


def test_lab_tasks_hide_peer_assignments(client):
    lab_id, _, _ = _world(client)
    h = _h(_admin(client))

    def _invite(email: str) -> str:
        inv = client.post(f"/api/v1/labs/{lab_id}/invites", json={"email": email, "role": "student"}, headers=h)
        assert inv.status_code == 200, inv.text
        reg = client.post(
            "/api/v1/auth/register",
            json={
                "invite_token": inv.json()["token"],
                "email": email,
                "password": "student99",
                "display_name": email.split("@")[0],
                "consent": True,
            },
        )
        assert reg.status_code == 200, reg.text
        me = client.get("/api/v1/auth/me", headers=_h(reg.json()["access"]))
        assert me.status_code == 200, me.text
        return me.json()["id"]

    uid_a = _invite("evaltaska@example.com")
    uid_b = _invite("evaltaskb@example.com")
    only_a = client.post(
        f"/api/v1/labs/{lab_id}/tasks",
        json={"title": "只给甲的看板", "assignee_id": uid_a},
        headers=h,
    )
    assert only_a.status_code == 200, only_a.text
    db = SessionLocal()
    try:
        out_b = agent_answer(
            db,
            user_id=uid_b,
            lab_id=lab_id,
            scope="lab",
            question="看板上我的任务有哪些？",
            feature="tutor",
        )
        used = {s["tool"] for s in out_b["steps"] if s.get("tool")}
        assert "get_lab_tasks" in used
        titles_b = {s.get("title") for s in out_b["sources"]}
        assert "只给甲的看板" not in titles_b
        out_a = agent_answer(
            db,
            user_id=uid_a,
            lab_id=lab_id,
            scope="lab",
            question="看板上我的任务有哪些？",
            feature="tutor",
        )
        titles_a = {s.get("title") for s in out_a["sources"]}
        assert "只给甲的看板" in titles_a
        out_named = agent_answer(
            db,
            user_id=uid_b,
            lab_id=lab_id,
            scope="lab",
            question="evaltaska的任务有哪些？",
            feature="tutor",
        )
        titles_named = {s.get("title") for s in out_named["sources"]}
        assert "只给甲的看板" not in titles_named
    finally:
        db.close()
