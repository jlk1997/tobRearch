from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.metrics import build_funnel, process_daily_rollup, rollup_lab_day


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


def test_g137_funnel_empty_is_named_not_zero(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g137fun")
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    funnel = stats.json().get("funnel") or {}
    stages = funnel.get("stages") or []
    assert [s["key"] for s in stages][:3] == ["literature", "reading", "method_card"]
    assert funnel.get("empty") is True
    lit = next(s for s in stages if s["key"] == "literature")
    assert lit["count"] == 0
    reading = next(s for s in stages if s["key"] == "reading")
    assert reading["conversion"]["label"] == "暂无"
    assert all((a.get("label_text") == "暂无") for a in (stats.json().get("coverage") or {}).get("axes") or [])
    assert stats.json().get("trend", {}).get("empty_text")
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200
    assert b"funnel" in csv.content


def test_g138_rollup_is_idempotent(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g138roll")
    db = SessionLocal()
    try:
        first = rollup_lab_day(db, lab)
        db.commit()
        second = rollup_lab_day(db, lab)
        assert first is True
        assert second is False
    finally:
        db.close()
    process_daily_rollup()
    stats = client.get(f"/api/v1/labs/{lab}/stats?days=7", headers=h)
    assert stats.status_code == 200, stats.text
    assert "trend" in stats.json()
    assert "compare_month" in stats.json()
    rebuilt = build_funnel(lab, stats.json())
    assert rebuilt["stages"][0]["key"] == "literature"


def test_g139_direction_filter_funnel(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g139dirf")
    did = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "漏斗方向"}, headers=h).json()["id"]
    stats = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": did}, headers=h)
    assert stats.status_code == 200, stats.text
    assert stats.json()["funnel"]["direction_id"] == did
    assert stats.json()["coverage"]["note"].startswith("覆盖率")
