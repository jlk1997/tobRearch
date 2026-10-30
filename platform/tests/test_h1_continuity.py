from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


def test_weak_password_rejected(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "PW", "code": "pwd1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "weak@example.com", "role": "student"}, headers=h).json()
    r = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "weak@example.com", "password": "abcdefgh", "consent": True},
    )
    assert r.status_code == 422


def test_export_bibtex_and_stats_csv(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "EX", "code": "exp1", "description": ""}, headers=h).json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Export Me Paper", "authors": ["Ada"], "publish_year": 2020, "doi": "10.1/xyz"},
        headers=h,
    )
    bib = client.get(f"/api/v1/labs/{lab}/literature/export-bibtex", headers=h)
    assert bib.status_code == 200, bib.text
    assert "Export Me Paper" in bib.text
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200
    assert "literature_count" in csv.text


def test_student_task_visibility(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "TV", "code": "tsk1", "description": ""}, headers=h).json()["id"]
    inv_a = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "stua@example.com", "role": "student"}, headers=h).json()
    inv_b = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "stub@example.com", "role": "student"}, headers=h).json()
    a = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv_a["token"], "email": "stua@example.com", "password": "student99", "consent": True, "display_name": "A"},
    ).json()["access"]
    b = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv_b["token"], "email": "stub@example.com", "password": "student99", "consent": True, "display_name": "B"},
    ).json()["access"]
    aid = client.get("/api/v1/auth/me", headers=_h(a)).json()["id"]
    client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "secret-unassigned"}, headers=h)
    client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "for-A", "assignee_id": aid}, headers=h)
    titles_b = [t["title"] for t in _items(client.get(f"/api/v1/labs/{lab}/tasks", headers=_h(b)).json())]
    titles_a = [t["title"] for t in _items(client.get(f"/api/v1/labs/{lab}/tasks", headers=_h(a)).json())]
    assert "secret-unassigned" not in titles_b
    assert "for-A" not in titles_b
    assert "for-A" in titles_a
    assert "secret-unassigned" not in titles_a


def test_overleaf_requires_url_and_falls_back_without_token(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "OL", "code": "olv1", "description": ""}, headers=h).json()["id"]
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "Draft", "kind": "conference"}, headers=h).json()
    mid = ms["id"]
    missing = client.post(f"/api/v1/manuscripts/{mid}/overleaf-sync", json={}, headers=h)
    assert missing.status_code == 400
    fb = client.post(
        f"/api/v1/manuscripts/{mid}/overleaf-sync",
        json={"git_url": "https://git.overleaf.com/abc"},
        headers=h,
    )
    assert fb.status_code == 200, fb.text
    assert fb.json()["ok"] is False
    assert fb.json()["fallback"] == "zip"


def test_run_artifacts_empty_and_stream_snapshot(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "RN", "code": "run2", "description": ""}, headers=h).json()["id"]
    run = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "toy", "dataset_path": ""}, headers=h)
    assert run.status_code == 200, run.text
    rid = run.json()["id"]
    arts = client.get(f"/api/v1/research-runs/{rid}/artifacts", headers=h)
    assert arts.status_code == 200
    assert arts.json()["items"] == []
    stream = client.get(f"/api/v1/research-runs/{rid}/stream", headers=h)
    assert stream.status_code == 200
    assert "snapshot" in stream.text
    paused = client.post(f"/api/v1/research-runs/{rid}/pause", headers=h)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"


def test_file_version_bump_and_meeting_detail(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "FV", "code": "fil1", "description": ""}, headers=h).json()["id"]
    a = client.post(f"/api/v1/labs/{lab}/files", files={"file": ("notes.txt", b"v1", "text/plain")}, headers=h)
    b = client.post(f"/api/v1/labs/{lab}/files", files={"file": ("notes.txt", b"v2", "text/plain")}, headers=h)
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert a.json()["version"] == 1
    assert b.json()["version"] == 2
    listed = _items(client.get(f"/api/v1/labs/{lab}/files", headers=h).json())
    assert {x["version"] for x in listed if x["filename"] == "notes.txt"} == {1, 2}
    dl = client.get(f"/api/v1/files/{b.json()['id']}", headers=h)
    assert dl.status_code == 200
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": "行动: 写摘要"}, headers=h)
    detail = client.get(f"/api/v1/meetings/{m.json()['id']}", headers=h)
    assert detail.status_code == 200
    assert detail.json()["title"] == "周会"


def test_language_and_kb_path_aliases(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "AL", "code": "als1", "description": ""}, headers=h).json()["id"]
    missing = client.post("/api/v1/language/polish", json={"text": "hello"}, headers=h)
    assert missing.status_code == 400
    pol = client.post("/api/v1/language/polish", json={"lab_id": lab, "text": "hello"}, headers=h)
    assert pol.status_code == 200, pol.text
    kb = client.get(f"/api/v1/labs/{lab}/kb", headers=h)
    assert kb.status_code == 200
    kid = kb.json()["id"]
    docs = client.get(f"/api/v1/kb/{kid}/documents", headers=h)
    assert docs.status_code == 200
    sm = client.post(f"/api/v1/kb/{kid}/summarize", headers=h)
    assert sm.status_code == 200


def test_credential_test_stub(client):
    token = _admin(client)
    h = _h(token)
    put = client.put(
        "/api/v1/me/llm-credentials",
        json={"provider": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-test1234", "model_chat": "gpt-4o-mini"},
        headers=h,
    )
    assert put.status_code == 200
    assert put.json()["api_key_masked"].endswith("1234")
    ping = client.post("/api/v1/me/llm-credentials/test", headers=h)
    assert ping.status_code == 200
    assert ping.json()["ok"] is True
    assert ping.json()["mode"] == "stub"


def test_inject_token_strips_from_error():
    from fengsuan.app.services.overleaf import inject_token

    url = inject_token("https://git.overleaf.com/proj", "secret-token")
    assert "secret-token" in url
    assert "git.overleaf.com" in url
