from __future__ import annotations

from fengsuan.app.services.reproduce import merge_direction_note
from fengsuan.app.services.research_engine import unused_injected_titles, write_lab_context


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


def test_h5127_graph_nodes_carry_latest_reproduce(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5127gr")
    iid, rid = _lit_and_run(client, h, lab, "Graph Spine Paper", tmp_path, {"acc": 0.9})
    g = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert g.status_code == 200, g.text
    node = next(x for x in (g.json().get("nodes") or []) if x.get("id") == iid)
    latest = node.get("latest_reproduce") or {}
    assert latest.get("id") == rid
    assert latest.get("measured") is False
    assert latest.get("actual_metrics") in ({}, None)
    assert latest.get("honest") == "not_run"
    spine = g.json().get("reproduce_spine") or {}
    assert spine.get("not_run", 0) >= 1
    assert spine.get("measured", 0) == 0
    assert spine.get("href") == f"/labs/{lab}/reproduces"


def test_h5128_direction_overview_spine(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5128dr")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "方向F", "description": "", "keywords": ["cpu"]},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Dir Spine Paper", "abstract": "cpu", "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "DirSet"}, headers=h)
    client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit.json()["id"],
            "dataset_id": ds.json()["id"],
            "dataset_path": str(tmp_path),
            "claimed_metrics": {"acc": 0.8},
        },
        headers=h,
    )
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    paper = next(x for x in (ov.json().get("literature") or []) if x.get("id") == lit.json()["id"])
    latest = paper.get("latest_reproduce") or {}
    assert latest.get("measured") is False
    assert "成功完成实验" not in (latest.get("diagnosis_summary") or "")
    spine = ov.json().get("reproduce_spine") or {}
    assert spine.get("not_run", 0) >= 1
    assert spine.get("not_run_href") == f"/labs/{lab}/reproduces?status=failed"


def test_h5129_claims_carry_reproduce_dto(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5129cl")
    iid, rid = _lit_and_run(client, h, lab, "Claim Spine Paper", tmp_path, {"acc": 0.88})
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "主张F", "kind": "other", "content_md": "见 spinekey。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    cite = client.post(
        f"/api/v1/manuscripts/{ms.json()['id']}/citations",
        json={"literature_item_id": iid, "citation_key": "spinekey"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    claims = client.get(f"/api/v1/manuscripts/{ms.json()['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    row = claims.json()["items"][0]
    assert row["reproduce_run_id"] == rid
    ev = row.get("reproduce") or {}
    assert ev.get("id") == rid
    assert ev.get("measured") is False
    assert ev.get("honest") == "not_run"
    assert ev.get("actual_metrics") in ({}, None)


def test_h5130_kb_summarize_has_separate_gap(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5130kb")
    _lit_and_run(client, h, lab, "KB Spine Paper", tmp_path)
    sm = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    body = sm.json()
    assert "reproduce_gap_md" in body
    assert "summary" in body
    gap = body.get("reproduce_gap_md") or ""
    summary = body.get("summary") or ""
    assert gap == "" or "尚无实测" in gap or "实测" in gap
    assert "成功完成实验" not in summary


def test_h5131_home_spine_and_awaiting_href(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5131hm")
    _lit_and_run(client, h, lab, "Home Spine Paper", tmp_path, {"acc": 0.7})
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    spine = home.json().get("reproduce_spine") or {}
    assert spine.get("not_run", 0) >= 1
    assert spine.get("measured", 0) == 0
    assert spine.get("not_run_href") == f"/labs/{lab}/reproduces?status=failed"
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"


def test_h5132_sidecar_skipped_by_unused(tmp_path):
    inj = {
        "literature": [
            {
                "id": "lit-f",
                "title": "Only In Sidecar",
                "reproduce_brief": "尚无实测（stub）。禁止把论文声称写成成功结果。",
            }
        ],
        "note": "未改图边",
    }
    write_lab_context(tmp_path, inj)
    root = tmp_path / "reproduce_gaps.md"
    ws = tmp_path / "workspace" / "reproduce_gaps.md"
    assert root.is_file() and ws.is_file()
    text = root.read_text(encoding="utf-8")
    assert "Only In Sidecar" in text
    assert "尚无实测" in text
    assert "成功完成实验" not in text
    unused = unused_injected_titles(tmp_path, inj)
    assert "Only In Sidecar" in unused


def test_h5133_direction_note_replaces_heading(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5133nt")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "写回F", "description": "原文", "keywords": []},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    first = client.patch(
        f"/api/v1/directions/{did}",
        json={"append_note": "## 组内复现差距\n\nfirst-gap lit-a"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/api/v1/directions/{did}",
        json={"append_note": "## 组内复现差距\n\nsecond-gap lit-a"},
        headers=h,
    )
    assert second.status_code == 200, second.text
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    desc = ov.json().get("description") or ""
    assert desc.count("组内复现差距") == 1
    assert "second-gap" in desc
    assert "first-gap" not in desc
    same = merge_direction_note(desc, "## 组内复现差距\n\nsecond-gap lit-a")
    assert same == desc


def test_h5134_merge_note_keeps_other_sections():
    cur = "前言\n\n## 文献对比与差距\n\nold compare\n\n## 组内复现差距\n\nold repro"
    out = merge_direction_note(cur, "## 组内复现差距\n\nnew repro")
    assert out.count("组内复现差距") == 1
    assert "new repro" in out
    assert "old compare" in out
    assert "old repro" not in out
    again = merge_direction_note(out, "## 文献对比与差距\n\nnew compare")
    assert again.count("文献对比与差距") == 1
    assert "new compare" in again
    assert "new repro" in again
