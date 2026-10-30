from __future__ import annotations

import json
from types import SimpleNamespace

from fengsuan.app.services.reproduce import evidence_brief, reproduce_evidence
from fengsuan.app.services.research_engine import compose_injected_question
from openlens_ai.agents.paper_reproducer.pipeline import improve_hint


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


def test_h5119_evidence_never_marks_claimed_as_measured():
    run = SimpleNamespace(
        id="r1",
        status="failed",
        error_class="stub",
        claimed_metrics_json='{"acc": 0.99}',
        actual_metrics_json="{}",
        gap_report_md="尚无实测",
        save_path="",
        literature_item_id="lit1",
    )
    ev = reproduce_evidence(run)
    assert ev["measured"] is False
    assert ev["honest"] == "not_run"
    assert ev["actual_metrics"] == {}
    assert "成功完成实验" not in evidence_brief(ev)
    assert "尚无实测" in evidence_brief(ev)
    copied = SimpleNamespace(
        id="r2",
        status="succeeded",
        error_class=None,
        claimed_metrics_json='{"acc": 0.99}',
        actual_metrics_json='{"acc": 0.99}',
        gap_report_md="",
        save_path="",
        literature_item_id="lit1",
    )
    bad = reproduce_evidence(copied)
    assert bad["measured"] is False
    assert bad["actual_metrics"] == {}


def test_h5120_compare_gap_names_item_ids(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5120cp")
    a = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paper A Spine", "abstract": "cpu method"}, headers=h)
    b = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paper B Spine", "abstract": "cpu baseline"}, headers=h)
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    cmp = client.post(
        "/api/v1/literature/compare",
        json={"lab_id": lab, "item_ids": [a.json()["id"], b.json()["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    gap = (cmp.json().get("reproduce_gap_md") or "") + (cmp.json().get("gap_md") or "")
    assert a.json()["id"] in gap and b.json()["id"] in gap
    assert "尚无" in gap or "实测" in gap
    items = {x["id"]: x for x in cmp.json().get("items") or []}
    assert items[a.json()["id"]].get("reproduce") in (None, {}) or items[a.json()["id"]]["reproduce"].get("measured") in (False, None)


def test_h5121_detail_and_tutor_carry_evidence(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5121tu")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Tutor Spine", "abstract": "linear cpu"}, headers=h)
    assert lit.status_code == 200, lit.text
    iid = lit.json()["id"]
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "SpineSet"}, headers=h)
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": iid, "dataset_id": ds.json()["id"], "dataset_path": str(tmp_path), "claimed_metrics": {"acc": 0.9}},
        headers=h,
    )
    assert run.status_code == 200, run.text
    detail = client.get(f"/api/v1/literature/{iid}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    latest = detail.json().get("latest_reproduce") or {}
    assert latest.get("id") == run.json()["id"]
    assert latest.get("measured") is False
    assert latest.get("actual_metrics") in ({}, None)
    tut = client.post(f"/api/v1/literature/{iid}/tutor", json={"question": "组内复现成功了吗？"}, headers=h)
    assert tut.status_code == 200, tut.text
    ev = tut.json().get("reproduce") or {}
    assert ev.get("measured") is False


def test_h5122_outline_mentions_reproduce(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5122ol")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Outline Spine Paper", "abstract": "graphs"}, headers=h)
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "大纲", "kind": "conference", "literature_item_ids": [lit.json()["id"]]},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    blob = json.dumps(ol.json(), ensure_ascii=False)
    assert "复现" in blob or ol.json().get("used_reproduce") is True
    md = ol.json().get("markdown") or ""
    assert "复现" in md or "尚无" in (ol.json().get("notes") or "") or ol.json().get("used_reproduce")


def test_h5123_injection_includes_reproduce_brief(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5123in")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Inject Spine", "abstract": "cpu"}, headers=h)
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "InjSet"}, headers=h)
    client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": lit.json()["id"], "dataset_id": ds.json()["id"], "dataset_path": str(tmp_path)},
        headers=h,
    )
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "研究问题", "literature_item_ids": [lit.json()["id"]], "dataset_id": ds.json()["id"]},
        headers=h,
    )
    assert run.status_code == 200, run.text
    inj = run.json().get("injected") or {}
    lits = inj.get("literature") or []
    assert lits
    assert lits[0].get("reproduce_brief") or (lits[0].get("reproduce") is not None)
    q = compose_injected_question("研究问题", inj)
    assert "声称" in q or "实测" in q


def test_h5124_improve_hint_eats_lab_feedback():
    hint = improve_hint(
        {
            "diagnosis": {"kind": "not_run", "summary": "尚未得到实测指标", "next_step": "改进一轮"},
            "lab_feedback": "精读追问：学习率是多少？",
        }
    )
    assert "精读追问" in hint
    assert "尚未得到实测" in hint


def test_h5125_cockpit_and_stats_spine(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5125sp")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Spine Stats", "abstract": "cpu"}, headers=h)
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "StatSet"}, headers=h)
    client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": lit.json()["id"], "dataset_id": ds.json()["id"], "dataset_path": str(tmp_path), "claimed_metrics": {"acc": 0.8}},
        headers=h,
    )
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    spine = cockpit.json().get("reproduce_spine") or {}
    assert spine.get("href") == f"/labs/{lab}/reproduces"
    assert spine.get("measured", 0) == 0
    assert spine.get("not_run", 0) >= 1
    assert "awaiting_gpu_href" in (cockpit.json().get("queue") or {})
    assert (cockpit.json().get("queue") or {}).get("awaiting_gpu_href") == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    assert (stats.json().get("reproduce_spine") or {}).get("not_run", 0) >= 1
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "reproduce_spine" in csv.text
