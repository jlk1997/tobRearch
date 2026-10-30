from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import literature_sidecar, research_diagnosis


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
    (dest / "tool_calls").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    (dest / "tool_calls" / "search_hits.jsonl").write_text(
        '{"title":"Unseen Title Paper","doi":"10.1000/u"}\n',
        encoding="utf-8",
    )


def test_g89_sidecar_and_diagnosis_fields(tmp_path):
    _plant(tmp_path)
    extra = literature_sidecar(tmp_path)
    assert extra["search_hit_count"] == 1
    assert extra["unused_bib_titles"] == ["Unseen Title Paper"]
    assert extra["search_hits_path"] == "tool_calls/search_hits.jsonl"
    run = SimpleNamespace(
        save_path=str(tmp_path),
        status="failed",
        error="综述未点名 literature_review.bib 中的题名：Unseen Title Paper。",
        error_class="parse",
        progress_json="{}",
    )
    d = research_diagnosis(run)
    assert d["search_hit_count"] == 1
    assert "Unseen Title Paper" in d["unused_bib_titles"]
    run.status = "succeeded"
    run.error = None
    run.error_class = None
    ok = research_diagnosis(run)
    assert ok["search_hit_count"] == 1
    assert not ok.get("next_step")


def test_g89_run_detail_exposes_unused(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g89diag")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G89 diagnosis", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        dest = Path(row.save_path)
        _plant(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述未点名"
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["diagnosis"]["search_hit_count"] == 1
    assert "Unseen Title Paper" in (body["diagnosis"].get("unused_bib_titles") or [])
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    raw = listed.json()
    items = raw if isinstance(raw, list) else (raw.get("items") or [])
    hit = next(x for x in items if x["id"] == rid)
    assert hit["diagnosis"]["search_hit_count"] == 1
