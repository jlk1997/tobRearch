from __future__ import annotations

from fengsuan.app.core.config import get_settings
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import UsageLedger
from fengsuan.app.services.writing import describe_writeback


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


def _line(body: dict, key: str) -> dict:
    return next(x for x in (body.get("situation") or {}).get("lines") or [] if x.get("key") == key)


def test_f6_situation_empty_and_worker_off(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f6sit")
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    body = st.json()
    assert body.get("situation", {}).get("worker_off") is True
    for key in ("people", "direction", "time", "honesty", "ai", "risk"):
        line = _line(body, key)
        assert line.get("empty") is True, key
        assert line.get("empty_text"), key
        assert line.get("next_step"), key
    assert "在读" in _line(body, "people")["empty_text"]
    assert "方向" in _line(body, "direction")["empty_text"]
    assert "阅读" in _line(body, "time")["empty_text"]
    assert "复现" in _line(body, "honesty")["empty_text"]
    assert "模型" in _line(body, "ai")["empty_text"]
    assert "风险" in _line(body, "risk")["empty_text"]
    assert body.get("ai", {}).get("empty") is True
    assert "记账" in (body.get("ai", {}).get("empty_text") or "")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert cockpit.json().get("situation", {}).get("worker_off") is True
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    assert "模型概述" in (digest.json().get("ai_brief_md") or "")
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "f6 pending worker", "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    queued = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert queued.status_code == 200, queued.text
    risk = _line(queued.json(), "risk")
    assert risk.get("empty") is False
    assert "工人未开" in (risk.get("text") or "")
    assert "pending" in (risk.get("href") or "")
    assert "失败" in (risk.get("next_step") or risk.get("text") or "")


def test_f6_quota_near_copy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f6qta")
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    limit = int(get_settings().lab_monthly_token_budget)
    db = SessionLocal()
    try:
        db.add(
            UsageLedger(
                lab_id=lab,
                user_id=me.json()["id"],
                feature="writing",
                prompt_tokens=int(limit * 0.85),
                completion_tokens=0,
            )
        )
        db.commit()
    finally:
        db.close()
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    ai = st.json().get("ai") or {}
    assert ai.get("quota_near") is True
    assert "预算" in (ai.get("quota_next") or "")
    assert "伪造" in (ai.get("quota_next") or "")
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "quota_near" in csv.text
    assert "empty" in csv.text
    assert "worker_off" in csv.text


def test_f6_search_empty_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f6src")
    miss = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "zzzz-no-hit-f6", "sources": ["lab"]},
        headers=h,
    )
    assert miss.status_code == 200, miss.text
    body = miss.json()
    assert body.get("items") == []
    nxt = body.get("empty_next") or {}
    assert "结果" in (nxt.get("title") or nxt.get("text") or "")
    steps = {x.get("key"): x for x in nxt.get("steps") or []}
    assert "sources" in steps and "pubmed" in (steps["sources"].get("href") or "").lower()
    assert "capture" in steps and "tab=manual" in (steps["capture"].get("href") or "")
    assert "upload" in steps and "literature" in (steps["upload"].get("href") or "")
    assert any("PDF" in (x.get("label") or "") for x in nxt.get("steps") or [])
    jump = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "zzzz-no-hit-f6"}, headers=h)
    assert jump.status_code == 200, jump.text
    assert jump.json().get("items") == []
    assert "tab=search" in (jump.json().get("search_href") or "")
    jsteps = jump.json().get("empty_next", {}).get("steps") or []
    assert any("tab=manual" in (x.get("href") or "") for x in jsteps)
    assert any("PDF" in (x.get("label") or "") for x in jsteps)


def test_f6_related_work_writeback_same_as_language(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f6wb")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F6稿", "kind": "other", "content_md": "## 引言\n先有一节。"},
        headers=h,
    ).json()
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    expected = describe_writeback(mode="append_heading", heading="相关工作")
    ok = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "RELATED_WORK_F6",
            "write_mode": "append_heading",
            "heading": "相关工作",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body.get("wrote") is True
    assert body.get("href") == f"/manuscripts/{ms['id']}"
    wb = body.get("writeback") or {}
    assert wb.get("mode") == "append_heading"
    assert "相关工作" in (wb.get("summary") or "")
    assert expected["summary"].split("（")[0] in (wb.get("summary") or "")
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "RELATED_WORK_F6" in got["content_md"]
