from __future__ import annotations

import json
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ReproduceRun
from fengsuan.app.services.reproduce import pick_item_reproduce_run, reproduce_evidence


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


def _as_items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


def test_h5151_pick_prefers_measured():
    older = SimpleNamespace(
        id="old",
        status="succeeded",
        error_class=None,
        claimed_metrics_json='{"acc": 0.9}',
        actual_metrics_json='{"acc": 0.41}',
        gap_report_md="",
        save_path="",
        literature_item_id="lit1",
    )
    newer = SimpleNamespace(
        id="new",
        status="failed",
        error_class="stub",
        claimed_metrics_json='{"acc": 0.9}',
        actual_metrics_json="{}",
        gap_report_md="",
        save_path="",
        literature_item_id="lit1",
    )
    picked = pick_item_reproduce_run([newer, older])
    assert picked is older
    assert reproduce_evidence(picked)["measured"] is True
    assert pick_item_reproduce_run([newer]).id == "new"


def test_h5152_latest_does_not_hide_measured(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5152pm")
    iid, rid1 = _lit_and_run(client, h, lab, "Prefer Measured Paper", tmp_path, {"acc": 0.9})
    ds = client.get(f"/api/v1/labs/{lab}/datasets", headers=h).json()
    rid2 = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": iid, "dataset_id": ds[0]["id"], "dataset_path": str(tmp_path), "claimed_metrics": {"acc": 0.9}},
        headers=h,
    ).json()["id"]
    db = SessionLocal()
    try:
        first = db.get(ReproduceRun, rid1)
        first.actual_metrics_json = json.dumps({"acc": 0.41})
        first.status = "succeeded"
        first.error_class = None
        db.commit()
        later = db.get(ReproduceRun, rid2)
        later.gap_report_md = (later.gap_report_md or "") + "retry"
        db.commit()
    finally:
        db.close()
    rows = _as_items(client.get(f"/api/v1/labs/{lab}/literature", headers=h).json())
    item = next(x for x in rows if x.get("id") == iid)
    ev = item.get("latest_reproduce") or {}
    assert ev.get("measured") is True
    assert ev.get("id") == rid1
    assert ev.get("id") != rid2
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert (home.json().get("reproduce_spine") or {}).get("measured", 0) >= 1


def test_h5153_not_run_includes_blocked(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5153bl")
    iid, rid = _lit_and_run(client, h, lab, "Blocked Spine Paper", tmp_path)
    db = SessionLocal()
    try:
        run = db.get(ReproduceRun, rid)
        run.status = "blocked"
        run.error_class = "need_data"
        db.commit()
    finally:
        db.close()
    not_run = _as_items(client.get(f"/api/v1/labs/{lab}/literature", params={"reproduce": "not_run"}, headers=h).json())
    assert iid in {x.get("id") for x in not_run}
    blocked = _as_items(client.get(f"/api/v1/labs/{lab}/literature", params={"reproduce": "blocked"}, headers=h).json())
    assert iid in {x.get("id") for x in blocked}
    runs = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"honest": "not_run"}, headers=h)
    assert runs.status_code == 200, runs.text
    hit = next(x for x in runs.json() if x.get("id") == rid)
    assert hit.get("honest") == "blocked"
    assert hit.get("measured") is False


def test_h5154_warning_sees_mentioned_title(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5154tt")
    iid_a, rid_a = _lit_and_run(client, h, lab, "Cited Measured Paper", tmp_path, {"acc": 0.8})
    iid_b, _rid_b = _lit_and_run(client, h, lab, "Mention Spine Paper", tmp_path, {"acc": 0.7})
    db = SessionLocal()
    try:
        run = db.get(ReproduceRun, rid_a)
        run.actual_metrics_json = json.dumps({"acc": 0.33})
        run.status = "succeeded"
        run.error_class = None
        db.commit()
    finally:
        db.close()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "点名稿I", "kind": "conference", "content_md": "引言"},
        headers=h,
    )
    mid = ms.json()["id"]
    client.post(f"/api/v1/manuscripts/{mid}/citations", json={"literature_item_id": iid_a, "citation_key": "citedk"}, headers=h)
    stamp = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["updated_at"]
    patched = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={
            "content_md": "结论：我们成功完成实验，对照 Mention Spine Paper。",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    warn = patched.json().get("ungrounded_reproduce") or ""
    assert "Mention Spine Paper" in warn
    assert "成功完成实验" in warn
    filtered = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"ungrounded": True}, headers=h)
    assert filtered.status_code == 200, filtered.text
    assert mid in {x.get("id") for x in filtered.json()}
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    card = next(x for x in (home.json().get("manuscripts") or []) if x.get("id") == mid)
    assert card.get("ungrounded_reproduce")
    jump = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "点名稿"}, headers=h)
    assert jump.status_code == 200, jump.text
    jms = next(x for x in (jump.json().get("items") or []) if x.get("id") == mid)
    assert jms.get("ungrounded_reproduce")
    sim = client.post(f"/api/v1/manuscripts/{mid}/review-simulate", headers=h)
    assert sim.status_code == 200, sim.text
    kinds = {x.get("kind") for x in sim.json().get("findings") or []}
    assert "ungrounded_reproduce" in kinds
    _ = iid_b


def test_h5156_lab_search_stamps_evidence(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5156sr")
    iid, rid = _lit_and_run(client, h, lab, "Search Spine Paper", tmp_path)
    hit = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "Search Spine", "sources": ["lab"]},
        headers=h,
    )
    assert hit.status_code == 200, hit.text
    row = next(x for x in (hit.json().get("items") or []) if x.get("id") == iid)
    ev = row.get("latest_reproduce") or {}
    assert ev.get("id") == rid
    assert ev.get("measured") is False
    assert row.get("reproduce_href")
