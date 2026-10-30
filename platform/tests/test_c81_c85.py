from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureAnalysis
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


def _add_trace(db, *, lab, user_id, session_id, q, sources=None, created_at=None):
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
            result_summary="2 篇" if sources else "0 篇",
            source_ids_json=json.dumps(sources or [], ensure_ascii=False),
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
            result_summary="有材料。" if sources else "没有。",
            source_ids_json=json.dumps(sources or [], ensure_ascii=False),
        )
    )
    db.flush()
    if created_at is not None:
        for row in db.query(AgentTrace).filter(AgentTrace.session_id == session_id).all():
            row.created_at = created_at


def test_c81_c84_direction_digest_week_empty_privacy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c8184")
    ada = _invite_student(client, h, lab, "c81ada@example.com", "Ada")
    zoe = _invite_student(client, h, lab, "c81zoe@example.com", "Zoe")
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
        _add_trace(db, lab=lab, user_id=aid, session_id="sess-c81-ada", q="组里残差论文", sources=["lit-1"])
        _add_trace(db, lab=lab, user_id=zid, session_id="sess-c81-zoe", q="空问茶壶")
        _add_trace(
            db,
            lab=lab,
            user_id=zid,
            session_id="sess-c81-old",
            q="上周空问",
            created_at=utcnow() - timedelta(days=10),
        )
        db.commit()
    finally:
        db.close()

    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    people = {p["user_id"]: p for p in ov.json().get("members") or []}
    assert not people[aid].get("week_empty_ai")
    assert any("空问茶壶" in (x.get("question") or "") for x in (people[zid].get("week_empty_ai") or []))
    assert not any("上周空问" in (x.get("question") or "") for x in (people[zid].get("week_empty_ai") or []))
    peer = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=ada).json()
    peer_people = {p["user_id"]: p for p in peer.get("members") or []}
    assert not peer_people[zid].get("week_empty_ai")
    assert peer_people[aid].get("week_empty_ai") == []

    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    empty = digest.json().get("week_empty_ai") or []
    names = [x.get("display_name") or "" for x in empty]
    assert names == sorted(names, key=lambda s: s.casefold())
    assert any("空问茶壶" in (x.get("question") or "") for x in empty)
    assert not any("上周空问" in (x.get("question") or "") for x in empty)
    md = digest_markdown(digest.json())
    assert "空问茶壶" in md
    assert "本周没查到材料（原话，不是模型改写）" in md
    scoped = client.get(f"/api/v1/labs/{lab}/digest", headers=h, params={"direction_id": other}).json()
    assert scoped.get("week_empty_ai") == []
    self_d = client.get(f"/api/v1/labs/{lab}/digest", headers=zoe).json()
    assert all(x.get("user_id") == zid for x in (self_d.get("week_empty_ai") or []))
    hidden = client.get(f"/api/v1/labs/{lab}/digest", headers=ada).json()
    assert not any(x.get("user_id") == zid for x in (hidden.get("week_empty_ai") or []))


def test_c82_graph_literature_can_hang_without_write(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c82graph")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Graph Hang C82", "abstract": "residual detector leftover", "direction_ids": []},
        headers=h,
    ).json()
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert graph.status_code == 200, graph.text
    node = next(n for n in (graph.json().get("nodes") or []) if n.get("id") == paper["id"])
    assert any(s.get("id") == did for s in (node.get("direction_suggestions") or []))
    after = client.get(f"/api/v1/literature/{paper['id']}", headers=h).json()
    assert did not in (after.get("direction_ids") or [])


def test_c83_search_cited_in_lab_written_card_only(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c83cite")
    src = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "How We Cite Residual C83", "abstract": "residual detector", "direction_ids": []},
        headers=h,
    ).json()
    weak = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Only Cite C83", "abstract": "residual detector", "direction_ids": []},
        headers=h,
    ).json()
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
                        "references": [{"title": "Outside Residual Survey C83", "doi": ""}],
                    },
                    ensure_ascii=False,
                ),
                created_by=uid,
                model="configured",
            )
        )
        db.add(
            LiteratureAnalysis(
                item_id=weak["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "source": "abstract_only",
                        "methods": {"text": "teaser"},
                        "references": [{"title": "Weak Outside Title C83", "doi": ""}],
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
    monkeypatch.setattr(
        "fengsuan.app.services.literature.cached_search_sources",
        lambda *a, **k: (
            [
                {"title": "Outside Residual Survey C83", "source": "arxiv", "abstract": "residual"},
                {"title": "Weak Outside Title C83", "source": "arxiv", "abstract": "residual"},
            ],
            False,
        ),
    )
    r = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "residual cite c83", "sources": ["arxiv"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items") or []
    hit = next(x for x in items if "Outside Residual Survey C83" in (x.get("title") or ""))
    assert hit.get("cited_in_lab")
    assert any(c.get("item_id") == src["id"] for c in hit["cited_in_lab"])
    weak_hit = next(x for x in items if "Weak Outside Title C83" in (x.get("title") or ""))
    assert not weak_hit.get("cited_in_lab")
    after = client.get(f"/api/v1/literature/{src['id']}", headers=h).json()
    assert after.get("direction_ids") == [] or after.get("direction_ids") is not None


def test_c85_recommend_leftover_hints_do_not_write(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c85hint")
    student = _invite_student(client, h, lab, "c85stu@example.com", "Stu")
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
    monkeypatch.setattr(
        "fengsuan.app.services.literature.cached_search_sources",
        lambda *a, **k: ([], False),
    )
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    hints = rec.json().get("keyword_hints") or []
    assert "teapot" in hints
    assert "residual" not in hints
    peer = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=student, params={"direction_id": did})
    assert peer.status_code == 200, peer.text
    assert not (peer.json().get("keyword_hints") or [])
    after = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h).json()
    assert "teapot" not in (after.get("keywords") or [])
