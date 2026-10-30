"""阅读器辅导切到 Agent 运行时后的接口契约。

要守住三条：答案可追溯（每步落 `agent_traces`）、引用可点（带 href 与页码）、
跨篇提问走同一个入口（`scope=lab`）而不是新开产品面。
"""

from __future__ import annotations

from fengsuan.app.db.session import SessionLocal

PAGES = [
    "Introduction. Sparse attention reduces the cost of long sequence modeling.",
    "Method. We prune attention heads by magnitude and re-train for two epochs.",
    "Experiments. Perplexity drops to 18.4 on WikiText-103 versus 19.9 for the dense baseline.",
]


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _paper_with_pages(client, h, lab_id: str, title: str) -> str:
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": title, "abstract": "Sparse attention for long sequences.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureFile
        from fengsuan.app.services.pagecache import store_pages

        f = LiteratureFile(
            item_id=item_id,
            file_path=f"/tmp/{item_id}.pdf",
            filename="p.pdf",
            mime="application/pdf",
            size=2048,
            parse_status="parsed",
        )
        db.add(f)
        db.flush()
        store_pages(db, f, PAGES)
        db.commit()
    finally:
        db.close()
    return item_id


def test_tutor_runs_on_agent_runtime_with_clickable_citations(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agtut", "code": "agtut", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item_id = _paper_with_pages(client, h, lab_id, "Sparse attention pruning")

    r = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "这一页的方法怎么做的？", "page_no": 2, "session_id": "s-agent"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] is True
    assert body["steps"], "必须留下查证步骤"
    assert any(s["tool"] == "get_paper_page_text" for s in body["steps"])
    assert body["sources"], "必须带来源"
    first = body["sources"][0]
    assert first["page"] == 2
    assert first.get("page_no") == 2
    assert first.get("literature_item_id") == item_id
    assert f"page={first['page']}" in first["href"], "引用要能点回对应页"
    assert any((s.get("label") or "") == "取页正文" for s in body["steps"]), body["steps"]
    assert body["citations"], "回答要指回来源"
    assert body["citations"][0]["source_id"] in {s["id"] for s in body["sources"]}
    assert body["trace_id"]

    db = SessionLocal()
    try:
        from fengsuan.app.services.agent.trace import session_steps

        rows = session_steps(db, body["trace_id"])
        assert rows and rows[-1]["kind"] == "answer"
    finally:
        db.close()

    hist = client.get(
        f"/api/v1/literature/{item_id}/detail",
        params={"session": "s-agent", "include": "tutor"},
        headers=h,
    )
    assert hist.status_code == 200, hist.text
    items = (hist.json().get("tutor_history") or {}).get("items") or []
    assert items and items[0]["session_id"] == "s-agent"


