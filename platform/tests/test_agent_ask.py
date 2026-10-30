"""A12 加深：/ask 别名、traces 只读、证据注入、待确认。不新开产品面。"""

from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.agent.features import attach_evidence


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


def _lab(client, h, code: str) -> str:
    lab = client.post("/api/v1/labs", json={"name": code, "code": code, "description": "图学习"}, headers=h)
    assert lab.status_code == 200, lab.text
    return lab.json()["id"]


def test_attach_evidence_appends_to_last_user_without_mutating():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "问什么"}]
    pack = {"sources": [{"id": "page:x:1", "title": "T", "excerpt": "graph convolution"}]}
    out = attach_evidence(msgs, pack)
    assert "page:x:1" in out[-1]["content"]
    assert "source_id" in out[-1]["content"]
    assert out[0]["content"] == "sys"
    assert msgs[-1]["content"] == "问什么"
    assert attach_evidence(msgs, {}) == msgs


def test_ask_alias_defaults_lab_and_keeps_onboarding(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "askalias")
    client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "图神经网络", "description": "节点分类", "keywords": ["gnn"]},
        headers=h,
    )
    remapped = client.post(f"/api/v1/labs/{lab_id}/ask", json={"q": "这个组在做什么？", "scope": "kb"}, headers=h)
    assert remapped.status_code == 200, remapped.text
    assert remapped.json()["scope"] == "lab"
    assert remapped.json()["agent"] is True
    assert remapped.json()["session_id"]
    assert remapped.json()["trace_id"]
    assert remapped.json().get("degraded_to") in {"", "chat"}

    onb = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "这个组在做什么方向？", "scope": "onboarding"},
        headers=h,
    )
    assert onb.status_code == 200, onb.text
    assert onb.json()["scope"] == "onboarding"
    tools = {s.get("tool") for s in onb.json().get("steps") or []}
    assert tools & {"get_direction_tree", "get_lab_members", "search_lab_kb"}


def test_ask_empty_catalog_offers_existing_next(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "askempty")
    r = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "组里有哪些文献？", "scope": "lab"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    steps = body.get("steps") or []
    assert any((s.get("label") or "") == "看文献目录" for s in steps), steps
    catalog = next(s for s in steps if s.get("tool") == "get_lab_literature")
    assert catalog.get("empty")
    nxt = catalog.get("empty_next") or body.get("empty_next") or {}
    assert "还没有文献" in (nxt.get("title") or "")
    assert any("/literature" in (s.get("href") or "") for s in nxt.get("steps") or []), nxt


def test_agent_trace_owner_teacher_or_self(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "asktr")
    ask = client.post(f"/api/v1/labs/{lab_id}/ask", json={"q": "组里方向是什么", "scope": "lab"}, headers=h)
    assert ask.status_code == 200, ask.text
    tid = ask.json()["trace_id"] or ask.json()["session_id"]
    mine = client.get(f"/api/v1/agent/traces/{tid}", headers=h)
    assert mine.status_code == 200, mine.text
    assert mine.json()["trace_id"] == tid
    assert mine.json()["steps"]

    _uid, sh = _student(client, h, lab_id, "asktr-a@example.com")
    other = client.get(f"/api/v1/agent/traces/{tid}", headers=sh)
    assert other.status_code == 403, other.text

    missing = client.get("/api/v1/agent/traces/not-a-real-trace", headers=h)
    assert missing.status_code == 404


def test_ask_write_tool_waits_for_confirm(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "asknb")
    first = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "提交调研：图卷积", "scope": "lab"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    pending = first.json().get("pending_confirm") or []
    assert pending, first.json()
    assert pending[0]["tool"] == "submit_research_run"
    assert pending[0].get("label") == "提交调研"

    second = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={
            "q": "提交调研：图卷积",
            "scope": "lab",
            "confirm": True,
            "session_id": first.json().get("session_id"),
        },
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert not second.json().get("pending_confirm")
    db = SessionLocal()
    try:
        from fengsuan.app.models.runs import ResearchRun

        assert db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id).count() == 1
    finally:
        db.close()
    third = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "提交调研：再来一次", "scope": "lab", "confirm": True, "session_id": first.json().get("session_id")},
        headers=h,
    )
    assert third.status_code == 200, third.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.runs import ResearchRun

        assert db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id).count() == 1
    finally:
        db.close()


def test_ask_confirm_replays_pending_question(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "askrpl")
    first = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "提交调研：图卷积", "scope": "lab"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    pending = first.json().get("pending_confirm") or []
    assert pending and pending[0]["tool"] == "submit_research_run"
    second = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={
            "q": "提交调研：不要跑这个",
            "scope": "lab",
            "confirm": True,
            "session_id": first.json().get("session_id"),
            "confirm_tools": [{"tool": p["tool"], "args": {"question": "提交调研：伪造题"}} for p in pending],
        },
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert not second.json().get("pending_confirm")
    db = SessionLocal()
    try:
        from fengsuan.app.models.runs import ResearchRun

        rows = db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id).all()
        assert len(rows) == 1
        assert "图卷积" in (rows[0].question or "")
        assert "不要跑这个" not in (rows[0].question or "")
    finally:
        db.close()


def test_tutor_pending_confirm_and_trace_roundtrip(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "asktut")
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Sparse attention", "abstract": "We prune heads.", "language": "en"},
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
        store_pages(db, f, ["Intro.", "Method. We prune.", "Results."])
        db.commit()
    finally:
        db.close()

    r = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "提交复现这篇", "page_no": 2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("pending_confirm"), body
    assert body["pending_confirm"][0]["tool"] == "submit_reproduce_run"
    assert body.get("degraded_to") in {"", "chat"}
    tid = body.get("trace_id") or body.get("session_id")
    tr = client.get(f"/api/v1/agent/traces/{tid}", headers=h)
    assert tr.status_code == 200, tr.text
    assert any(s.get("tool") == "submit_reproduce_run" for s in tr.json()["steps"])


def test_analyze_returns_trace_that_can_be_fetched(client):
    h = _h(_admin(client))
    lab_id = _lab(client, h, "askan")
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Graph convolution networks", "abstract": "We study graph convolution.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    r = client.post(f"/api/v1/literature/{item.json()['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 200, r.text
    tid = r.json().get("trace_id") or r.json().get("session_id")
    assert tid
    tr = client.get(f"/api/v1/agent/traces/{tid}", headers=h)
    assert tr.status_code == 200, tr.text
    tools = {s.get("tool") for s in tr.json()["steps"]}
    assert tools & {"get_paper_page_text", "get_paper_structure", "search_lab_literature"}


def test_trace_record_failure_does_not_rollback_business_write(client):
    """追踪写失败只能丢掉这一条审计，不能把同一请求里已 flush 的笔记撤掉。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "trsave")
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Keep note", "abstract": "graph"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    pid = item.json()["id"]
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    nid = ""
    db = SessionLocal()
    try:
        from unittest.mock import patch

        from fengsuan.app.models.literature import LiteratureNote
        from fengsuan.app.services.agent import trace

        note = LiteratureNote(item_id=pid, user_id=uid, content_md="KEEP_ME")
        db.add(note)
        db.flush()
        nid = note.id
        with patch("fengsuan.app.services.agent.trace.json.dumps", side_effect=ValueError("trace down")):
            trace.record(
                db,
                lab_id=lab_id,
                user_id=uid,
                session_id="s-keep",
                feature="tutor",
                scope="paper",
                step=1,
                kind="tool",
            )
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        kept = db.get(LiteratureNote, nid)
        assert kept is not None
        assert kept.content_md == "KEEP_ME"
    finally:
        db.close()
