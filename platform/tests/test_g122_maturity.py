from __future__ import annotations

import json
from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import persist_unused_diagnosis
from fengsuan.app.services.writing import writing_unused_research_note


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


def _plant(dest: Path, title: str):
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        f"@article{{z,\n  title={{{title}}},\n  author={{A}},\n  year={{2021}},\n  journal={{J}}\n}}\n",
        encoding="utf-8",
    )


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


def _unused_job(client, h, lab: str, dest: Path, title: str, question: str):
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(dest, title)
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


def _strip_direction(rid: str):
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        try:
            prev = json.loads(row.progress_json or "{}")
        except (TypeError, json.JSONDecodeError):
            prev = {}
        if not isinstance(prev, dict):
            prev = {}
        prev.pop("direction_id", None)
        diag = prev.get("diagnosis")
        if isinstance(diag, dict):
            diag.pop("direction_id", None)
            prev["diagnosis"] = diag
        row.progress_json = json.dumps(prev, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


def test_g122_orphan_unused_stays_off_live_direction(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g122orph")
    a = _invite_student(client, h, lab, "g122a@example.com", "丙同学")
    aid = _direction(client, h, lab, "LiveA")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": aid}, headers=a).status_code == 200
    rid = _unused_job(client, a, lab, tmp_path / "job", "Orphan Unused Paper", "no snapshot")
    _strip_direction(rid)

    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    rows = {x["id"]: x for x in listed.json()}
    assert rows[aid].get("unused_research") == 0

    scoped = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "direction_id": aid}, headers=h)
    body = scoped.json()
    assert not _rows(body)
    assert (body.get("empty_next") or {}).get("title") == "该方向没有综述没写到的文献调研"

    whole = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert {row["id"] for row in _rows(whole.json())} == {rid}


def test_g123_manuscript_stuck_empty_is_named(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g123ms")
    created = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "空稿", "kind": "other", "content_md": "还没有相关工作标题。"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    empty = client.get(
        f"/api/v1/labs/{lab}/manuscripts",
        params={"owner_id": "nobody-not-a-user", "ungrounded": True},
        headers=h,
    )
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert not _rows(body)
    assert body.get("empty_kind") == "filter"
    assert (body.get("empty_next") or {}).get("title") == "该成员没有还没复现就写成成功的手稿"
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=h)
    assert isinstance(listed.json(), list)
    assert listed.json()[0]["id"] == created.json()["id"]


def test_g124_writing_unused_exposes_title_chips(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g124chip")
    rid = _unused_job(client, h, lab, tmp_path / "job", "Chip Unused Paper", "writing chips")
    db = SessionLocal()
    try:
        uid = db.get(ResearchRun, rid).requester_id
        note = writing_unused_research_note(db, lab, uid)
    finally:
        db.close()
    assert note
    assert "Chip Unused Paper" in (note.get("text") or "")
    assert "Chip Unused Paper" in (note.get("titles") or [])
    ol = client.post(f"/api/v1/labs/{lab}/writing/outline", json={"title": "综述", "kind": "survey"}, headers=h)
    assert ol.status_code == 200, ol.text
    unused = next(n for n in ol.json().get("honesty_notes") or [] if n.get("kind") == "unused_research")
    assert "Chip Unused Paper" in (unused.get("text") or "")
    assert "Chip Unused Paper" in (unused.get("titles") or [])