def test_tutor_scope_lab_searches_across_papers(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agtutlab", "code": "agtutlab", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    here = _paper_with_pages(client, h, lab_id, "Sparse attention pruning")
    other = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Pruning attention heads at scale", "abstract": "We prune attention heads.", "language": "en"},
        headers=h,
    )
    assert other.status_code == 200, other.text

    r = client.post(
        f"/api/v1/literature/{here}/tutor",
        json={"question": "组里还读过哪些 attention 相关的文献？", "scope": "lab", "page_no": 1},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    tools = {s["tool"] for s in body["steps"]}
    assert tools & {"search_lab_literature", "search_lab_kb"}, tools
    ids = {s["item_id"] for s in body["sources"] if s.get("item_id")}
    assert other.json()["id"] in ids, "跨篇提问要能引到别的篇目"
    for src in body["sources"]:
        assert src["href"], "跨篇引用必须能点回原文"


def test_tutor_streaming_emits_tool_steps_then_answer(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agtutsse", "code": "agtutsse", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item_id = _paper_with_pages(client, h, lab_id, "Sparse attention pruning")
    r = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "这一页说了什么？", "page_no": 3, "stream": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    text = r.text
    assert '"tool"' in text, "流式必须先报查证步骤"
    assert '"done": true' in text.lower()


def test_tutor_falls_back_to_single_turn_when_disabled(client, monkeypatch):
    """开关关掉就退回单轮兜底，保证 Agent 不可用时阅读器还能问。"""
    monkeypatch.setenv("FENGSUN_TUTOR_AGENT", "0")
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agtutoff", "code": "agtutoff", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item_id = _paper_with_pages(client, h, lab_id, "Sparse attention pruning")
    r = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "这一页说了什么？", "page_no": 2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body.get("agent")
    assert body.get("answer")


def _student(client, h, lab_id: str, email: str) -> tuple[str, dict]:
    inv = client.post(f"/api/v1/labs/{lab_id}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "studentpass1",
            "display_name": email.split("@")[0],
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    sh = _h(reg.json()["access"])
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    return me.json()["id"], sh


def test_tutor_who_is_reading_uses_reading_roster_not_guess(client):
    """精读「这篇谁在读」只引用当前篇的在读记录，不把其他篇的读者算进来。"""
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agtutrd", "code": "agtutrd", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    a = _paper_with_pages(client, h, lab_id, "Paper A readers")
    b = _paper_with_pages(client, h, lab_id, "Paper B other")
    _uid, sh = _student(client, h, lab_id, "readerkid@example.com")
    assert client.post(f"/api/v1/literature/{a}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    assert client.post(f"/api/v1/literature/{b}/mark-read", json={"status": "reading"}, headers=h).status_code == 200

    r = client.post(
        f"/api/v1/literature/{a}/tutor",
        json={"question": "这篇谁在读？", "page_no": 1, "session_id": "s-readers"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tools = {s["tool"] for s in r.json().get("steps") or []}
    assert "get_paper_readers" in tools, tools
    blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in r.json().get("sources") or [])
    assert "readerkid" in blob
    assert "Paper B other" not in blob
    ids = {s.get("item_id") for s in r.json().get("sources") or []}
    assert a in ids
    assert b not in ids

    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer
        from fengsuan.app.services.literature import reading_roster

        uid = db.query(User).filter(User.email == "admin@example.com").one().id
        assert reading_roster(db, lab_id, item_ids=[]) == []
        leaked = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="paper",
            item_id=a,
            question="这篇谁在读？",
            feature="tutor",
            seed_tools=[("get_paper_readers", {"item_id": b})],
        )
        leaked_ids = {s.get("item_id") for s in leaked.get("sources") or []}
        assert a in leaked_ids
        assert b not in leaked_ids, "精读不得被工具参数带到另一篇"
    finally:
        db.close()

    done = client.post(f"/api/v1/literature/{a}/mark-read", json={"status": "finished"}, headers=sh)
    assert done.status_code == 200, done.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer

        uid = db.query(User).filter(User.email == "admin@example.com").one().id
        finished = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="paper",
            item_id=a,
            question="这篇谁在读？",
            feature="tutor",
            seed_tools=[("get_paper_readers", {})],
        )
        summaries = " ".join(s.get("result_summary") or "" for s in finished.get("steps") or [])
        assert "已读" in summaries
        assert "当前没有人在读" in summaries
    finally:
        db.close()


def test_tutor_page_text_stays_on_current_paper(client):
    """精读取页正文必须钉在当前篇，不能靠 item_id 串到另一篇。"""
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "agtutpg", "code": "agtutpg", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    a = _paper_with_pages(client, h, lab_id, "Paper A current")
    b = _paper_with_pages(client, h, lab_id, "Paper B other")
    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer

        uid = db.query(User).filter(User.email == "admin@example.com").one().id
        out = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="paper",
            item_id=a,
            question="这一页怎么说？",
            feature="tutor",
            seed_tools=[("get_paper_page_text", {"item_id": b, "page_no": 1})],
        )
        ids = {s.get("item_id") for s in out.get("sources") or []}
        titles = {s.get("title") for s in out.get("sources") or []}
        assert a in ids
        assert b not in ids
        assert "Paper A current" in titles
        assert "Paper B other" not in titles
    finally:
        db.close()


def test_tutor_puts_current_page_in_prompt_not_the_question(client, monkeypatch):
    seen: dict = {}

    def fake_run_agent(*_args, **kwargs):
        seen["question"] = kwargs.get("question") or ""
        seen["system_extra"] = kwargs.get("system_extra") or ""
        yield {
            "done": True,
            "answer": "这一页在讲按幅度剪掉注意力头。",
            "citations": [],
            "sources": [],
            "steps": [],
            "ungrounded": [],
        }

    monkeypatch.setattr("fengsuan.app.services.agent.run_agent", fake_run_agent)
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "pagetut", "code": "pagetut", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    item_id = _paper_with_pages(client, h, lab.json()["id"], "Page bound tutor")
    asked = "这一页在说什么？"
    r = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": asked, "page_no": 2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["question"] == asked
    assert "prune attention heads" in seen["system_extra"]
    assert "prune" not in seen["question"]
    assert "用中文" in seen["system_extra"]
