from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.analysis import RUNNING_STALE_S, find_running_analysis, method_card_needs_preread


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


def _put_card(item_id: str, user_id: str, payload: dict) -> LiteratureAnalysis:
    db = SessionLocal()
    try:
        row = LiteratureAnalysis(
            item_id=item_id,
            analysis_type="method_card",
            result_json=json.dumps(payload, ensure_ascii=False),
            created_by=user_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def test_c6_detail_prefers_running_over_weak_card(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c6live")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Weak Then Running", "abstract": "abstract only"},
        headers=h,
    ).json()
    _put_card(
        paper["id"],
        uid,
        {"problem": {"text": "from abstract", "pages": []}, "source": "abstract_only"},
    )
    _put_card(paper["id"], uid, {"status": "running", "source": "pending", "warning": "正在提炼这篇怎么做…"})
    detail = client.get(f"/api/v1/literature/{paper['id']}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body.get("analysis_status") == "running"
    assert body.get("card_empty", {}).get("kind") == "running"
    assert any(a.get("is_running") for a in body.get("analyses") or [])


def test_c6_stale_running_expires_on_retry(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c6stale")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Stale Residual", "abstract": "We study residual detectors."},
        headers=h,
    ).json()
    row = _put_card(paper["id"], uid, {"status": "running", "source": "pending"})
    db = SessionLocal()
    try:
        live = db.get(LiteratureAnalysis, row.id)
        live.created_at = utcnow() - timedelta(seconds=RUNNING_STALE_S + 30)
        db.commit()
        assert find_running_analysis(db, paper["id"], expire=True) is None
        db.commit()
    finally:
        db.close()
    again = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert again.status_code == 200, again.text
    assert again.json().get("status") != "running" or again.json().get("result", {}).get("source") != "pending"


def test_c6_failed_shell_needs_preread_after_cooldown(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c6pre")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Failed Shell"}, headers=h).json()
    row = _put_card(paper["id"], uid, {"source": "parse_failed", "warning": "没提炼出这篇怎么做"})
    db = SessionLocal()
    try:
        assert method_card_needs_preread(db, paper["id"]) is False
        live = db.get(LiteratureAnalysis, row.id)
        live.created_at = utcnow() - timedelta(minutes=11)
        db.commit()
        assert method_card_needs_preread(db, paper["id"]) is True
    finally:
        db.close()
