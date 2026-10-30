from __future__ import annotations

from fengsuan.app.services.language import TUTOR_FOLLOWUP_SEL, clip_preview


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


def test_g330_followup_prompt_only_with_excerpt(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g330tut")
    paper = _paper(client, h, lab, "Keep Selection Paper", "We propose a convolutional method.")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "stub_complete", lambda messages, json_mode=False: "这句话在讲卷积方法。")
    with_sel = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={
            "question": "这句话在说什么？",
            "page_no": 1,
            "excerpt": "We propose a convolutional method.",
        },
        headers=h,
    )
    assert with_sel.status_code == 200, with_sel.text
    body = with_sel.json()
    assert body.get("excerpt") == "We propose a convolutional method."
    assert body.get("followup_prompt") == TUTOR_FOLLOWUP_SEL
    assert "细一点" in (body.get("followup_prompt") or "")
    assert body.get("excerpt_preview") == clip_preview(body["excerpt"])

    page_only = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "这一页在做什么？", "page_no": 1},
        headers=h,
    )
    assert page_only.status_code == 200, page_only.text
    assert not (page_only.json().get("followup_prompt") or "")
    assert not (page_only.json().get("excerpt") or "")


def test_g330_home_arrays_and_contracts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g330home")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Keep Sel Paper", "direction_ids": [did]},
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
    assert "方法卡" not in ((stats.json().get("stuck_brief") or {}).get("text") or "")
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
