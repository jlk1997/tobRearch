from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import (
    persist_unused_diagnosis,
    run_unused_actionable,
    run_unused_titles,
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


def _plant(dest: Path, *, mention: bool = False):
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "tool_calls").mkdir(parents=True, exist_ok=True)
    md = "综述 Unseen Title Paper。" if mention else "综述正文没有点名。"
    (dest / "workspace" / "literature_review.md").write_text(md, encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    (dest / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Must Cite Graph Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _fail_run(rid: str, dest: Path, *, status: str = "failed"):
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        dest.mkdir(parents=True, exist_ok=True)
        row.save_path = str(dest)
        row.status = status
        row.error_class = "parse"
        row.error = "综述没写到"
        persist_unused_diagnosis(row)
        db.commit()
        return row.id
    finally:
        db.close()


def test_g98_trust_empty_diagnosis_keys(tmp_path):
    _plant(tmp_path)
    run = SimpleNamespace(
        save_path=str(tmp_path),
        status="failed",
        progress_json=json.dumps({"diagnosis": {"unused_bib_titles": [], "unused_injected_titles": []}}),
    )
    assert run_unused_titles(run) == {"unused_bib_titles": [], "unused_injected_titles": []}
    assert not run_unused_actionable(run)


def test_g98_pause_before_review_not_actionable(tmp_path):
    (tmp_path / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Must Cite Graph Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    run = SimpleNamespace(save_path=str(tmp_path), status="paused", progress_json="{}")
    persist_unused_diagnosis(run)
    stored = json.loads(run.progress_json)["diagnosis"]
    assert stored["unused_bib_titles"] == []
    assert stored["unused_injected_titles"] == []
    assert not run_unused_actionable(run)


def test_g98_pause_after_review_is_actionable(tmp_path):
    _plant(tmp_path)
    run = SimpleNamespace(save_path=str(tmp_path), status="paused", progress_json="{}")
    assert run_unused_actionable(run)


def test_g95_home_and_unused_filter(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g95home")
    sh = _invite_student(client, h, lab, "g95stu@example.com")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G95 unused", "domain": "general"},
        headers=sh,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    dest = tmp_path / "student"
    _plant(dest)
    _fail_run(rid, dest)

    paused = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G95 pause early", "domain": "general"},
        headers=h,
    )
    assert paused.status_code == 200, paused.text
    pid = paused.json()["id"]
    early = tmp_path / "early"
    early.mkdir(parents=True, exist_ok=True)
    (early / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Must Cite Graph Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, pid)
        row.save_path = str(early)
        row.status = "paused"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()

    student_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert student_home.status_code == 200, student_home.text
    ssteps = student_home.json().get("next_steps") or []
    unused = next(x for x in ssteps if str(x.get("key") or "").startswith("unused"))
    assert "Unseen Title Paper" in (unused.get("text") or "")
    assert unused.get("href") == f"/runs/{rid}"
    assert not student_home.json().get("stuck_brief")

    teacher_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert teacher_home.status_code == 200, teacher_home.text
    th = teacher_home.json()
    brief = th.get("stuck_brief") or {}
    assert brief.get("unused_research") == 1
    assert "unused=1" in (brief.get("unused_research_href") or "")
    assert "unused=1" in (th.get("runs_href") or "")
    assert th.get("runs_label") == "综述没写到"
    tkeys = {x.get("key") for x in th.get("next_steps") or []}
    assert "unused_research" in tkeys

    listed = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert listed.status_code == 200, listed.text
    raw = listed.json()
    items = raw if isinstance(raw, list) else (raw.get("items") or [])
    ids = {x["id"] for x in items}
    assert rid in ids
    assert pid not in ids

    empty_lab = _lab(client, h, "g95empty")
    empty = client.get(f"/api/v1/labs/{empty_lab}/research-runs", params={"unused": "1"}, headers=h)
    assert empty.status_code == 200, empty.text
    pack = empty.json()
    assert pack.get("empty_kind") == "library"
    assert "还没有文献调研" in ((pack.get("empty_next") or {}).get("title") or "")

    other = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "status": "succeeded"}, headers=h)
    assert other.status_code == 200, other.text
    other_body = other.json()
    if isinstance(other_body, dict):
        assert other_body.get("empty_kind") == "filter"
        assert "没有综述没写到的文献调研" in ((other_body.get("empty_next") or {}).get("title") or "")

    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    stats = st.json()
    assert stats.get("unused_research") == 1
    honesty = next(x for x in (stats.get("situation") or {}).get("lines") or [] if x.get("key") == "honesty")
    assert "综述没写到 1" in (honesty.get("text") or "")
    assert "unused=1" in (honesty.get("href") or "")
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "unused_research" in csv.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    cards = cockpit.json().get("members") or []
    assert any(
        any(s.get("key") == "unused_research" for s in (card.get("stuck") or {}).get("steps") or [])
        for card in cards
    )
