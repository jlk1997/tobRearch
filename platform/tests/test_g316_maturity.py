from __future__ import annotations

from fengsuan.app.services.language import (
    glossary_hits_on_page,
    split_readable_chunks,
    surrounding_context,
    wrap_language_user,
)


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


def test_g316_surrounding_and_chunks_and_hits():
    page = "Intro. We propose a convolutional method for images. Conclusion follows."
    ctx = surrounding_context(page, "convolutional method")
    assert "propose" in ctx
    assert "images" in ctx
    assert surrounding_context("", "x") == ""
    assert surrounding_context(page, "missing span") == ""

    long = ("Alpha sentence about vision. " * 40) + "\n\n" + ("Beta sentence about language. " * 40)
    parts = split_readable_chunks(long)
    assert len(parts) >= 2
    assert all(len(p) <= 2200 for p in parts)
    assert split_readable_chunks("short page") == ["short page"]

    hits = glossary_hits_on_page(
        "We use BERT and a CNN backbone.",
        [
            {"src": "BERT", "tgt": "BERT", "do_not_translate": True},
            {"src": "absent", "tgt": "无"},
        ],
    )
    assert hits and hits[0]["src"] == "BERT" and hits[0]["do_not_translate"] is True
    assert all(h["src"] != "absent" for h in hits)

    wrapped = wrap_language_user("It works well.", "We propose BERT. It works well on GLUE.")
    assert "前后文" in wrapped
    assert "不要译这段" in wrapped
    assert wrapped.endswith("It works well.")
    polished = wrap_language_user("It works well.", "We propose BERT.", mode="polish")
    assert "不要改这段" in polished


def test_g316_translate_uses_context_not_selection(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g316ctx")
    seen = []
    from fengsuan.app.services import llm as llm_mod

    def _stub(messages, json_mode=False):
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content") or ""
        seen.append(user)
        return "它在 GLUE 上表现很好。"

    monkeypatch.setattr(llm_mod, "stub_complete", _stub)
    r = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={
            "text": "It works well.",
            "target": "zh",
            "context": "We propose BERT. It works well on GLUE.",
            "selection": "manuscript span must not become translate context",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    pack = r.json()
    assert pack.get("chunks") == 1
    blob = seen[-1] if seen else ""
    assert "前后文" in blob
    assert "不要译这段" in blob
    assert "We propose BERT" in blob
    assert "manuscript span must not become translate context" not in blob

    seen.clear()
    bare = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "It works well.", "target": "zh", "selection": "ignore me"},
        headers=h,
    )
    assert bare.status_code == 200, bare.text
    assert "前后文" not in (seen[-1] if seen else "")


def test_g316_long_translate_chunks(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g316chunk")
    seen = []
    from fengsuan.app.services import llm as llm_mod

    def _stub(messages, json_mode=False):
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content") or ""
        seen.append(user)
        return "我们提出一种卷积方法。"

    monkeypatch.setattr(llm_mod, "stub_complete", _stub)
    text = ("Alpha sentence about vision. " * 50) + "\n\n" + ("Beta sentence about language. " * 50)
    r = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": text, "target": "zh"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("chunks") >= 2
    assert len(seen) >= 2
    assert "前后文" in (seen[1] if len(seen) > 1 else "")


def test_g316_tutor_excerpt_and_context(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g316tut")
    paper = _paper(client, h, lab, "Ground Selection Paper", "We propose a convolutional method.")
    seen = {}
    from fengsuan.app.services import llm as llm_mod

    def _stub(messages, json_mode=False):
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content") or ""
        seen["user"] = user
        return "这句话在讲卷积方法。"

    monkeypatch.setattr(llm_mod, "stub_complete", _stub)
    r = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={
            "question": "这句话在说什么？",
            "page_no": 1,
            "excerpt": "We propose a convolutional method.",
            "context": "Intro. We propose a convolutional method for images.",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("excerpt") == "We propose a convolutional method."
    user = seen.get("user") or ""
    assert "convolutional method" in user
    # 选区与其上下文都要到模型手里；Agent 运行时里是行文而非 selection_context 字段。
    assert "选区上下文" in user or "selection_context" in user
    assert "for images" in user
    assert body.get("panel_title") == "这一页还没有可复制文字"


def test_g316_glossary_hits_and_home_contracts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g316home")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    added = client.post(
        f"/api/v1/labs/{lab}/glossary",
        json={"src": "BERT", "tgt": "BERT", "do_not_translate": True},
        headers=h,
    )
    assert added.status_code == 200, added.text
    hits = glossary_hits_on_page("BERT is locked.", [{"src": "BERT", "do_not_translate": True}])
    assert hits[0]["do_not_translate"] is True

    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Context Polish Paper", "direction_ids": [did]},
        headers=h,
    ).json()
    assert lit["id"]

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    steps = home.json().get("next_steps") or []
    reading = next((s for s in steps if s.get("key") == "reading" or s.get("label") == "开始阅读"), None)
    href = (reading or {}).get("href") or ""
    if href:
        assert href.startswith(f"/labs/{lab}/literature")
        assert "unread" not in href
        if "direction_id=" in href:
            assert f"direction_id={did}" in href

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    stuck = stats.json().get("stuck_brief") or {}
    text = stuck.get("text") or ""
    assert "方法卡" not in text
    if stuck.get("no_card"):
        assert "还没写方法" in text

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)
    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
    gloss = client.get(f"/api/v1/labs/{lab}/glossary", params={"direction_id": did}, headers=h)
    assert gloss.status_code == 200, gloss.text
    assert isinstance(gloss.json(), list)
    mutex = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "status": "pending"},
        headers=h,
    )
    assert mutex.status_code == 200, mutex.text
    mutex_body = mutex.json()
    assert isinstance(mutex_body, dict)
    assert mutex_body.get("items") == []
    nxt = mutex_body.get("empty_next") or {}
    assert "综述没写到" in f"{nxt.get('title') or ''}{nxt.get('text') or ''}"
    wrapped = client.get(
        f"/api/v1/labs/{lab}/reproduce-runs",
        params={"literature_item_id": lit["id"]},
        headers=h,
    )
    assert wrapped.status_code == 200, wrapped.text
    assert isinstance(wrapped.json(), dict)
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", params={"direction_id": did}, headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert cockpit.json().get("events_scope") == "lab"
