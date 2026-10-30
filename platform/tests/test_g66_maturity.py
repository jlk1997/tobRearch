from __future__ import annotations

from fengsuan.app.services.llm import chat, strip_model_reasoning


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str):
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _paper(client, h, lab: str, title: str, abstract: str = "") -> dict:
    r = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": title, "abstract": abstract},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_g66_strip_model_reasoning_drops_think():
    text, stripped = strip_model_reasoning("<think>用户没给正文</think>\n本页方法是卷积。")
    assert "think" not in text.lower()
    assert "卷积" in text
    assert stripped is True
    leftover, leftover_flag = strip_model_reasoning("<think>未闭合残段\n可见回答")
    assert "think" not in leftover.lower()
    assert "可见回答" in leftover
    assert leftover_flag is True


def test_g66_chat_strips_think(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g66chat")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: "<think>hidden</think>\n可见回答",
    )
    from fengsuan.app.db.session import SessionLocal

    db = SessionLocal()
    try:
        out = chat(db, user_id="admin", lab_id=lab, messages=[{"role": "user", "content": "hi"}], feature="tutor")
    finally:
        db.close()
    assert "<think>" not in out
    assert "可见回答" in out


def test_g66_empty_translate_still_400(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g66tr")
    empty = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "   ", "target": "zh"},
        headers=h,
    )
    assert empty.status_code == 400, empty.text
    body = empty.json()
    assert "待处理" in (body.get("detail") or "")
    assert body.get("error_class") == "parse"


def test_g66_translate_and_tutor_strip_think(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g66both")
    paper = _paper(client, h, lab, "G66 Think Paper", "We propose a convolutional method.")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: "<think>用户没给正文</think>\n卷积方法。",
    )
    tr = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={
            "text": "We propose a convolutional method.",
            "target": "zh",
            "literature_item_id": paper["id"],
            "page_no": 1,
        },
        headers=h,
    )
    assert tr.status_code == 200, tr.text
    pack = tr.json()
    assert "<think>" not in (pack.get("text") or "")
    assert pack.get("reasoning_stripped") is True
    assert pack.get("source")
    tut = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "这一页在做什么？", "page_no": 1},
        headers=h,
    )
    assert tut.status_code == 200, tut.text
    body = tut.json()
    assert "<think>" not in (body.get("answer") or "").lower()
    assert body.get("reasoning_stripped") is True
    assert body.get("page_text_missing") is True
    assert body.get("panel_title") == "这一页还没有可复制文字"


def test_g67_tutor_sessions_and_history_cap(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g67sess")
    paper = _paper(client, h, lab, "G67 Session Paper", "Abstract only for tutor sessions.")
    first = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "会话甲第一问", "page_no": 1, "session_id": "sess-a"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    for i in range(12):
        r = client.post(
            f"/api/v1/literature/{paper['id']}/tutor",
            json={"question": f"会话甲追问{i}", "page_no": 1, "session_id": "sess-a"},
            headers=h,
        )
        assert r.status_code == 200, r.text
    last_a = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "会话甲第十四问", "page_no": 1, "session_id": "sess-a"},
        headers=h,
    )
    assert last_a.status_code == 200, last_a.text
    assert last_a.json().get("history_turns") == 12
    other = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "会话乙独立问", "page_no": 2, "session_id": "sess-b"},
        headers=h,
    )
    assert other.status_code == 200, other.text
    assert other.json().get("history_turns") == 0
    detail = client.get(
        f"/api/v1/literature/{paper['id']}/detail",
        params={"session": "sess-a", "page": 1, "include": "tutor"},
        headers=h,
    )
    assert detail.status_code == 200, detail.text
    dto = detail.json()
    sessions = dto.get("tutor_sessions") or []
    ids = {s.get("session_id") for s in sessions}
    assert "sess-a" in ids
    assert "sess-b" in ids
    a_row = next(s for s in sessions if s["session_id"] == "sess-a")
    assert a_row["turns"] >= 14
    assert a_row.get("last_question")
    hist = dto.get("tutor_history") or {}
    assert hist.get("session_id") == "sess-a"
    assert all((x.get("session_id") == "sess-a") for x in hist.get("items") or [])
    assert dto.get("page_text_missing") is True


def test_g68_tutor_write_note_and_payload(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g68send")
    paper = _paper(client, h, lab, "G68 Note Paper", "A method card page for notes.")
    tut = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "请解释方法", "page_no": 1, "write_note": True, "session_id": "sess-note"},
        headers=h,
    )
    assert tut.status_code == 200, tut.text
    body = tut.json()
    assert body.get("note_id")
    assert body.get("session_id") == "sess-note"
    assert body.get("reasoning_stripped") is True
    notes = client.get(f"/api/v1/literature/{paper['id']}/notes", headers=h)
    assert notes.status_code == 200, notes.text
    items = notes.json()
    assert isinstance(items, list)
    assert any(n.get("id") == body["note_id"] for n in items)
