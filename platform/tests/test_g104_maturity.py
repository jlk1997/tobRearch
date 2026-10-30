from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import (
    persist_unused_diagnosis,
    research_unused_brief,
    run_unused_actionable,
    run_unused_titles,
)
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


def _fail_run(rid: str, dest: Path, *, status: str = "failed"):
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        dest.mkdir(parents=True, exist_ok=True)
        row.save_path = str(dest)
        row.status = status
        row.error_class = "parse"
        row.error = "综述未点名"
        persist_unused_diagnosis(row)
        db.commit()
        return row.requester_id
    finally:
        db.close()


def test_g104_failed_before_review_not_actionable(tmp_path):
    (tmp_path / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Must Cite Graph Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    run = SimpleNamespace(save_path=str(tmp_path), status="failed", progress_json="{}")
    persist_unused_diagnosis(run)
    stored = json.loads(run.progress_json)["diagnosis"]
    assert stored["unused_bib_titles"] == []
    assert stored["unused_injected_titles"] == []
    assert run_unused_titles(run) == {"unused_bib_titles": [], "unused_injected_titles": []}
    assert not run_unused_actionable(run)


def test_g104_dirty_persisted_keys_without_review_ignored(tmp_path):
    (tmp_path / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Ghost Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    run = SimpleNamespace(
        save_path=str(tmp_path),
        status="failed",
        progress_json=json.dumps(
            {
                "diagnosis": {
                    "unused_bib_titles": ["Ghost Paper"],
                    "unused_injected_titles": ["Ghost Paper"],
                }
            }
        ),
    )
    assert run_unused_titles(run) == {"unused_bib_titles": [], "unused_injected_titles": []}
    assert not run_unused_actionable(run)


def test_g104_brief_skips_newer_failed_before_review(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g104scan")
    unused = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G104 unused after review", "domain": "general"},
        headers=h,
    )
    assert unused.status_code == 200, unused.text
    uid = unused.json()["id"]
    _plant(tmp_path / "reviewed")
    _fail_run(uid, tmp_path / "reviewed")

    early = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G104 fail before review", "domain": "general"},
        headers=h,
    )
    assert early.status_code == 200, early.text
    eid = early.json()["id"]
    dest = tmp_path / "early"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Must Cite Graph Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    _fail_run(eid, dest)

    db = SessionLocal()
    try:
        pack = research_unused_brief(db, lab, limit=1)
        requester = db.get(ResearchRun, uid).requester_id
        own = writing_unused_research_note(db, lab, requester)
    finally:
        db.close()
    assert pack["count"] == 1
    assert pack["items"][0]["id"] == uid
    assert "Unseen Title Paper" in ((pack.get("titles") or [""])[0] or "")
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert listed.status_code == 200, listed.text
    raw = listed.json()
    items = raw if isinstance(raw, list) else (raw.get("items") or [])
    ids = {x["id"] for x in items}
    assert uid in ids
    assert eid not in ids
    assert own
    assert "Unseen Title Paper" in own["text"]
    assert own["href"] == f"/runs/{uid}"


def test_g105_named_or_unused_mark_clears_writing_note(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g105body")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G105 unused", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(tmp_path / "job")
    uid = _fail_run(rid, tmp_path / "job")

    bare = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G105稿", "kind": "other", "content_md": "## 相关工作\n已写。"},
        headers=h,
    )
    assert bare.status_code == 200, bare.text
    mid = bare.json()["id"]
    claims = client.get(f"/api/v1/manuscripts/{mid}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    unused = claims.json().get("unused_research") or {}
    assert "Unseen Title Paper" in (unused.get("text") or "")
    assert (claims.json().get("next_step") or {}).get("key") == "unused_research"

    named = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"content_md": "## 相关工作\n点名 Unseen Title Paper。"},
        headers=h,
    )
    assert named.status_code == 200, named.text
    db = SessionLocal()
    try:
        note = writing_unused_research_note(db, lab, uid, body="点名 Unseen Title Paper。", manuscript_id=mid)
    finally:
        db.close()
    assert note is None
    named_claims = client.get(f"/api/v1/manuscripts/{mid}/claims", headers=h)
    assert named_claims.status_code == 200, named_claims.text
    assert not named_claims.json().get("unused_research")
    assert (named_claims.json().get("next_step") or {}).get("key") != "unused_research"

    unused_mark = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G105未使用", "kind": "other", "content_md": "## 相关工作\n未使用：Unseen Title Paper"},
        headers=h,
    )
    assert unused_mark.status_code == 200, unused_mark.text
    mark_claims = client.get(f"/api/v1/manuscripts/{unused_mark.json()['id']}/claims", headers=h)
    assert mark_claims.status_code == 200, mark_claims.text
    assert not mark_claims.json().get("unused_research")


