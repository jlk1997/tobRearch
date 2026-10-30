from __future__ import annotations

from types import SimpleNamespace

from fengsuan.app.services.insight import digest_markdown, simulate_review
from fengsuan.app.services.research_engine import dishonest_reproduce_claims, research_output_gate


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
    body = {"literature_item_id": lit.json()["id"], "dataset_id": ds.json()["id"], "dataset_path": str(tmp_path)}
    if claimed is not None:
        body["claimed_metrics"] = claimed
    run = client.post(f"/api/v1/labs/{lab}/reproduce-runs", json=body, headers=h)
    assert run.status_code == 200, run.text
    return lit.json()["id"], run.json()["id"]


def test_h5135_review_flags_unmeasured_success(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5135rv")
    iid, _rid = _lit_and_run(client, h, lab, "Spine Review Paper", tmp_path, {"acc": 0.9})
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "评审稿G",
            "kind": "conference",
            "content_md": "引言\n\n方法\n\n实验\n\n结论：我们成功完成实验，复现了 Spine Review Paper，见 [@spinekey]。\n",
        },
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    cite = client.post(
        f"/api/v1/manuscripts/{ms.json()['id']}/citations",
        json={"literature_item_id": iid, "citation_key": "spinekey"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    sim = client.post(f"/api/v1/manuscripts/{ms.json()['id']}/review-simulate", headers=h)
    assert sim.status_code == 200, sim.text
    kinds = {x.get("kind") for x in sim.json().get("findings") or []}
    assert "ungrounded_reproduce" in kinds
    blob = " ".join(x.get("body") or "" for x in sim.json().get("findings") or [])
    assert "尚无实测" in blob
    assert "官方查重" in blob
    unit = simulate_review(
        "结论：成功完成实验 [@k]。",
        "other",
        ["k"],
        [],
        [{"key": "k", "title": "T", "item_id": "i1", "measured": False}],
    )
    assert any(x.get("kind") == "ungrounded_reproduce" for x in unit)


def test_h5136_digest_carries_spine(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5136dg")
    _lit_and_run(client, h, lab, "Digest Spine Paper", tmp_path, {"acc": 0.7})
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    spine = digest.json().get("reproduce_spine") or {}
    assert spine.get("not_run", 0) >= 1
    assert spine.get("measured", 0) == 0
    md = digest_markdown(digest.json())
    assert "未跑" in md
    assert "不是排行榜" in md


def test_h5137_stats_direction_spine(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5137st")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "统计G", "description": "", "keywords": []},
        headers=h,
    )
    assert d.status_code == 200, d.text
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Stats Dir Paper", "abstract": "cpu", "direction_ids": [d.json()["id"]]},
        headers=h,
    )
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "StatGSet"}, headers=h)
    client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit.json()["id"],
            "dataset_id": ds.json()["id"],
            "dataset_path": str(tmp_path),
            "claimed_metrics": {"acc": 0.6},
        },
        headers=h,
    )
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    row = next(x for x in (st.json().get("by_direction") or []) if x.get("direction_id") == d.json()["id"])
    assert row.get("reproduce_not_run", 0) >= 1
    assert row.get("reproduce_measured", 0) == 0
    assert row.get("reproduce_not_run_href") == f"/labs/{lab}/reproduces?status=failed"
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "reproduce_not_run" in csv.text


def test_h5138_progress_stamps_literature_evidence(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5138pr")
    iid, rid = _lit_and_run(client, h, lab, "Progress Spine Paper", tmp_path)
    prog = client.get(f"/api/v1/labs/{lab}/progress", headers=h)
    assert prog.status_code == 200, prog.text
    lit_rows = [
        x
        for x in (prog.json().get("items") or [])
        if x.get("object_type") == "literature" or x.get("object_id") == iid
    ]
    if lit_rows:
        ev = lit_rows[0].get("latest_reproduce")
        if ev:
            assert ev.get("id") == rid
            assert ev.get("measured") is False


def test_h5139_guide_lists_not_run(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5139gd")
    _lit_and_run(client, h, lab, "Guide Spine Paper", tmp_path)
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    keys = {x["key"]: x for x in home.json().get("guide") or []}
    assert "reproduce_not_run" in keys
    assert keys["reproduce_not_run"]["done"] is False
    assert keys["reproduce_not_run"]["href"] == f"/labs/{lab}/reproduces?status=failed"


def test_h5140_gate_blocks_fake_success(tmp_path):
    inj = {
        "literature": [
            {"id": "x", "title": "Fake Success Paper", "reproduce": {"measured": False}},
        ]
    }
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "literature_review.md").write_text("综述 Fake Success Paper。我们成功完成实验。", encoding="utf-8")
    (ws / "literature_review.bib").write_text(
        "@article{fake2020,\n  title={Fake Success Paper},\n  author={A},\n  year={2020},\n  journal={X}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "plan.md").write_text("计划引用 Fake Success Paper", encoding="utf-8")
    assert "成功完成实验" in dishonest_reproduce_claims(tmp_path, inj)
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    msg = research_output_gate(cfg, run, inj)
    assert "成功完成实验" in msg
    assert "禁止把声称当组内成功" in msg
    honest = {
        "literature": [{"id": "x", "title": "Fake Success Paper", "reproduce": {"measured": True}}],
    }
    assert dishonest_reproduce_claims(tmp_path, honest) == ""
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "workspace").mkdir()
    (clean / "workspace" / "literature_review.md").write_text("综述 Fake Success Paper。尚无实测。", encoding="utf-8")
    (clean / "plan.md").write_text("计划 Fake Success Paper", encoding="utf-8")
    assert dishonest_reproduce_claims(clean, inj) == ""


def test_h5141_research_artifacts_include_gaps(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5141ag")
    iid, _rid = _lit_and_run(client, h, lab, "Artifact Gap Paper", tmp_path)
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "研究问题", "literature_item_ids": [iid], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    inj = (run.json().get("injected") or {}).get("literature") or []
    assert inj
    assert inj[0].get("reproduce") is not None or inj[0].get("reproduce_brief")
    arts = client.get(f"/api/v1/research-runs/{run.json()['id']}/artifacts", headers=h)
    assert arts.status_code == 200, arts.text
    paths = {x.get("path") for x in arts.json().get("items") or []}
    assert "reproduce_gaps.md" in paths or "workspace/reproduce_gaps.md" in paths
