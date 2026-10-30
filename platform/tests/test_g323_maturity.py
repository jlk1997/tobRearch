from __future__ import annotations

from fengsuan.app.services.language import clip_preview, split_readable_chunks, wrap_language_user


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


def test_g323_clip_preview_and_wrap():
    assert clip_preview("") == ""
    assert clip_preview("short span") == "short span"
    long = "We propose a convolutional method for images and language. " * 4
    prev = clip_preview(long, 72)
    assert prev.endswith("…")
    assert len(prev) <= 72
    assert "convolutional" in prev
    assert "前后文" in wrap_language_user("It works well.", "We propose BERT.")
    assert len(split_readable_chunks(("Alpha sentence about vision. " * 50) + "\n\n" + ("Beta sentence about language. " * 50))) >= 2


def test_g323_tutor_excerpt_preview(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g323tut")
    paper = _paper(
        client,
        h,
        lab,
        "Friendly Excerpt Paper",
        "We propose a convolutional method for images and language understanding tasks.",
    )
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "stub_complete", lambda messages, json_mode=False: "这句话在讲卷积方法。")
    excerpt = "We propose a convolutional method for images and language understanding tasks."
    r = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={
            "question": "这句话在说什么？",
            "page_no": 1,
            "excerpt": excerpt,
            "context": "Intro. " + excerpt,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("excerpt") == excerpt
    preview = body.get("excerpt_preview") or ""
    assert preview
    assert preview.endswith("…") or len(excerpt) <= 72
    assert "convolutional" in preview
    assert body.get("panel_title") == "这一页还没有可复制文字"


def test_g323_translate_chunks_and_home_contracts(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g323home")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "stub_complete", lambda messages, json_mode=False: "我们提出一种卷积方法。")
    text = ("Alpha sentence about vision. " * 50) + "\n\n" + ("Beta sentence about language. " * 50)
    tr = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": text, "target": "zh"},
        headers=h,
    )
    assert tr.status_code == 200, tr.text
    assert tr.json().get("chunks") >= 2

    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Friendly Reader Paper", "direction_ids": [did]},
        headers=h,
    ).json()

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
    assert "方法卡" not in (stuck.get("text") or "")
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
