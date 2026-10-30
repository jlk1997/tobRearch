"""运行时加深：方向过滤走现有检索，连续工具失败必须可见降级。"""

from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.agent import agent_answer


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


def _admin_user_id() -> str:
    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User

        row = db.query(User).filter(User.email == "admin@example.com").first()
        assert row is not None
        return row.id
    finally:
        db.close()


def test_ask_respects_direction_scope(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "dirask", "code": "dirask", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    d1 = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "图学习", "description": "gnn", "keywords": ["graph"]},
        headers=h,
    )
    d2 = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "注意力", "description": "attn", "keywords": ["attention"]},
        headers=h,
    )
    assert d1.status_code == 200 and d2.status_code == 200
    a = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={
            "title": "Graph convolution uniquetokenaaa",
            "abstract": "graph convolution uniquetokenaaa",
            "language": "en",
            "direction_ids": [d1.json()["id"]],
        },
        headers=h,
    )
    b = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={
            "title": "Attention uniquetokenbbb",
            "abstract": "attention uniquetokenbbb",
            "language": "en",
            "direction_ids": [d2.json()["id"]],
        },
        headers=h,
    )
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    bid = b.json()["id"]

    leaked = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "uniquetokenbbb", "scope": "lab", "direction_id": d1.json()["id"]},
        headers=h,
    )
    assert leaked.status_code == 200, leaked.text
    ids = {s.get("item_id") for s in leaked.json().get("sources") or []}
    assert bid not in ids, "选方向后不得引用方向外的篇目"

    hit = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "uniquetokenbbb", "scope": "lab", "direction_id": d2.json()["id"]},
        headers=h,
    )
    assert hit.status_code == 200, hit.text
    ids = {s.get("item_id") for s in hit.json().get("sources") or []}
    assert bid in ids


def test_consecutive_tool_failures_degrade_to_chat(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "degfail", "code": "degfail", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    db = SessionLocal()
    try:
        out = agent_answer(
            db,
            user_id=_admin_user_id(),
            lab_id=lab.json()["id"],
            scope="paper",
            question="这一页说了什么？",
            feature="tutor",
            item_id="",
            seed_tools=[("get_paper_page_text", {"page_no": 1}), ("get_paper_page_text", {"page_no": 2})],
        )
        assert out.get("degraded_to") == "chat"
        assert "tool_failures" in (out.get("degraded") or "")
        assert out.get("answer")
        assert out.get("text") == out.get("answer")
        assert out.get("trace_id")
    finally:
        db.close()


def test_write_note_waits_for_confirm(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "ntcfm", "code": "ntcfm", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Sparse attention", "abstract": "We prune heads.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureFile, LiteratureNote
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
        before = db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).count()
    finally:
        db.close()

    first = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "记下来：方法是剪枝", "page_no": 2},
        headers=h,
    )
    assert first.status_code == 200, first.text
    pending = first.json().get("pending_confirm") or []
    assert pending and pending[0]["tool"] == "write_note", first.json()
    assert pending[0].get("label") == "写笔记"
    assert "剪枝" in (pending[0].get("preview_text") or "")

    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        assert db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).count() == before
    finally:
        db.close()

    second = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "记下来：方法是剪枝", "page_no": 2, "confirm": True},
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert not second.json().get("pending_confirm")
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        assert db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).count() == before + 1
    finally:
        db.close()


