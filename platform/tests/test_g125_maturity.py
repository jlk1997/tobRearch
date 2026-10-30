from __future__ import annotations

from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.insight import reproduce_list_empty_next, research_list_empty_next
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


def test_g125_reproduce_filter_empty_is_named(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g125repro")
    created = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"dataset_path": ".", "claimed_metrics": {"acc": 0.9}},
        headers=h,
    )
    assert created.status_code == 200, created.text
    listed = client.get(f"/api/v1/labs/{lab}/reproduce-runs", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    assert listed.json()[0]["id"] == created.json()["id"]
    created_status = created.json().get("status") or ""
    want = "awaiting_gpu" if created_status != "awaiting_gpu" else "pending"
    empty = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"status": want}, headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert not _rows(body)
    assert body.get("empty_kind") == "filter"
    assert (body.get("empty_next") or {}).get("title") == (
        "没有等待实验算力的复现" if want == "awaiting_gpu" else "没有已入队的复现"
    )
    measured = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"honest": "measured"}, headers=h)
    assert measured.status_code == 200, measured.text
    assert (measured.json().get("empty_next") or {}).get("title") == "没有已有实测的复现"
    assert reproduce_list_empty_next(lab, empty_kind="filter", status="failed")["title"] == "没有失败的复现"
    assert reproduce_list_empty_next(lab, empty_kind="filter", honest="blocked")["title"] == "没有缺数据的复现"
    alive = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"status": created_status or "pending"}, headers=h)
    assert alive.status_code == 200, alive.text
    if _rows(alive.json()):
        assert isinstance(alive.json(), list)


def test_g126_research_status_empty_is_named(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g126run")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "keep array", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    empty = client.get(f"/api/v1/labs/{lab}/research-runs", params={"status": "failed"}, headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert not _rows(body)
    assert body.get("empty_kind") == "filter"
    assert (body.get("empty_next") or {}).get("title") == "没有失败的文献调研"
    gpu = client.get(f"/api/v1/labs/{lab}/research-runs", params={"status": "awaiting_gpu"}, headers=h)
    assert (gpu.json().get("empty_next") or {}).get("title") == "没有等待实验算力的文献调研"
    assert research_list_empty_next(lab, empty_kind="filter", status="paused")["title"] == "没有已暂停的文献调研"
    alive = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"status": created.json().get("status") or "pending"},
        headers=h,
    )
    if _rows(alive.json()):
        assert isinstance(alive.json(), list)


def test_g127_direction_and_member_unused_titles(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g127chip")
    stu = _invite_student(client, h, lab, "g127s@example.com", "丁同学")
    did = _direction(client, h, lab, "ChipDir")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=stu).status_code == 200
    _unused_job(client, stu, lab, tmp_path / "job", "Hub Leftover Paper", "hub chips")
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    rows = {x["id"]: x for x in listed.json()}
    assert rows[did].get("unused_research") == 1
    assert "Hub Leftover Paper" in (rows[did].get("unused_titles") or [])
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}", headers=h)
    assert ov.status_code == 200, ov.text
    assert "Hub Leftover Paper" in (ov.json().get("unused_titles") or [])
    me = client.get("/api/v1/auth/me", headers=stu)
    assert me.status_code == 200, me.text
    uid = me.json()["id"]
    mem = client.get(f"/api/v1/labs/{lab}/cockpit/members/{uid}", headers=h)
    assert mem.status_code == 200, mem.text
    assert "Hub Leftover Paper" in (mem.json().get("unused_titles") or [])
