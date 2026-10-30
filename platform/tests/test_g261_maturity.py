from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.activity import LabMetricDaily
from fengsuan.app.services.metrics import rollup_lab_day


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
    return pid, cid


def test_g261_parent_daily_legacy_not_backfilled(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g261roll")
    pid, cid = _tree(client, h, lab)
    client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Child Rollup Paper", "direction_ids": [cid]}, headers=h)

    yesterday = (utcnow().date() - timedelta(days=1)).isoformat()
    today = utcnow().date().isoformat()
    db = SessionLocal()
    try:
        db.add(
            LabMetricDaily(
                lab_id=lab,
                direction_id=pid,
                user_id="",
                day=yesterday,
                metrics_json=json.dumps({"literature_count": 7}),
            )
        )
        db.commit()
        assert rollup_lab_day(db, lab, force=True) is True
        db.commit()
        old = (
            db.query(LabMetricDaily)
            .filter(
                LabMetricDaily.lab_id == lab,
                LabMetricDaily.direction_id == pid,
                LabMetricDaily.user_id == "",
                LabMetricDaily.day == yesterday,
            )
            .first()
        )
        old_metrics = json.loads(old.metrics_json or "{}")
        assert old_metrics.get("literature_count") == 7
        assert not old_metrics.get("rollup_kind")
        fresh = (
            db.query(LabMetricDaily)
            .filter(
                LabMetricDaily.lab_id == lab,
                LabMetricDaily.direction_id == pid,
                LabMetricDaily.user_id == "",
                LabMetricDaily.day == today,
            )
            .first()
        )
        fresh_metrics = json.loads((fresh.metrics_json if fresh else "") or "{}")
        assert fresh_metrics.get("rollup_kind") == "parent_with_children"
        assert fresh_metrics.get("includes_children") is True
    finally:
        db.close()

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid, "days": 7}, headers=h)
    assert parent.status_code == 200, parent.text
    trend = parent.json().get("trend") or {}
    assert trend.get("legacy_rollup") is True
    assert "没有回填" in (trend.get("legacy_note") or "")
    assert trend.get("honest_from") == today
    days = {row.get("day"): row for row in (trend.get("points") or [])}
    assert days.get(yesterday, {}).get("literature_count") == 7
    assert not days.get(yesterday, {}).get("rollup_kind")

    child = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": cid, "days": 7}, headers=h)
    assert child.status_code == 200, child.text
    assert not (child.json().get("trend") or {}).get("legacy_rollup")

    lab_wide = client.get(f"/api/v1/labs/{lab}/stats", params={"days": 7}, headers=h)
    assert lab_wide.status_code == 200, lab_wide.text
    assert not (lab_wide.json().get("trend") or {}).get("legacy_rollup")

    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", params={"direction_id": pid, "days": 7}, headers=h)
    assert csv.status_code == 200, csv.text
    assert "没有回填" in csv.text

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)
