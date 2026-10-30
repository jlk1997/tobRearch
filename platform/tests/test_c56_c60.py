from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.models.runs import ReproduceRun
from fengsuan.app.services.activity import emit_activity


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


def test_c56_member_profile_draws_returned_fields(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c56prof")
    sh = _invite_student(client, h, lab, "c56prof@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh).status_code == 200
    patched = client.patch(
        f"/api/v1/labs/{lab}/members/{sid}/profile",
        json={"skills": ["PyTorch"], "expected_graduate_at": "2028-06-01", "bio": "做残差", "interests": ["detect"]},
        headers=sh,
    )
    assert patched.status_code == 200, patched.text
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    prof = member.get("profile") or {}
    assert "残差检测" in [d.get("name") for d in (prof.get("directions") or [])]
    assert "PyTorch" in (prof.get("skills") or [])
    assert (prof.get("expected_graduate_at") or "").startswith("2028-06-01")
    for step in (member.get("stuck") or {}).get("steps") or []:
        assert step.get("href")


def test_c57_unread_reproduce_chips(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c57repro")
    sh = _invite_student(client, h, lab, "c57repro@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Unread Repro Paper"}, headers=h).json()
    assert client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 2, "page_count": 10},
        headers=sh,
    ).status_code == 200
    db = SessionLocal()
    try:
        db.add(
            ReproduceRun(
                lab_id=lab,
                requester_id=sid,
                literature_item_id=paper["id"],
                status="pending",
                current_stage="env_check",
                claimed_metrics_json="{}",
                actual_metrics_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    chips = card.get("unread_reproduce_titles") or []
    assert any(t.get("id") == paper["id"] for t in chips)
    assert any("/reproduces" in (t.get("href") or "") for t in chips)
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert any(t.get("id") == paper["id"] for t in member.get("unread_reproduce_titles") or [])


def test_c58_ask_lab_teacher_only(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c58ask")
    sh = _invite_student(client, h, lab, "c58ask@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    asked = client.post(f"/api/v1/labs/{lab}/ask", json={"q": "组里残差方向怎么走", "scope": "lab"}, headers=sh)
    if asked.status_code != 200:
        db = SessionLocal()
        try:
            emit_activity(
                db,
                lab_id=lab,
                actor_id=sid,
                verb="ask.lab",
                object_type="kb",
                summary="组里残差方向怎么走",
                payload={"scope": "lab"},
            )
            db.commit()
        finally:
            db.close()
    teacher_events = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json().get("recent_events") or []
    student_events = client.get(f"/api/v1/labs/{lab}/cockpit", headers=sh).json().get("recent_events") or []
    assert any(e.get("verb") == "ask.lab" for e in teacher_events)
    assert all(e.get("verb") != "ask.lab" for e in student_events)
    assert all(e.get("verb") != "kb.query" for e in teacher_events)
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h).json()
    assert any(x.get("verb") == "ask.lab" for x in digest.get("items") or [])
    student_digest = client.get(f"/api/v1/labs/{lab}/digest", headers=sh).json()
    assert all(x.get("verb") != "ask.lab" for x in student_digest.get("items") or [])


def test_c59_c60_tutor_grounding_and_fail_split(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c5960")
    sh = _invite_student(client, h, lab, "c5960@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    tutor_paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Tutor Ground Weak"}, headers=h).json()
    fail_paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Parse Fail Week"}, headers=h).json()
    weak_paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Abstract Only Weak"}, headers=h).json()
    assert client.post(
        f"/api/v1/literature/{weak_paper['id']}/progress",
        json={"page_no": 1, "page_count": 8},
        headers=sh,
    ).status_code == 200
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=tutor_paper["id"],
                analysis_type="tutor",
                result_json=json.dumps(
                    {"degraded": "budget", "grounded_ratio": 0.1, "ungrounded": True},
                    ensure_ascii=False,
                ),
                created_by=sid,
                model="chat",
            )
        )
        db.add(
            LiteratureAnalysis(
                item_id=fail_paper["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {"source": "parse_failed", "warning": "提炼超时，请再点「提炼方法」。"},
                    ensure_ascii=False,
                ),
                created_by=sid,
                model="stale",
            )
        )
        db.add(
            LiteratureAnalysis(
                item_id=weak_paper["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {"source": "abstract_only", "problem": {"text": "from abstract"}, "methods": {"text": "abs"}},
                    ensure_ascii=False,
                ),
                created_by=sid,
                model="configured",
            )
        )
        emit_activity(
            db,
            lab_id=lab,
            actor_id=sid,
            verb="analysis.tutor",
            object_type="literature",
            object_id=tutor_paper["id"],
            summary="Tutor Ground Weak",
        )
        emit_activity(
            db,
            lab_id=lab,
            actor_id=sid,
            verb="analysis.ready",
            object_type="literature",
            object_id=fail_paper["id"],
            summary="Parse Fail Week",
        )
        db.commit()
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    tutor = next(t for t in (card.get("week_tutor_titles") or []) if t.get("id") == tutor_paper["id"])
    assert tutor.get("degraded") or tutor.get("ground_weak")
    assert tutor.get("why") in {"降级", "接地弱"}
    fail_ids = {t.get("id") for t in card.get("week_fail_titles") or []}
    weak_ids = {t.get("id") for t in card.get("weak_card_titles") or []}
    assert fail_paper["id"] in fail_ids
    assert fail_paper["id"] not in weak_ids
    assert weak_paper["id"] in weak_ids
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert any(t.get("id") == fail_paper["id"] for t in member.get("week_fail_titles") or [])
    assert all(t.get("id") != fail_paper["id"] for t in member.get("weak_card_titles") or [])
    assert card.get("week_tutor") or member.get("week_tutor")