def test_g105_teacher_outline_uses_student_manuscript_owner(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g105own")
    sh = _invite_student(client, h, lab, "g105stu@example.com")
    teacher_run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "teacher unused", "domain": "general"},
        headers=h,
    )
    assert teacher_run.status_code == 200, teacher_run.text
    _plant(tmp_path / "teacher", "Teacher Only Paper")
    _fail_run(teacher_run.json()["id"], tmp_path / "teacher")

    student_run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "student unused", "domain": "general"},
        headers=sh,
    )
    assert student_run.status_code == 200, student_run.text
    sid = student_run.json()["id"]
    _plant(tmp_path / "student")
    _fail_run(sid, tmp_path / "student")

    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "学生稿", "kind": "other", "content_md": "## 相关工作\n已写。"},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]

    teacher_ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey"},
        headers=h,
    )
    assert teacher_ol.status_code == 200, teacher_ol.text
    teacher_texts = " ".join(n.get("text") or "" for n in teacher_ol.json().get("honesty_notes") or [])
    assert "Teacher Only Paper" in teacher_texts
    assert "Unseen Title Paper" not in teacher_texts

    student_ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "manuscript_id": mid},
        headers=h,
    )
    assert student_ol.status_code == 200, student_ol.text
    student_texts = " ".join(n.get("text") or "" for n in student_ol.json().get("honesty_notes") or [])
    assert "Unseen Title Paper" in student_texts
    assert "Teacher Only Paper" not in student_texts


def test_g106_compare_and_expand_carry_unused(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "fengsuan.app.api.v1.language.chat",
        lambda *a, **k: "拓展后的相关工作草稿。",
    )
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g106cmp")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G106 unused", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(tmp_path / "job")
    _fail_run(rid, tmp_path / "job")

    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G106 Compare Alpha TitleX", "abstract": "a"},
        headers=h,
    ).json()
    b = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G106 Compare Beta TitleX", "abstract": "b"},
        headers=h,
    ).json()
    cmp = client.post(
        f"/api/v1/labs/{lab}/literature/compare",
        json={"item_ids": [a["id"], b["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    unused = cmp.json().get("unused_research") or {}
    assert "Unseen Title Paper" in (unused.get("text") or "")
    assert unused.get("href") == f"/runs/{rid}"
    kinds = {n.get("kind") for n in cmp.json().get("honesty_notes") or []}
    assert "unused_research" in kinds

    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G106稿", "kind": "other", "content_md": "## 相关工作\n已写。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    expand = client.post(
        f"/api/v1/labs/{lab}/language/expand",
        json={
            "text": "相关工作还很短。",
            "expand_goal": "related_work",
            "manuscript_id": ms.json()["id"],
            "stream": False,
        },
        headers=h,
    )
    assert expand.status_code == 200, expand.text
    exp_unused = expand.json().get("unused_research") or {}
    assert "Unseen Title Paper" in (exp_unused.get("text") or "")
    polish = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "相关工作还很短。", "stream": False},
        headers=h,
    )
    assert polish.status_code == 200, polish.text
    assert not polish.json().get("unused_research")
