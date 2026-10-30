from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis


CLAIM = "convolution residual fusion on graphs"
METHOD = "we stack residual convolution blocks"


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _insert_card(item_id: str, user_id: str, card: dict, *, created_at: datetime | None = None) -> None:
    db = SessionLocal()
    try:
        row = LiteratureAnalysis(
            item_id=item_id,
            analysis_type="method_card",
            result_json=json.dumps(card, ensure_ascii=False),
            created_by=user_id,
        )
        if created_at is not None:
            row.created_at = created_at
        db.add(row)
        db.commit()
    finally:
        db.close()


def test_h599_outline_eats_best_method_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h599ol")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Card Aware Graph Paper", "abstract": "graphs"},
        headers=h,
    ).json()
    bare = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Bare Title Only Paper", "abstract": "no card yet"},
        headers=h,
    ).json()
    me = client.get("/api/v1/auth/me", headers=h).json()
    _insert_card(
        paper["id"],
        me["id"],
        {
            "source": "pdf",
            "problem": {"text": "real excerpt from paper body about graphs", "pages": [2]},
            "claims": [{"text": CLAIM, "pages": [2]}],
            "methods": {"text": METHOD, "pages": [2]},
            "limitations": {"text": "limited to small graphs in this excerpt", "pages": [2]},
        },
    )
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    body = ol.json()
    assert body.get("used_method_card") is True
    blob = json.dumps(body.get("outline") or {}, ensure_ascii=False) + (body.get("markdown") or "")
    assert CLAIM in blob or METHOD in blob
    assert "还没写方法" not in (body.get("notes") or "")
    bare_ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [bare["id"]]},
        headers=h,
    )
    assert bare_ol.status_code == 200, bare_ol.text
    assert "还没写方法" in (bare_ol.json().get("notes") or "")
    assert bare_ol.json().get("used_method_card") is False


def test_h5100_section_and_summarize_stamp_ungrounded(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5100ug")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Ground Section Paper", "abstract": "We propose a convolution method on graphs."},
        headers=h,
    ).json()
    kb = client.post(f"/api/v1/literature/{paper['id']}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    with patch("fengsuan.app.api.v1.language.chat", return_value="准确率达到 99.9%"):
        sec = client.post(
            f"/api/v1/labs/{lab}/writing/section",
            json={"heading": "相关工作", "notes": "写差距", "literature_item_ids": [paper["id"]]},
            headers=h,
        )
    assert sec.status_code == 200, sec.text
    assert sec.json().get("ungrounded") is True
    assert "缺少" in (sec.json().get("text") or "")
    with patch("fengsuan.app.services.llm.chat", return_value="准确率达到 99.9%"):
        sm = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    assert sm.json().get("ungrounded") is True
    assert "99.9" in (sm.json().get("summary") or "")


def test_h5101_citation_graph_uses_best_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5101cg")
    p1 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Best Card Node", "abstract": "a"},
        headers=h,
    ).json()
    p2 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Related Node", "abstract": "b"},
        headers=h,
    ).json()
    me = client.get("/api/v1/auth/me", headers=h).json()
    older = datetime.now(timezone.utc) - timedelta(days=2)
    newer = datetime.now(timezone.utc)
    _insert_card(
        p1["id"],
        me["id"],
        {
            "source": "pdf",
            "problem": {"text": "real excerpt from paper body about graphs", "pages": [3]},
            "related_in_lab": [{"literature_item_id": p2["id"]}],
        },
        created_at=older,
    )
    _insert_card(
        p1["id"],
        me["id"],
        {"source": "abstract_only", "problem": {"text": "later weak", "ungrounded": True}},
        created_at=newer,
    )
    g = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert g.status_code == 200, g.text
    node = next(x for x in (g.json().get("nodes") or []) if x.get("id") == p1["id"])
    assert node.get("source_page") == 3
    related = [e for e in (g.json().get("edges") or []) if e.get("kind") == "related"]
    assert any(e.get("source") == p1["id"] and e.get("target") == p2["id"] for e in related)


def test_h5102_recommend_marks_hash_embed_fallback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5102rc")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body.get("embed_fallback") == "hash"
    assert body.get("llm_rerank") is False


def test_h5103_chat_over_budget_is_quota(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5103qt")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Quota Paper", "abstract": "methods"},
        headers=h,
    ).json()
    with patch("fengsuan.app.services.llm.lab_tokens_this_month", return_value=10**12):
        ana = client.post(
            f"/api/v1/literature/{paper['id']}/analyze",
            json={"analysis_type": "method_card"},
            headers=h,
        )
        ol = client.post(
            f"/api/v1/labs/{lab}/writing/outline",
            json={"title": "综述", "kind": "survey"},
            headers=h,
        )
    assert ana.status_code == 429, ana.text
    assert ana.json().get("error_class") == "quota"
    step = ana.json().get("next_step") or ""
    assert "token" in step.lower() or "预算" in step
    assert ol.status_code == 429, ol.text
    assert ol.json().get("error_class") == "quota"
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"


def test_h5103_vision_purpose_uses_model_vision(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5103vs")
    put = client.put(
        "/api/v1/me/llm-credentials",
        json={
            "provider": "openai",
            "api_key": "sk-vis-test",
            "kind": "cloud",
            "model_chat": "gpt-4o-mini",
            "model_vision": "gpt-4o-vision-test",
        },
        headers=h,
    )
    assert put.status_code == 200, put.text
    me = client.get("/api/v1/auth/me", headers=h).json()
    db = SessionLocal()
    try:
        from fengsuan.app.services.llm import resolve_llm

        vision = resolve_llm(db, me["id"], lab, purpose="vision")
        chat = resolve_llm(db, me["id"], lab, purpose="chat")
        assert vision.model == "gpt-4o-vision-test"
        assert chat.model == "gpt-4o-mini"
        assert vision.purpose == "vision"
    finally:
        db.close()
