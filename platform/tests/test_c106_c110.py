from __future__ import annotations

from fengsuan.app.services.agent.features import followups_from_result, stamp_followups, stamp_trace


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


def test_c106_stamp_trace_steps_and_followups():
    pack = {
        "session_id": "sess-c106",
        "steps": [
            {"step": 1, "tool": "search_lab_literature", "result_summary": "2 篇", "error": ""},
            {"step": 2, "tool": "search_lab_kb", "result_summary": "", "error": "empty"},
        ],
    }
    out = stamp_trace({"next_step": {"label": "写回手稿", "href": "/labs/x/writing"}}, pack)
    assert out["session_id"] == "sess-c106"
    assert out["trace_id"] == "sess-c106"
    assert out["steps"][0]["label"] == "搜文献库"
    assert out["degraded"] == "tool_failures"
    assert "search_lab_kb" in (out.get("available_tools") or [])
    labels = [x.get("label") for x in (out.get("followups") or [])]
    assert "写回手稿" in labels


def test_c108_followups_after_next_step():
    payload = {"next_step": {"label": "打开写作", "href": "/labs/x/writing"}}
    stamp_followups(payload)
    assert payload["followups"][0]["href"] == "/labs/x/writing"
    again = {"followups": [{"label": "已有", "href": "/a"}], "next_step": {"label": "别覆盖", "href": "/b"}}
    stamp_followups(again)
    assert again["followups"][0]["label"] == "已有"
    assert followups_from_result({}) == []


def test_c106_c110_outline_compare_review_minutes(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c106ai")
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "残差检测综述", "kind": "survey", "literature_item_ids": []},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    body = ol.json()
    assert "followups" in body
    assert "session_id" in body or "trace_id" in body

    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Network Detection Alpha", "abstract": "residual", "direction_ids": []},
        headers=h,
    )
    b = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Network Detection Beta", "abstract": "residual nets", "direction_ids": []},
        headers=h,
    )
    assert a.status_code == 200 and b.status_code == 200
    cmp = client.post(
        f"/api/v1/labs/{lab}/literature/compare",
        json={"item_ids": [a.json()["id"], b.json()["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    cbody = cmp.json()
    assert "followups" in cbody
    assert "steps" in cbody or cbody.get("session_id")

    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "残差稿", "kind": "survey", "content_md": "# Intro\nHello.", "direction_ids": []},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    rev = client.post(f"/api/v1/manuscripts/{ms.json()['id']}/review-simulate", headers=h)
    assert rev.status_code == 200, rev.text
    assert "session_id" in rev.json() or "trace_id" in rev.json()

    meet = client.post(
        f"/api/v1/labs/{lab}/meetings",
        json={"title": "周会", "notes_raw": "行动: 先读残差那篇"},
        headers=h,
    )
    assert meet.status_code == 200, meet.text
    mins = client.post(
        f"/api/v1/meetings/{meet.json()['id']}/minutes",
        json={"notes_raw": "行动: 先读残差那篇", "assign_to": []},
        headers=h,
    )
    assert mins.status_code == 200, mins.text
    assert "session_id" in mins.json() or "trace_id" in mins.json()
    assert "minutes" in mins.json()
