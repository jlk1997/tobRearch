from __future__ import annotations

import json

from fengsuan.app.core.config import get_settings
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.literature import recommend_rank_key


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


def test_c61_recommend_cache_follows_direction_words(client, tmp_path, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c61cache")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    monkeypatch.setenv("FILE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    n = {"calls": 0}

    def fake(query, sources, max_per_source=8, language=None):
        n["calls"] += 1
        return [{"title": f"Ext Residual {n['calls']}", "doi": f"10.1000/c61-{n['calls']}", "source": "arxiv"}]

    monkeypatch.setattr("fengsuan.app.services.literature.search_sources", fake)
    try:
        first = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
        assert first.status_code == 200, first.text
        assert first.json().get("cached") is False
        assert "scholar" not in (first.json().get("sources_used") or [])
        second = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
        assert second.status_code == 200, second.text
        assert second.json().get("cached") is True
        after_second = n["calls"]
        patched = client.patch(f"/api/v1/directions/{did}", json={"keywords": ["anomaly", "detector"]}, headers=h)
        assert patched.status_code == 200, patched.text
        third = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
        assert third.status_code == 200, third.text
        assert third.json().get("cached") is False
        assert n["calls"] > after_second
    finally:
        get_settings.cache_clear()


def test_c62_equal_score_uses_citations_not_scholar():
    low = {"score": 0.4, "citation_count": 1, "journal": "", "title": "a"}
    high = {"score": 0.4, "citation_count": 90, "journal": "Nature", "title": "b"}
    better_score = {"score": 0.9, "citation_count": 0, "journal": "", "title": "c"}
    rows = [low, high, better_score]
    rows.sort(key=recommend_rank_key)
    assert rows[0]["title"] == "c"
    assert rows[1]["title"] == "b"


def test_c63_method_card_biblio_makes_cites(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c63cite")
    src = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Citing Residual Net"}, headers=h).json()
    dst = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Deep Residual Learning"}, headers=h).json()
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
                        "references": [{"title": "Deep Residual Learning", "doi": ""}],
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
    edges = graph.json().get("edges") or []
    hit = next(
        e
        for e in edges
        if e.get("kind") == "cites" and e.get("source") == src["id"] and e.get("target") == dst["id"]
    )
    assert hit.get("via") == "method_card"


def test_c64_overview_hangable_does_not_write(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c64hang")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Anomaly Detector C64", "abstract": "residual detector", "direction_ids": []},
        headers=h,
    ).json()
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=paper["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {"source": "pdf", "methods": {"text": "residual detector 残差检测", "pages": [1]}},
                    ensure_ascii=False,
                ),
                created_by=uid,
                model="configured",
            )
        )
        db.commit()
    finally:
        db.close()
    before = client.get(f"/api/v1/literature/{paper['id']}", headers=h).json()
    assert did not in (before.get("direction_ids") or [])
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    hang = ov.json().get("hangable") or []
    assert any(x.get("id") == paper["id"] for x in hang)
    row = next(x for x in hang if x.get("id") == paper["id"])
    assert row.get("direction_suggestions")
    after = client.get(f"/api/v1/literature/{paper['id']}", headers=h).json()
    assert did not in (after.get("direction_ids") or [])


def test_c65_ask_lists_available_tools(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c65tools")
    asked = client.post(f"/api/v1/labs/{lab}/ask", json={"q": "组里有哪些文献", "scope": "lab"}, headers=h)
    assert asked.status_code == 200, asked.text
    tools = asked.json().get("available_tools") or []
    assert tools
    assert "search_lab_literature" in tools


def test_c66_c69_direction_recent_ai_privacy_and_tools(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c66ai")
    sh = _invite_student(client, h, lab, "c66on@example.com", "方向内")
    other = _invite_student(client, h, lab, "c66off@example.com", "方向外")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    oid = client.get("/api/v1/auth/me", headers=other).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh).status_code == 200
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=sid,
                session_id="sess-c66-on",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "组里残差论文"}, ensure_ascii=False),
                result_summary="2 篇",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=oid,
                session_id="sess-c66-off",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_kb",
                args_json=json.dumps({"q": "方向外的问"}, ensure_ascii=False),
                result_summary="1 篇",
            )
        )
        db.commit()
    finally:
        db.close()
    tea = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h).json()
    rows = tea.get("recent_ai") or []
    assert any(x.get("user_id") == sid and "残差" in (x.get("question") or "") for x in rows)
    assert all(x.get("user_id") != oid for x in rows)
    mine = next(x for x in rows if x.get("user_id") == sid)
    assert "search_lab_literature" in (mine.get("tools") or [])
    stu = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=sh).json()
    assert all(x.get("user_id") == sid for x in (stu.get("recent_ai") or []))
    off = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=other).json()
    assert not (off.get("recent_ai") or [])
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert any("search_lab_literature" in (x.get("tools") or []) for x in (member.get("recent_ai") or []))


def test_c68_unmatched_does_not_write(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c68miss")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Teapot Calibration Protocol XYZ-C68", "abstract": "kitchenware only", "direction_ids": []},
        headers=h,
    ).json()
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    body = ov.json()
    assert any(x.get("id") == paper["id"] for x in (body.get("unmatched") or []))
    assert all(x.get("id") != paper["id"] for x in (body.get("hangable") or []))
    after = client.get(f"/api/v1/literature/{paper['id']}", headers=h).json()
    assert did not in (after.get("direction_ids") or [])
