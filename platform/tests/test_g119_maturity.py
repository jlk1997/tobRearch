from __future__ import annotations

from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import persist_unused_diagnosis


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


def _invite_student(client, h, lab: str, email: str, name: str = "生"):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": name,
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _rows(payload):
    if isinstance(payload, list):
        return payload
    return list(payload.get("items") or [])


def _plant(dest: Path, titles):
    names = [titles] if isinstance(titles, str) else list(titles)
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    entries = []
    for i, title in enumerate(names):
        entries.append(f"@article{{z{i},\n  title={{{title}}},\n  author={{A}},\n  year={{2021}},\n  journal={{J}}\n}}\n")
    (dest / "workspace" / "literature_review.bib").write_text("".join(entries), encoding="utf-8")


def _fail_run(rid: str, dest: Path):
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        dest.mkdir(parents=True, exist_ok=True)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述没写到"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()


def _unused_job(client, h, lab: str, dest: Path, titles, question: str):
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(dest, titles)
    _fail_run(rid, dest)
    return rid


def _direction(client, h, lab: str, name: str):
    r = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": name, "keywords": [name.lower()], "description": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_g119_unused_list_names_scope(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g119name")
    a = _invite_student(client, h, lab, "g119a@example.com", "甲同学")
    aid = client.get("/api/v1/auth/me", headers=a).json()["id"]
    did = _direction(client, h, lab, "未点名方向")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=a).status_code == 200
    rid = _unused_job(client, a, lab, tmp_path / "a", "Named Leftover Paper", "named unused")

    scoped = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "requester_id": aid, "direction_id": did},
        headers=h,
    )
    assert scoped.status_code == 200, scoped.text
    body = scoped.json()
    assert isinstance(body, dict)
    assert {row["id"] for row in _rows(body)} == {rid}
    scope = body.get("unused_scope") or {}
    assert scope.get("requester_id") == aid
    assert scope.get("requester_name") == "甲同学"
    assert scope.get("direction_id") == did
    assert scope.get("direction_name") == "未点名方向"
    assert "Named Leftover Paper" in (body.get("unused_titles") or [])
    assert int(body.get("unused_title_more") or 0) == 0

    plain = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert plain.status_code == 200, plain.text
    assert isinstance(plain.json(), list)
    assert plain.json()[0]["id"] == rid


def test_g120_leftover_chips_and_more(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g120chip")
    titles = [f"Leftover Title {i:02d}" for i in range(1, 10)]
    _unused_job(client, h, lab, tmp_path / "many", titles, "many leftovers")

    listed = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert isinstance(body, dict)
    shown = body.get("unused_titles") or []
    assert len(shown) == 8
    assert int(body.get("unused_title_more") or 0) == 1
    assert shown[0] == "Leftover Title 01"

    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    pack = digest.json()
    assert pack.get("unused_research") == 1
    assert (pack.get("unused_titles") or [])[:1] == ["Leftover Title 01"]
    assert int(pack.get("unused_title_more") or 0) == 1
    brief = pack.get("stuck_brief") or {}
    assert "Leftover Title 01" in (brief.get("unused_titles") or [])

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    st = stats.json()
    assert st.get("unused_research") == 1
    assert (st.get("unused_titles") or [])[:1] == ["Leftover Title 01"]
    assert int(st.get("unused_title_more") or 0) == 1


def test_g121_unused_empty_keeps_scope_and_plain_array(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g121hero")
    a = _invite_student(client, h, lab, "g121a@example.com", "乙同学")
    aid = client.get("/api/v1/auth/me", headers=a).json()["id"]
    _unused_job(client, a, lab, tmp_path / "job", "Hero Unused Paper", "hero unused")

    empty = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "requester_id": "nobody-not-a-user"},
        headers=h,
    )
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert isinstance(body, dict)
    assert not _rows(body)
    assert body.get("empty_kind") == "filter"
    assert (body.get("empty_next") or {}).get("title") == "该成员没有综述没写到的文献调研"
    assert (body.get("unused_scope") or {}).get("requester_id") == "nobody-not-a-user"
    assert not (body.get("unused_scope") or {}).get("requester_name")

    mine = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "requester_id": aid}, headers=h)
    assert (mine.json().get("unused_scope") or {}).get("requester_name") == "乙同学"

    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "still array", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert isinstance(listed.json(), list)
    assert {row["id"] for row in listed.json()} >= {created.json()["id"]}
