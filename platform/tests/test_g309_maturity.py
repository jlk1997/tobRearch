from __future__ import annotations

from fengsuan.app.services.language import translation_looks_like_instructions


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


def test_g309_tutor_excerpt_and_empty_page_title(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g309tut")
    paper = _paper(client, h, lab, "Ask Sentence Paper", "We propose a convolutional method.")
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
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "convolutional method" in (seen.get("user") or "")
    assert "这句话在说什么" in (seen.get("user") or "")
    assert body.get("page_text_missing") is True
    assert body.get("panel_title") == "这一页还没有可复制文字"
    assert "尚无页文本" not in (body.get("panel_title") or "")


def test_g309_polish_rejects_instruction_dump(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g309pl")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: (
            "Carefully polish the following. Do not add explanations. Key points: keep tone."
        ),
    )
    bad = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "We propose a convolutional method.", "polish_level": "academic"},
        headers=h,
    )
    assert bad.status_code == 400, bad.text
    pack = bad.json()
    assert "正文" in (pack.get("detail") or "")
    assert pack.get("error_class") == "llm"
    assert translation_looks_like_instructions(
        "Carefully polish the following. Do not add explanations.",
        "en",
    )


def test_g309_home_arrays_and_contracts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g309home")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Ask And Polish Paper", "direction_ids": [did]},
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
