from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.services.insight import digest_markdown


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


def _add_trace(db, *, lab, user_id, session_id, q, sources=None, created_at=None, error=None, result=""):
    db.add(
        AgentTrace(
            lab_id=lab,
            user_id=user_id,
            session_id=session_id,
            feature="kb_query",
            scope="lab",
            step=1,
            kind="tool",
            tool="search_lab_literature",
            args_json=json.dumps({"q": q}, ensure_ascii=False),
            result_summary=result or ("2 篇" if sources else "0 篇"),
            source_ids_json=json.dumps(sources or [], ensure_ascii=False),
            error=error,
        )
    )
    db.add(
        AgentTrace(
            lab_id=lab,
            user_id=user_id,
            session_id=session_id,
            feature="kb_query",
            scope="lab",
            step=2,
            kind="answer",
            result_summary=result or ("有材料。" if sources else "没有。"),
            source_ids_json=json.dumps(sources or [], ensure_ascii=False),
            error=error,
        )
    )
    db.flush()
    if created_at is not None:
        for row in db.query(AgentTrace).filter(AgentTrace.session_id == session_id).all():
            row.created_at = created_at


def test_c86_c88_degraded_privacy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c8688")
    ada = _invite_student(client, h, lab, "c86ada@example.com", "Ada")
    zoe = _invite_student(client, h, lab, "c86zoe@example.com", "Zoe")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    zid = client.get("/api/v1/auth/me", headers=zoe).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    other = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "茶壶校准", "keywords": ["teapot"], "description": ""},
        headers=h,
    ).json()["id"]
    assert client.patch(f"/api/v1/labs/{lab}/members/{aid}", json={"direction_id": did}, headers=h).status_code == 200
    assert client.patch(f"/api/v1/labs/{lab}/members/{zid}", json={"direction_id": did}, headers=h).status_code == 200
    db = SessionLocal()
    try:
        _add_trace(db, lab=lab, user_id=aid, session_id="sess-c86-src", q="组里残差论文", sources=["lit-1"])
        _add_trace(db, lab=lab, user_id=zid, session_id="sess-c86-deg", q="降级问残差", error="degraded", result="budget: out")
        _add_trace(
            db,
            lab=lab,
            user_id=zid,
            session_id="sess-c86-old",
            q="上周降级",
            error="degraded",
            result="budget: out",
            created_at=utcnow() - timedelta(days=10),
        )
        db.commit()
    finally:
        db.close()

    tea = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    ada_card = next(m for m in tea["members"] if m["user_id"] == aid)
    zoe_card = next(m for m in tea["members"] if m["user_id"] == zid)
    assert not ada_card.get("week_degraded_ai")
    assert any("降级问残差" in (x.get("question") or "") for x in (zoe_card.get("week_degraded_ai") or []))
    assert not any("上周降级" in (x.get("question") or "") for x in (zoe_card.get("week_degraded_ai") or []))
    peer = client.get(f"/api/v1/labs/{lab}/cockpit", headers=ada).json()
    peer_zoe = next(m for m in peer["members"] if m["user_id"] == zid)
    assert not peer_zoe.get("week_degraded_ai")
    mine = client.get(f"/api/v1/labs/{lab}/cockpit/members/{zid}", headers=zoe).json()
    assert any("降级问残差" in (x.get("question") or "") for x in (mine.get("week_degraded_ai") or []))
    hidden = client.get(f"/api/v1/labs/{lab}/cockpit/members/{zid}", headers=ada).json()
    assert not hidden.get("week_degraded_ai")

    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    people = {p["user_id"]: p for p in ov.json().get("members") or []}
    assert not people[aid].get("week_degraded_ai")
    assert any("降级问残差" in (x.get("question") or "") for x in (people[zid].get("week_degraded_ai") or []))
    peer_ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=ada).json()
    peer_people = {p["user_id"]: p for p in peer_ov.get("members") or []}
    assert not peer_people[zid].get("week_degraded_ai")

    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    degraded = digest.json().get("week_degraded_ai") or []
    names = [x.get("display_name") or "" for x in degraded]
    assert names == sorted(names, key=lambda s: s.casefold())
    assert any("降级问残差" in (x.get("question") or "") for x in degraded)
    assert not any("上周降级" in (x.get("question") or "") for x in degraded)
    md = digest_markdown(digest.json())
    assert "降级问残差" in md
    assert "本周这次降级了（原话，不是模型改写）" in md
    scoped = client.get(f"/api/v1/labs/{lab}/digest", headers=h, params={"direction_id": other}).json()
    assert scoped.get("week_degraded_ai") == []
    self_d = client.get(f"/api/v1/labs/{lab}/digest", headers=zoe).json()
    assert all(x.get("user_id") == zid for x in (self_d.get("week_degraded_ai") or []))
    hidden_d = client.get(f"/api/v1/labs/{lab}/digest", headers=ada).json()
    assert not any(x.get("user_id") == zid for x in (hidden_d.get("week_degraded_ai") or []))


def test_c89_c90_leftover_hints_do_not_write(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c8990")
    student = _invite_student(client, h, lab, "c89stu@example.com", "Stu")
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
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h, params={"direction_id": did})
    assert graph.status_code == 200, graph.text
    hints = graph.json().get("keyword_hints") or []
    assert "teapot" in hints
    assert "residual" not in hints
    peer_g = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=student, params={"direction_id": did})
    assert peer_g.status_code == 200, peer_g.text
    assert not (peer_g.json().get("keyword_hints") or [])
    monkeypatch.setattr(
        "fengsuan.app.services.literature.cached_search_sources",
        lambda *a, **k: ([], False),
    )
    search = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "residual leftover c90", "sources": ["arxiv"], "direction_id": did},
        headers=h,
    )
    assert search.status_code == 200, search.text
    shints = search.json().get("keyword_hints") or []
    assert "teapot" in shints
    assert "residual" not in shints
    peer_s = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "residual leftover c90", "sources": ["arxiv"], "direction_id": did},
        headers=student,
    )
    assert peer_s.status_code == 200, peer_s.text
    assert not (peer_s.json().get("keyword_hints") or [])
    after = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h).json()
    assert "teapot" not in (after.get("keywords") or [])
