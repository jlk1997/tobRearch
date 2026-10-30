from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureAnalysis


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


def _invite_student(client, h, lab: str, email: str, name: str = "学生"):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": name,
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c71_c74_cockpit_last_ai_privacy_and_ground(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c71ai")
    sh = _invite_student(client, h, lab, "c71on@example.com", "问过的")
    other = _invite_student(client, h, lab, "c71off@example.com", "没问的")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    oid = client.get("/api/v1/auth/me", headers=other).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=sid,
                session_id="sess-c71-src",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "组里残差论文"}, ensure_ascii=False),
                result_summary="2 篇",
                source_ids_json=json.dumps(["lit-1"], ensure_ascii=False),
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=sid,
                session_id="sess-c71-src",
                feature="kb_query",
                scope="lab",
                step=2,
                kind="answer",
                result_summary="有两篇。",
                source_ids_json=json.dumps(["lit-1"], ensure_ascii=False),
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=oid,
                session_id="sess-c71-empty",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "空问"}, ensure_ascii=False),
                result_summary="0 篇",
            )
        )
        db.commit()
    finally:
        db.close()
    tea = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    on = next(m for m in tea["members"] if m["user_id"] == sid)
    off = next(m for m in tea["members"] if m["user_id"] == oid)
    assert on.get("last_ai", {}).get("question", "").find("残差") >= 0
    assert on["last_ai"].get("ground_why") == "有组内材料"
    assert on["last_ai"].get("has_sources") is True
    assert "search_lab_literature" in (on["last_ai"].get("tools") or [])
    assert off.get("last_ai", {}).get("ground_why") == "没查到材料"
    assert off["last_ai"].get("has_sources") is False
    peer = client.get(f"/api/v1/labs/{lab}/cockpit", headers=other).json()
    peer_on = next(m for m in peer["members"] if m["user_id"] == sid)
    peer_me = next(m for m in peer["members"] if m["user_id"] == oid)
    assert not peer_on.get("last_ai")
    assert peer_me.get("last_ai", {}).get("ground_why") == "没查到材料"
    mine = client.get(f"/api/v1/labs/{lab}/cockpit", headers=sh).json()
    mine_row = next(m for m in mine["members"] if m["user_id"] == sid)
    assert mine_row.get("last_ai", {}).get("ground_why") == "有组内材料"


def test_c72_cite_edge_has_titles(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c72edge")
    src = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Citing Residual Net C72"}, headers=h).json()
    dst = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Deep Residual Learning C72"}, headers=h).json()
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=src["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "source": "pdf",
                        "methods": {"text": "residual stack", "pages": [1]},
                        "references": [{"title": "Deep Residual Learning C72", "doi": ""}],
                    },
                    ensure_ascii=False,
                ),
                created_by=uid,
                model="configured",
            )
        )
        db.commit()
    finally:
        db.close()
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert graph.status_code == 200, graph.text
    hit = next(
        e
        for e in (graph.json().get("edges") or [])
        if e.get("kind") == "cites" and e.get("source") == src["id"] and e.get("target") == dst["id"]
    )
    assert hit.get("via") == "method_card"
    assert "Deep Residual" in (hit.get("target_title") or "")
    assert hit.get("target_href")


def test_c73_leftover_hints_do_not_write(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c73hint")
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Teapot Calibration Protocol Alpha", "abstract": "teapot only", "direction_ids": []},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Teapot Calibration Protocol Beta", "abstract": "teapot kitchen", "direction_ids": []},
        headers=h,
    )
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    hints = ov.json().get("keyword_hints") or []
    assert "teapot" in hints
    assert "residual" not in hints
    before = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h).json()
    assert "teapot" not in (before.get("keywords") or [])


def test_c75_lab_related_has_hang_suggestions(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c75rel")
    related = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Anomaly Related C75", "abstract": "residual detector leftover", "direction_ids": []},
        headers=h,
    ).json()
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Hung Residual Backbone", "abstract": "residual detector", "direction_ids": [did]},
        headers=h,
    )
    monkeypatch.setattr(
        "fengsuan.app.services.literature.search_sources",
        lambda *a, **k: [],
    )
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    rows = rec.json().get("lab_related") or []
    hit = next(x for x in rows if x.get("id") == related["id"])
    assert hit.get("direction_suggestions")
    assert any(s.get("id") == did for s in hit["direction_suggestions"])
    after = client.get(f"/api/v1/literature/{related['id']}", headers=h).json()
    assert did not in (after.get("direction_ids") or [])
