from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.analysis import analysis_followups, stamp_analysis_ai
from fengsuan.app.services.insight import _pack_ai_session


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


def test_c111_analysis_followups_and_stamp():
    item = SimpleNamespace(id="lit-c111", lab_id="lab-c111")
    good = analysis_followups(item, {})
    labels = [x.get("label") for x in good]
    assert "问这一页" in labels
    assert "去写作" in labels
    assert "开始复现" in labels
    assert all("/tutor" not in (x.get("href") or "") or "panel=tutor" in (x.get("href") or "") for x in good)

    empty = analysis_followups(item, {"kind": "abstract_only", "label": "尚无可用全文", "next_step": "请先上传"})
    assert empty[0]["label"] == "尚无可用全文"
    assert empty[0]["href"] == "/literature/lit-c111?panel=card"

    running = analysis_followups(item, {"kind": "running"})
    assert running[0]["label"] == "正在提炼"

    payload = {"next_step": {"label": "上传可复制 PDF", "href": "/literature/lit-c111?panel=card"}}
    stamp_analysis_ai(
        payload,
        item,
        empty={"kind": "abstract_only", "label": "尚无可用全文"},
        result={"session_id": "sess-c111", "steps": [{"tool": "get_paper_page_text", "label": "取页文字"}]},
    )
    assert payload["session_id"] == "sess-c111"
    assert payload["trace_id"] == "sess-c111"
    assert payload["steps"][0]["tool"] == "get_paper_page_text"
    assert payload["followups"][0]["label"] == "尚无可用全文"
    kept = {"followups": [{"label": "已有", "href": "/a"}]}
    stamp_analysis_ai(kept, item, empty=None, result={})
    assert kept["followups"][0]["label"] == "已有"


def test_c114_last_ai_href_from_session():
    tutor = SimpleNamespace(
        lab_id="lab-c114",
        user_id="u1",
        session_id="sess-tutor",
        feature="tutor",
        scope="paper",
        args_json=json.dumps({"item_id": "lit-9", "question": "方法怎么做"}, ensure_ascii=False),
        result_summary="",
        error=None,
        tool="get_paper_page_text",
        kind="tool",
        source_ids_json="[]",
        id="t1",
    )
    packed = _pack_ai_session([tutor])
    assert packed["href"].startswith("/literature/lit-9")
    assert "panel=tutor" in packed["href"]
    assert "session=sess-tutor" in packed["href"]

    kb = SimpleNamespace(
        lab_id="lab-c114",
        user_id="u1",
        session_id="sess-kb",
        feature="kb_query",
        scope="lab",
        args_json=json.dumps({"q": "组里残差"}, ensure_ascii=False),
        result_summary="2 篇",
        error=None,
        tool="search_lab_literature",
        kind="tool",
        source_ids_json='["a"]',
        id="k1",
    )
    kb_pack = _pack_ai_session([kb])
    assert kb_pack["href"].startswith("/labs/lab-c114/assistant?")
    assert "session=sess-kb" in kb_pack["href"]
    assert "组里残差" in kb_pack["question"]


def test_c111_c113_home_and_card_followups(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c111hm")
    ada = _invite_student(client, h, lab, "c111ada@example.com", "Ada")
    silent = _invite_student(client, h, lab, "c111sid@example.com", "Sid")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    sid = client.get("/api/v1/auth/me", headers=silent).json()["id"]

    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Network Detection C111", "abstract": "residual nets"},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    iid = paper.json()["id"]
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=iid,
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "problem": [{"text": "残差检测", "pages": [1]}],
                        "methods": [{"text": "残差块", "pages": [2]}],
                        "source": "pdf",
                        "session_id": "sess-card-c111",
                        "steps": [{"tool": "get_paper_structure", "label": "看章节"}],
                    },
                    ensure_ascii=False,
                ),
                created_by=uid,
                model="configured",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c111-ada",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "组里残差论文"}, ensure_ascii=False),
                result_summary="2 篇",
                source_ids_json='["lit-1"]',
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c111-ada",
                feature="kb_query",
                scope="lab",
                step=2,
                kind="answer",
                result_summary="有材料。",
                source_ids_json='["lit-1"]',
            )
        )
        db.commit()
    finally:
        db.close()

    detail = client.get(f"/api/v1/literature/{iid}", headers=h)
    assert detail.status_code == 200, detail.text
    cards = [a for a in (detail.json().get("analyses") or []) if a.get("analysis_type") == "method_card"]
    assert cards
    card = cards[0]
    assert card.get("session_id") == "sess-card-c111" or (card.get("result") or {}).get("session_id") == "sess-card-c111"
    labels = [x.get("label") for x in (card.get("followups") or (card.get("result") or {}).get("followups") or [])]
    assert "问这一页" in labels
    assert "去写作" in labels

    ada_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=ada)
    assert ada_home.status_code == 200, ada_home.text
    last = ada_home.json().get("last_ai") or {}
    assert "组里残差论文" in (last.get("question") or "")
    assert (last.get("href") or "").startswith(f"/labs/{lab}/assistant?")
    assert "session=sess-c111-ada" in (last.get("href") or "")
    assert ada_home.json().get("week_never_ai") is False

    sid_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=silent)
    assert sid_home.status_code == 200, sid_home.text
    assert sid_home.json().get("week_never_ai") is True
    peer = sid_home.json().get("last_ai")
    assert not peer or peer.get("user_id") == sid


def test_c111_week_never_keeps_old_question(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c111old")
    ada = _invite_student(client, h, lab, "c111old@example.com", "Old")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    db = SessionLocal()
    try:
        row = AgentTrace(
            lab_id=lab,
            user_id=aid,
            session_id="sess-old-c111",
            feature="kb_query",
            scope="lab",
            step=1,
            kind="tool",
            tool="search_lab_literature",
            args_json=json.dumps({"q": "上周问过残差"}, ensure_ascii=False),
            result_summary="1 篇",
            source_ids_json='["a"]',
        )
        db.add(row)
        db.flush()
        row.created_at = utcnow() - timedelta(days=10)
        db.commit()
    finally:
        db.close()
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=ada).json()
    assert home.get("week_never_ai") is True
    assert "上周问过残差" in ((home.get("last_ai") or {}).get("question") or "")
