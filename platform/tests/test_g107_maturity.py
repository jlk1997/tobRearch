from __future__ import annotations

import json
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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _plant(dest: Path, title: str = "Unseen Title Paper"):
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
        row.error = "综述未点名"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()


def _unused_job(client, h, lab: str, dest: Path, question: str = "G107 unused"):
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(dest)
    _fail_run(rid, dest)
    return rid


def test_g107_kb_query_and_summarize_carry_unused(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g107kb")
    rid = _unused_job(client, h, lab, tmp_path / "job")
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "anything"}, headers=h)
    assert q.status_code == 200, q.text
    unused = q.json().get("unused_research") or {}
    assert "Unseen Title Paper" in (unused.get("text") or "")
    assert unused.get("href") == f"/runs/{rid}"
    sm = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    assert "Unseen Title Paper" in ((sm.json().get("unused_research") or {}).get("text") or "")

    named = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G107稿", "kind": "other", "content_md": "## 相关工作\n点名 Unseen Title Paper。"},
        headers=h,
    )
    assert named.status_code == 200, named.text
    q2 = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "anything"}, headers=h)
    assert q2.status_code == 200, q2.text
    assert not q2.json().get("unused_research")


def test_g108_review_sim_unused_finding(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g108rv")
    rid = _unused_job(client, h, lab, tmp_path / "job")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G108稿", "kind": "other", "content_md": "## 相关工作\n已写。\n\n结论"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    before = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    r = client.post(f"/api/v1/manuscripts/{mid}/review-simulate", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("official") is False
    unused = body.get("unused_research") or {}
    assert "Unseen Title Paper" in (unused.get("text") or "")
    assert unused.get("href") == f"/runs/{rid}"
    kinds = {f.get("kind") for f in body.get("findings") or []}
    assert "unused_research" in kinds
    assert "ok" not in kinds
    after = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    assert after == before

    patch = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"content_md": "## 相关工作\n点名 Unseen Title Paper。\n\n结论"},
        headers=h,
    )
    assert patch.status_code == 200, patch.text
    r2 = client.post(f"/api/v1/manuscripts/{mid}/review-simulate", headers=h)
    assert r2.status_code == 200, r2.text
    assert not r2.json().get("unused_research")
    kinds2 = {f.get("kind") for f in r2.json().get("findings") or []}
    assert "unused_research" not in kinds2


def test_g109_direction_and_home_named_copy(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g109dir")
    sh = _invite_student(client, h, lab, "g109stu@example.com")
    a = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "DirA", "keywords": ["a"], "description": ""},
        headers=h,
    )
    b = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "DirB", "keywords": ["b"], "description": ""},
        headers=h,
    )
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    aid, bid = a.json()["id"], b.json()["id"]
    pick = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": aid}, headers=sh)
    assert pick.status_code == 200, pick.text
    rid = _unused_job(client, sh, lab, tmp_path / "stu", "student unused")

    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    rows = {x["id"]: x for x in listed.json()}
    assert rows[aid].get("unused_research") == 1
    assert rows[bid].get("unused_research") == 0
    assert "unused=1" in (rows[aid].get("unused_research_href") or "")
    assert rows[aid].get("stuck_count") == (rows[aid].get("unread_count") or 0) + (
        rows[aid].get("no_card_count") or 0
    ) + (rows[aid].get("related_work_missing") or 0) + (rows[aid].get("reproduce_not_run") or 0) + (
        rows[aid].get("manuscripts_ungrounded") or 0
    )

    ov = client.get(f"/api/v1/labs/{lab}/directions/{aid}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    unused_line = next(x for x in (ov.json().get("stuck") or {}).get("active") or [] if x.get("key") == "unused_research")
    assert unused_line.get("count") == 1
    other = client.get(f"/api/v1/labs/{lab}/directions/{bid}/overview", headers=h)
    assert other.status_code == 200, other.text
    other_keys = {x.get("key") for x in (other.json().get("stuck") or {}).get("active") or []}
    assert "unused_research" not in other_keys

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    step = next(x for x in home.json().get("next_steps") or [] if str(x.get("key") or "").startswith("unused"))
    assert "Unseen Title Paper" in (step.get("text") or "")
    assert "相关工作里点名" in (step.get("text") or "")
    assert step.get("href") == f"/runs/{rid}"

    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "学生稿", "kind": "other", "content_md": "## 相关工作\n点名 Unseen Title Paper。"},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    home2 = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home2.status_code == 200, home2.text
    step2 = next(x for x in home2.json().get("next_steps") or [] if str(x.get("key") or "").startswith("unused"))
    assert "相关工作已点名" in (step2.get("text") or "")
    assert step2.get("href") == f"/runs/{rid}"
