from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureDirectionLink
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


def test_c96_c99_digest_stats_members_never_privacy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c9699")
    ada = _invite_student(client, h, lab, "c96ada@example.com", "Ada")
    zoe = _invite_student(client, h, lab, "c96zoe@example.com", "Zoe")
    silent = _invite_student(client, h, lab, "c96sid@example.com", "Sid")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    zid = client.get("/api/v1/auth/me", headers=zoe).json()["id"]
    sid = client.get("/api/v1/auth/me", headers=silent).json()["id"]
    db = SessionLocal()
    try:
        _add_trace(db, lab=lab, user_id=aid, session_id="sess-c96-src", q="组里残差论文", sources=["lit-1"])
        _add_trace(db, lab=lab, user_id=zid, session_id="sess-c96-empty", q="空问残差")
        _add_trace(
            db,
            lab=lab,
            user_id=zid,
            session_id="sess-c96-old",
            q="上周问过",
            created_at=utcnow() - timedelta(days=10),
        )
        db.commit()
    finally:
        db.close()

    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    never = digest.json().get("week_never_ai") or []
    names = [row.get("display_name") for row in never]
    assert "Sid" in names
    assert "Ada" not in names
    assert "Zoe" not in names
    assert names == sorted(names, key=lambda x: (str(x or "").casefold(),))
    md = digest_markdown(digest.json())
    assert "本周没问过" in md
    assert "搜文献库" in md
    assert "空问残差" in md

    peer_digest = client.get(f"/api/v1/labs/{lab}/digest", headers=ada).json()
    peer_never = {row.get("user_id") for row in (peer_digest.get("week_never_ai") or [])}
    assert sid not in peer_never
    sid_digest = client.get(f"/api/v1/labs/{lab}/digest", headers=silent).json()
    sid_never = [row.get("user_id") for row in (sid_digest.get("week_never_ai") or [])]
    assert sid_never == [sid]

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    by = {m["user_id"]: m for m in stats.json().get("by_member") or []}
    assert by[aid].get("week_never_ai") is False
    assert by[zid].get("week_never_ai") is False
    assert by[sid].get("week_never_ai") is True
    peer_stats = client.get(f"/api/v1/labs/{lab}/stats", headers=ada).json()
    peer_by = {m["user_id"]: m for m in peer_stats.get("by_member") or []}
    assert peer_by[sid].get("week_never_ai") is not True

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    people = {m["user_id"]: m for m in members.json()}
    assert "组里残差论文" in ((people[aid].get("last_ai") or {}).get("question") or "")
    assert people[sid].get("week_never_ai") is True
    peer_members = {m["user_id"]: m for m in client.get(f"/api/v1/labs/{lab}/members", headers=ada).json()}
    assert not peer_members[zid].get("last_ai")
    assert peer_members[sid].get("week_never_ai") is not True
    mine = next(m for m in client.get(f"/api/v1/labs/{lab}/members", headers=silent).json() if m["user_id"] == sid)
    assert mine.get("week_never_ai") is True


def test_c100_cockpit_readings_hang_do_not_write(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c100hang")
    tea_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Network Detection Study", "abstract": "residual nets", "direction_ids": []},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    rid = paper.json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    marked = client.post(f"/api/v1/literature/{rid}/mark-read", json={"status": "reading"}, headers=h)
    assert marked.status_code == 200, marked.text
    tea = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert tea.status_code == 200, tea.text
    card = next(m for m in tea.json()["members"] if m["user_id"] == tea_id)
    hit = next((x for x in (card.get("readings") or []) if x.get("id") == rid), None)
    assert hit, card.get("readings")
    names = [s.get("name") for s in (hit.get("direction_suggestions") or [])]
    assert "残差检测" in names
    detail = client.get(f"/api/v1/labs/{lab}/cockpit/members/{tea_id}", headers=h)
    assert detail.status_code == 200, detail.text
    now = next((x for x in (detail.json().get("reading_now") or []) if x.get("id") == rid), None)
    assert now, detail.json().get("reading_now")
    assert "残差检测" in [s.get("name") for s in (now.get("direction_suggestions") or [])]
    db = SessionLocal()
    try:
        n = (
            db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.item_id == rid, LiteratureDirectionLink.direction_id == did)
            .count()
        )
        assert n == 0
    finally:
        db.close()


def test_c101_kb_query_history_and_followups(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c101ask")
    empty = client.post(
        f"/api/v1/labs/{lab}/kb/query",
        json={"q": "组里有什么", "history": [{"q": "上一问", "a": "上一答"}]},
        headers=h,
    )
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert "followups" in body
    assert "session_id" in body
    assert isinstance(body.get("followups"), list)
