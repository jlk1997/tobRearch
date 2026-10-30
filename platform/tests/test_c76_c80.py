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


def test_c76_c78_c80_direction_digest_empty_privacy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c7680")
    ada = _invite_student(client, h, lab, "c76ada@example.com", "Ada")
    zoe = _invite_student(client, h, lab, "c76zoe@example.com", "Zoe")
    old = _invite_student(client, h, lab, "c76old@example.com", "Old")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    zid = client.get("/api/v1/auth/me", headers=zoe).json()["id"]
    oid = client.get("/api/v1/auth/me", headers=old).json()["id"]
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
        _add_trace(db, lab=lab, user_id=aid, session_id="sess-c76-ada", q="组里残差论文", sources=["lit-1"])
        _add_trace(db, lab=lab, user_id=zid, session_id="sess-c76-zoe", q="空问茶壶")
        _add_trace(
            db,
            lab=lab,
            user_id=oid,
            session_id="sess-c76-old",
            q="上周空问",
            created_at=utcnow() - timedelta(days=10),
        )
        db.commit()
    finally:
        db.close()

    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    people = {p["user_id"]: p for p in ov.json().get("members") or []}
    assert "残差" in (people[aid].get("last_ai") or {}).get("question", "")
    assert (people[aid].get("last_ai") or {}).get("ground_why") == "有组内材料"
    assert (people[zid].get("last_ai") or {}).get("ground_why") == "没查到材料"
    peer = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=zoe).json()
    peer_people = {p["user_id"]: p for p in peer.get("members") or []}
    assert not peer_people[aid].get("last_ai")
    assert (peer_people[zid].get("last_ai") or {}).get("question", "").find("空问") >= 0

    tea = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    ada_card = next(m for m in tea["members"] if m["user_id"] == aid)
    zoe_card = next(m for m in tea["members"] if m["user_id"] == zid)
    old_card = next(m for m in tea["members"] if m["user_id"] == oid)
    assert not ada_card.get("week_empty_ai")
    assert any("空问茶壶" in (x.get("question") or "") for x in (zoe_card.get("week_empty_ai") or []))
    assert not any("上周空问" in (x.get("question") or "") for x in (old_card.get("week_empty_ai") or []))
    peer_cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=ada).json()
    peer_zoe = next(m for m in peer_cockpit["members"] if m["user_id"] == zid)
    assert not peer_zoe.get("week_empty_ai")
    mine = client.get(f"/api/v1/labs/{lab}/cockpit/members/{zid}", headers=zoe).json()
    assert any("空问茶壶" in (x.get("question") or "") for x in (mine.get("week_empty_ai") or []))
    hidden = client.get(f"/api/v1/labs/{lab}/cockpit/members/{zid}", headers=ada).json()
    assert not hidden.get("week_empty_ai")

    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    last = digest.json().get("last_ai") or []
    names = [x.get("display_name") or "" for x in last]
    assert "Ada" in names and "Zoe" in names
    assert names == sorted(names, key=lambda s: s.casefold())
    md = digest_markdown(digest.json())
    assert "组里残差论文" in md
    assert "原话，不是模型改写" in md
    assert "不是排名" in md
    scoped = client.get(f"/api/v1/labs/{lab}/digest", headers=h, params={"direction_id": other}).json()
    assert scoped.get("last_ai") == []
    self_d = client.get(f"/api/v1/labs/{lab}/digest", headers=zoe).json()
    self_names = [x.get("display_name") for x in (self_d.get("last_ai") or [])]
    assert self_names == ["Zoe"]
    assert all(x.get("user_id") == zid for x in (self_d.get("last_ai") or []))


def test_c77_cited_in_lab_written_card_only(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c77cite")
    src = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "How We Cite Residual C77", "abstract": "residual detector", "direction_ids": []},
        headers=h,
    ).json()
    weak = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Only Cite C77", "abstract": "residual detector", "direction_ids": []},
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
                item_id=src["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "source": "pdf",
                        "methods": {"text": "residual stack", "pages": [1]},
                        "references": [{"title": "Outside Residual Survey C77", "doi": ""}],
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
                        "references": [{"title": "Weak Outside Title C77", "doi": ""}],
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
                {"title": "Outside Residual Survey C77", "source": "arxiv", "abstract": "residual"},
                {"title": "Weak Outside Title C77", "source": "arxiv", "abstract": "residual"},
            ],
            False,
        ),
    )
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    items = rec.json().get("items") or []
    hit = next(x for x in items if "Outside Residual Survey C77" in (x.get("title") or ""))
    assert hit.get("cited_in_lab")
    assert any(c.get("item_id") == src["id"] for c in hit["cited_in_lab"])
    assert any("panel=card" in (c.get("href") or "") for c in hit["cited_in_lab"])
    weak_hit = next(x for x in items if "Weak Outside Title C77" in (x.get("title") or ""))
    assert not weak_hit.get("cited_in_lab")
    after = client.get(f"/api/v1/literature/{src['id']}", headers=h).json()
    assert did not in (after.get("direction_ids") or [])


def test_c79_search_library_can_hang_without_write(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c79hang")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Search Hang C79", "abstract": "residual detector leftover", "direction_ids": []},
        headers=h,
    ).json()
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    monkeypatch.setattr(
        "fengsuan.app.services.literature.cached_search_sources",
        lambda *a, **k: (
            [{"title": "Residual Search Hang C79", "source": "arxiv", "url": "https://arxiv.org/abs/c79"}],
            False,
        ),
    )
    r = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "residual hang c79", "sources": ["arxiv"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    hit = next(x for x in (r.json().get("items") or []) if x.get("already_in_library"))
    assert hit.get("id") == paper["id"]
    assert any(s.get("id") == did for s in (hit.get("direction_suggestions") or []))
    after = client.get(f"/api/v1/literature/{paper['id']}", headers=h).json()
    assert did not in (after.get("direction_ids") or [])
