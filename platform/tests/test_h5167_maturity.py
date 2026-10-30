from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ReproduceRun
from fengsuan.app.services.research_engine import unused_injected_titles


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _lit_and_run(client, h, lab: str, title: str, tmp_path, claimed=None):
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": title, "abstract": "cpu"}, headers=h)
    assert lit.status_code == 200, lit.text
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": f"{title}Set"}, headers=h)
    assert ds.status_code == 200, ds.text
    body = {"literature_item_id": lit.json()["id"], "dataset_id": ds.json()["id"], "dataset_path": str(tmp_path)}
    if claimed is not None:
        body["claimed_metrics"] = claimed
    run = client.post(f"/api/v1/labs/{lab}/reproduce-runs", json=body, headers=h)
    assert run.status_code == 200, run.text
    return lit.json()["id"], run.json()["id"]


def test_h5167_unused_reads_lab_context_titles(tmp_path):
    title = "Context Unused Title Paper"
    ctx = {"literature": [{"id": "z", "title": title, "reproduce": {"measured": False}}]}
    (tmp_path / "lab_context.json").write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述。我们讨论了方法。", encoding="utf-8")
    (tmp_path / "plan.md").write_text("计划", encoding="utf-8")
    assert title in unused_injected_titles(tmp_path, {})
    (ws / "literature_review.md").write_text(f"综述。未使用：{title}。", encoding="utf-8")
    assert unused_injected_titles(tmp_path, {}) == []


def test_h5168_progress_and_cockpit_stamp_manuscript(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5168pr")
    title = "Progress Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "进展稿K", "kind": "other", "content_md": f"结论：我们成功完成实验。参见 {title}。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    prog = client.get(f"/api/v1/labs/{lab}/progress", headers=h)
    assert prog.status_code == 200, prog.text
    events = [x for x in (prog.json().get("items") or []) if x.get("object_id") == mid]
    assert events
    assert any(x.get("ungrounded_reproduce") for x in events)
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    recent = [x for x in (cockpit.json().get("recent_events") or []) if x.get("object_id") == mid]
    assert recent
    assert any(x.get("ungrounded_reproduce") for x in recent)


def test_h5169_direction_and_graph_mark_manuscript(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5169dr")
    title = "Hub Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "枢纽K", "description": "", "keywords": []},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "方向稿K",
            "kind": "conference",
            "content_md": f"结论：我们成功完成实验。参见 {title}。",
            "direction_ids": [did],
        },
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    row = next(x for x in (ov.json().get("manuscripts") or []) if x.get("id") == mid)
    assert row.get("ungrounded_reproduce")
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert graph.status_code == 200, graph.text
    node = next(x for x in (graph.json().get("nodes") or []) if x.get("id") == mid)
    assert node.get("type") == "manuscript"
    assert node.get("ungrounded_reproduce")


def test_h5171_guide_lists_ungrounded_manuscripts(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5171gd")
    title = "Guide Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "引导稿K", "kind": "other", "content_md": f"结论：我们成功完成实验。参见 {title}。"},
        headers=h,
    )
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    keys = {x["key"]: x for x in home.json().get("guide") or []}
    assert "manuscripts_ungrounded" in keys
    assert keys["manuscripts_ungrounded"]["done"] is False
    assert "ungrounded=1" in (keys["manuscripts_ungrounded"].get("href") or "")


def test_h5172_section_names_title_and_measured_not_false(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5172wr")
    title = "Section Mention Unmeasured Paper"
    iid, _rid = _lit_and_run(client, h, lab, title, tmp_path)
    sec = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "结论", "notes": f"我们成功完成实验，复现了 {title}。", "literature_item_ids": [iid]},
        headers=h,
    )
    assert sec.status_code == 200, sec.text
    warn = sec.json().get("ungrounded_reproduce") or ""
    assert "成功完成实验" in warn
    assert title in warn
    measured_title = "Already Measured Mention Paper"
    mid_lit, rid = _lit_and_run(client, h, lab, measured_title, tmp_path, {"acc": 0.9})
    db = SessionLocal()
    try:
        run = db.get(ReproduceRun, rid)
        run.actual_metrics_json = json.dumps({"acc": 0.41})
        run.status = "succeeded"
        run.error_class = None
        db.commit()
    finally:
        db.close()
    honest = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "已测稿K", "kind": "other", "content_md": f"结论：我们成功完成实验。参见 {measured_title}。"},
        headers=h,
    )
    assert honest.status_code == 200, honest.text
    assert not (honest.json().get("ungrounded_reproduce") or "")
    _ = mid_lit