def test_run_agent_ledgers_once(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "ledgr", "code": "ledgr", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    db = SessionLocal()
    try:
        from fengsuan.app.models.runs import UsageLedger

        before = db.query(UsageLedger).filter(UsageLedger.lab_id == lab_id, UsageLedger.feature == "kb_query").count()
        agent_answer(
            db,
            user_id=_admin_user_id(),
            lab_id=lab_id,
            scope="lab",
            question="组里方向是什么",
            feature="kb_query",
        )
        db.commit()
        after = db.query(UsageLedger).filter(UsageLedger.lab_id == lab_id, UsageLedger.feature == "kb_query").count()
        assert after - before == 1
    finally:
        db.close()


def _seed_pages(item_id: str, pages: list[str]) -> None:
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
        store_pages(db, f, pages)
        db.commit()
    finally:
        db.close()


def test_get_my_notes_hides_others(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "mynote", "code": "mynote", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Notes paper", "abstract": "We prune.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    uid, sh = _student(client, h, lab_id, "notekid@example.com")
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        db.add(LiteratureNote(item_id=item_id, user_id=_admin_user_id(), content_md="ADMIN_ONLY_NOTE", page_no=1))
        db.add(LiteratureNote(item_id=item_id, user_id=uid, content_md="STUDENT_ONLY_NOTE", page_no=2))
        db.commit()
        out = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="paper",
            question="我写过什么批注？",
            feature="tutor",
            item_id=item_id,
        )
        blob = " ".join((s.get("excerpt") or "") for s in out.get("sources") or [])
        assert "STUDENT_ONLY_NOTE" in blob
        assert "ADMIN_ONLY_NOTE" not in blob
        tools = {s.get("tool") for s in out.get("steps") or []}
        assert "get_my_notes" in tools
    finally:
        db.close()
    assert sh


def test_write_highlight_needs_real_excerpt(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "hlite", "code": "hlite", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Sparse attention", "abstract": "We prune heads.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    _seed_pages(item_id, ["Intro.", "Method. We prune.", "Results."])

    fake = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "高亮这段", "page_no": 2},
        headers=h,
    )
    assert fake.status_code == 200, fake.text
    assert not fake.json().get("pending_confirm")
    errs = [s for s in fake.json().get("steps") or [] if s.get("tool") == "write_highlight" and s.get("error")]
    assert errs, fake.json()

    first = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "高亮这段", "page_no": 2, "excerpt": "We prune."},
        headers=h,
    )
    assert first.status_code == 200, first.text
    pending = first.json().get("pending_confirm") or []
    assert pending and pending[0]["tool"] == "write_highlight", first.json()

    second = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "高亮这段", "page_no": 2, "excerpt": "We prune.", "confirm": True},
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert not second.json().get("pending_confirm")
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        row = (
            db.query(LiteratureNote)
            .filter(LiteratureNote.item_id == item_id, LiteratureNote.anchor_json.isnot(None))
            .first()
        )
        assert row is not None
        assert "We prune." in (row.anchor_json or "")
        assert row.user_id == _admin_user_id()
    finally:
        db.close()


def test_confirm_replays_pending_args_not_new_question(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "ntrpl", "code": "ntrpl", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Sparse attention", "abstract": "We prune heads.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    _seed_pages(item_id, ["Intro.", "Method. We prune.", "Results."])

    first = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "记下来：方法是剪枝", "page_no": 2, "write_note": True},
        headers=h,
    )
    assert first.status_code == 200, first.text
    pending = first.json().get("pending_confirm") or []
    assert pending and pending[0]["tool"] == "write_note", first.json()
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        assert db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).count() == 0
    finally:
        db.close()

    second = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={
            "question": "记下来：不要写这个",
            "page_no": 2,
            "write_note": True,
            "confirm": True,
            "session_id": first.json().get("session_id"),
            "confirm_tools": [{"tool": p["tool"], "args": {"content_md": "客户端伪造的内容", "page_no": 2}} for p in pending],
        },
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert not second.json().get("pending_confirm")
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        rows = db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).all()
        assert len(rows) == 1
        assert "方法是剪枝" in (rows[0].content_md or "")
        assert "不要写这个" not in (rows[0].content_md or "")
    finally:
        db.close()

    third = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={
            "question": "记下来：不要写这个",
            "page_no": 2,
            "confirm": True,
            "session_id": first.json().get("session_id"),
        },
        headers=h,
    )
    assert third.status_code == 200, third.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        assert db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).count() == 1
    finally:
        db.close()


def test_confirm_without_confirm_tools_replays_preview(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "ntauto", "code": "ntauto", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Sparse attention", "abstract": "We prune heads.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    _seed_pages(item_id, ["Intro.", "Method. We prune.", "Results."])
    first = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={"question": "记下来：方法是剪枝", "page_no": 2},
        headers=h,
    )
    assert first.status_code == 200, first.text
    assert first.json().get("pending_confirm")
    second = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={
            "question": "记下来：不要写这个",
            "page_no": 2,
            "confirm": True,
            "session_id": first.json().get("session_id"),
        },
        headers=h,
    )
    assert second.status_code == 200, second.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        rows = db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).all()
        assert len(rows) == 1
        assert "方法是剪枝" in (rows[0].content_md or "")
        assert "不要写这个" not in (rows[0].content_md or "")
    finally:
        db.close()


def test_confirm_tools_without_session_pending_does_not_write(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "ntarg", "code": "ntarg", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Sparse attention", "abstract": "We prune heads.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    _seed_pages(item_id, ["Intro.", "Method. We prune.", "Results."])
    r = client.post(
        f"/api/v1/literature/{item_id}/tutor",
        json={
            "question": "这一页说了什么？",
            "page_no": 2,
            "confirm": True,
            "confirm_tools": [
                {"tool": "get_paper_page_text", "args": {"page_no": 2}},
                {"tool": "write_note", "args": {"content_md": "REPLAYED_NOTE", "page_no": 2}},
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureNote

        rows = db.query(LiteratureNote).filter(LiteratureNote.item_id == item_id).all()
        assert rows == []
    finally:
        db.close()
