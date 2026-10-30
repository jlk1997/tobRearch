from __future__ import annotations

from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.analysis import _tutor_messages
from fengsuan.app.services.insight import dataset_list_empty_next, member_scope_hrefs, object_open_href
from fengsuan.app.services.metrics import event_trend
from fengsuan.app.services.research_engine import persist_unused_diagnosis, stamp_notification_unused


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


def _tree(client, h, lab: str):
    pid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    cid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "多模态", "parent_id": pid, "keywords": ["multimodalkw"]},
        headers=h,
    ).json()["id"]
    sib = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "并列方向", "keywords": ["sibkw"]},
        headers=h,
    ).json()["id"]
    return pid, cid, sib


def _rows(payload):
    if isinstance(payload, list):
        return payload
    return list(payload.get("items") or [])


def test_g179_event_trend_parent_child_invalid(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g179trend")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Trend Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Trend Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert (
        client.post(
            f"/api/v1/literature/{child_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/literature/{sib_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=h,
        ).status_code
        == 200
    )
    me = client.get("/api/v1/auth/me", headers=h)
    uid = me.json()["id"]
    db = SessionLocal()
    try:
        emit_activity(
            db,
            lab_id=lab,
            actor_id=uid,
            verb="kb.query",
            object_type="kb",
            summary="child q",
            payload={"direction_id": cid},
        )
        emit_activity(
            db,
            lab_id=lab,
            actor_id=uid,
            verb="kb.query",
            object_type="kb",
            summary="lab q",
        )
        db.commit()
        parent_pts = event_trend(db, lab, days=7, direction_id=pid)
        child_pts = event_trend(db, lab, days=7, direction_id=cid)
        sib_pts = event_trend(db, lab, days=7, direction_id=sib)
        ghost = event_trend(db, lab, days=7, direction_id="missing-dir")
        wide = event_trend(db, lab, days=7)
        assert sum(p["reading_started"] for p in parent_pts) >= 1
        assert sum(p["kb_query"] for p in parent_pts) >= 1
        assert sum(p["reading_started"] for p in child_pts) >= 1
        assert sum(p["reading_started"] for p in sib_pts) >= 1
        assert sum(p["kb_query"] for p in sib_pts) == 0
        assert all(p["reading_started"] == 0 and p["kb_query"] == 0 for p in ghost)
        assert sum(p["reading_started"] for p in wide) >= 2
        assert sum(p["kb_query"] for p in wide) >= 2
        item = db.get(LiteratureItem, child_lit["id"])
        msgs, _ctx = _tutor_messages(db, item, uid, "方法是什么")
        blob = msgs[1]["content"]
        assert "multimodalkw" in blob
        assert "visionkw" not in blob
    finally:
        db.close()

    stats = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert stats.status_code == 200, stats.text
    points = (stats.json().get("trend") or {}).get("points") or []
    assert sum(int(p.get("reading_started") or 0) for p in points) >= 1
    ghost_stats = client.get(
        f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h
    )
    ghost_pts = (ghost_stats.json().get("trend") or {}).get("points") or []
    assert sum(int(p.get("reading_started") or 0) for p in ghost_pts) == 0
    href = object_open_href(lab, "kb", "", "kb.query", extra={"direction_id": cid})
    assert f"direction_id={cid}" in href
    scoped = member_scope_hrefs(lab, uid, direction_id=pid)
    assert f"direction_id={pid}" in scoped["reproduce_not_run_href"]
    assert "status=failed" in scoped["reproduce_not_run_href"]


