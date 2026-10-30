from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import (
    literature_sidecar,
    literature_sidecar_for_run,
    persist_unused_diagnosis,
    research_diagnosis,
)
from fengsuan.app.services.writing import outline_honesty, writing_unused_research_note


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


def _plant(dest: Path):
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )


def test_g101_diagnosis_trusts_persisted_keys(tmp_path):
    _plant(tmp_path)
    disk = literature_sidecar(tmp_path)
    assert "Unseen Title Paper" in (disk.get("unused_bib_titles") or [])
    run = SimpleNamespace(
        save_path=str(tmp_path),
        status="failed",
        error="综述没写到",
        error_class="parse",
        progress_json=json.dumps(
            {
                "diagnosis": {
                    "next_step": "恢复",
                    "unused_bib_titles": [],
                    "unused_injected_titles": [],
                    "search_hit_count": 0,
                }
            }
        ),
    )
    extra = literature_sidecar_for_run(run)
    assert extra["unused_bib_titles"] == []
    d = research_diagnosis(run)
    assert d["unused_bib_titles"] == []
    assert d.get("next_step")


def test_g102_outline_honesty_keeps_unused_note():
    unused = {
        "kind": "unused_research",
        "level": "warning",
        "text": "上次综述没写到「Unseen Title Paper」。",
        "href": "/runs/x",
        "label": "打开文献调研",
    }
    bare = outline_honesty(paper_titles=[], used_card=False, used_repro=False, unused_research=unused)
    kinds = {n["kind"] for n in bare["honesty_notes"]}
    assert kinds == {"unused_research", "literature"}
    assert bare["notes"] == "未结合文献"
    with_papers = outline_honesty(
        paper_titles=["T"], used_card=False, used_repro=True, unused_research=unused
    )
    kinds = {n["kind"] for n in with_papers["honesty_notes"]}
    assert kinds == {"unused_research", "method_card", "reproduce"}


def test_g103_writing_and_claims_see_unused(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g103wr")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G103 unused", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    dest = tmp_path / "job"
    _plant(dest)
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述没写到"
        persist_unused_diagnosis(row)
        db.commit()
        uid = row.requester_id
    finally:
        db.close()
    db = SessionLocal()
    try:
        note = writing_unused_research_note(db, lab, uid)
    finally:
        db.close()
    assert note
    assert "Unseen Title Paper" in note["text"]
    assert note["href"] == f"/runs/{rid}"

    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey"},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    kinds = {n.get("kind") for n in ol.json().get("honesty_notes") or []}
    assert "unused_research" in kinds
    assert any("Unseen Title Paper" in (n.get("text") or "") for n in ol.json()["honesty_notes"])

    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G103稿", "kind": "other", "content_md": "## 相关工作\n已写。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    claims = client.get(f"/api/v1/manuscripts/{ms.json()['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    unused = claims.json().get("unused_research") or {}
    assert "Unseen Title Paper" in (unused.get("text") or "")
    assert unused.get("href") == f"/runs/{rid}"
    nxt = claims.json().get("next_step") or {}
    assert nxt.get("key") == "unused_research"