def test_g180_datasets_direction_scope(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g180ds")
    pid, cid, sib = _tree(client, h, lab)
    parent_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Parent Dataset Paper", "direction_ids": [pid]},
        headers=h,
    ).json()
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Dataset Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Dataset Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    parent_ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "ParentSet", "literature_item_ids": [parent_lit["id"]]},
        headers=h,
    )
    child_ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "ChildSet", "literature_item_ids": [child_lit["id"]]},
        headers=h,
    )
    sib_ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "SibSet", "literature_item_ids": [sib_lit["id"]]},
        headers=h,
    )
    assert parent_ds.status_code == 200, parent_ds.text
    assert child_ds.status_code == 200, child_ds.text
    assert sib_ds.status_code == 200, sib_ds.text
    listed = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    parent_list = client.get(f"/api/v1/labs/{lab}/datasets", params={"direction_id": pid}, headers=h)
    parent_ids = {row["id"] for row in parent_list.json()}
    assert parent_ds.json()["id"] in parent_ids
    assert child_ds.json()["id"] in parent_ids
    assert sib_ds.json()["id"] not in parent_ids
    child_list = client.get(f"/api/v1/labs/{lab}/datasets", params={"direction_id": cid}, headers=h)
    child_ids = {row["id"] for row in child_list.json()}
    assert child_ds.json()["id"] in child_ids
    assert parent_ds.json()["id"] not in child_ids
    ghost = client.get(f"/api/v1/labs/{lab}/datasets", params={"direction_id": "missing-dir"}, headers=h)
    assert ghost.status_code == 200, ghost.text
    body = ghost.json()
    assert body.get("items") == []
    assert body.get("empty_kind") == "filter"
    empty = dataset_list_empty_next(lab, direction_id=pid, includes_children=True, empty_kind="filter")
    assert "小课题" in (empty.get("title") or "") + (empty.get("text") or "")


def test_g181_research_inject_and_notification_stamp(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g181inj")
    pid, cid, sib = _tree(client, h, lab)
    parent_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Parent Inject Paper", "direction_ids": [pid]},
        headers=h,
    ).json()
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Inject Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Inject Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    pin = client.patch(
        f"/api/v1/labs/{lab}/literature/{child_lit['id']}/core",
        json={"direction_id": cid, "is_core": True},
        headers=h,
    )
    assert pin.status_code == 200, pin.text
    sh = _invite_student(client, h, lab, "g181stu@example.com")
    picked = client.put(
        f"/api/v1/labs/{lab}/me/direction",
        json={"direction_id": pid, "direction_ids": [pid]},
        headers=sh,
    )
    assert picked.status_code == 200, picked.text
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G181 hung inject", "domain": "general"},
        headers=sh,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    injected = body.get("literature_item_ids") or []
    assert child_lit["id"] in injected
    assert parent_lit["id"] in injected
    assert sib_lit["id"] not in injected
    assert injected[0] == child_lit["id"]
    progress = body.get("progress") or {}
    assert progress.get("direction_id") == pid
    assert progress.get("injected_from_hung") is True
    assert progress.get("includes_children") is True

    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": pid, "direction_ids": [pid]},
            headers=h,
        ).status_code
        == 200
    )
    explicit = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={
            "question": "G181 explicit only",
            "domain": "general",
            "literature_item_ids": [sib_lit["id"]],
        },
        headers=h,
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json().get("literature_item_ids") == [sib_lit["id"]]
    assert (explicit.json().get("progress") or {}).get("injected_from_hung") is False
    assert (explicit.json().get("progress") or {}).get("direction_id") == pid

    rid = body["id"]
    dest = Path(tmp_path) / "g181run"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{z0,\n  title={Unused Hung TitleX},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述未点名"
        persist_unused_diagnosis(row)
        db.commit()
        notice = {"object_type": "research_run", "object_id": rid, "href": f"/runs/{rid}"}
        stamp_notification_unused(db, [notice])
        assert notice["href"] == f"/runs/{rid}"
        assert "Unused Hung TitleX" in (notice.get("unused_titles") or [])
        assert f"direction_id={pid}" in (notice.get("unused_research_href") or "")
        assert "unused=1" in (notice.get("unused_research_href") or "")
    finally:
        db.close()

    notes = client.get("/api/v1/notifications", headers=sh)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
    hit = [n for n in notes.json() if n.get("object_id") == rid]
    if hit:
        assert hit[0]["href"] == f"/runs/{rid}"
        assert f"direction_id={pid}" in (hit[0].get("unused_research_href") or "")
