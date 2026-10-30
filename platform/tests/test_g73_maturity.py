from __future__ import annotations

import json

from fengsuan.app.services.analysis import filter_gap_md, ground_against_corpus
from fengsuan.app.services.writing import outline_honesty


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


def _paper(client, h, lab: str, title: str, abstract: str = "") -> dict:
    r = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": title, "abstract": abstract},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_g73_filter_and_ground_spans():
    kept = filter_gap_md("有出处 a1。这句没有编号。", ["a1"])
    assert "a1" in kept
    assert "没有编号" not in kept
    assert filter_gap_md("整表顶替，没有任何文献编号。", ["a1"]) == ""
    grounded = ground_against_corpus("作者报告准确率达到 99.9%。", "只有定性讨论", note="缺依据")
    assert grounded["ungrounded"] is True
    assert any("99.9" in s for s in grounded.get("spans") or [])


def test_g73_compare_drops_ungrounded_and_raw_table(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g73cmp")
    a = _paper(client, h, lab, "Compare A", "cpu method")
    b = _paper(client, h, lab, "Compare B", "cpu baseline")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: json.dumps(
            {
                "table_md": "这不是表格，是一段垃圾原文准确率 99.9%",
                "gap_md": "这句没有文献编号，不能写回。",
                "json": {"methods": [{"text": "无出处方法"}]},
            },
            ensure_ascii=False,
        ),
    )
    cmp = client.post(
        "/api/v1/literature/compare",
        json={"lab_id": lab, "item_ids": [a["id"], b["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    body = cmp.json()
    assert body.get("gap_md") == ""
    assert body.get("table_md") == ""
    assert not (body.get("related_work_md") or "").strip()
    assert body.get("empty_next")
    assert "短综述" in (body["empty_next"].get("title") or "")
    gap = body.get("reproduce_gap_md") or ""
    assert a["id"] in gap and b["id"] in gap
    assert "尚无" in gap or "实测" in gap


def test_g74_section_exposes_ungrounded_spans(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g74sec")
    paper = _paper(client, h, lab, "Ground Section Paper", "We propose a convolution method on graphs.")
    from unittest.mock import patch

    with patch("fengsuan.app.api.v1.language.chat", return_value="作者报告准确率达到 99.9%"):
        sec = client.post(
            f"/api/v1/labs/{lab}/writing/section",
            json={"heading": "相关工作", "notes": "写差距", "literature_item_ids": [paper["id"]]},
            headers=h,
        )
    assert sec.status_code == 200, sec.text
    body = sec.json()
    assert body.get("ungrounded") is True
    assert any("99.9" in str(s) for s in body.get("ungrounded_spans") or [])
    assert "writeback" not in body or not body.get("writeback")


def test_g75_outline_honesty_and_no_auto_writeback(client):
    honesty = outline_honesty(paper_titles=[], used_card=False, used_repro=False)
    assert honesty["notes"] == "未结合文献"
    assert honesty["honesty_notes"][0]["kind"] == "literature"
    with_papers = outline_honesty(paper_titles=["T"], used_card=False, used_repro=True)
    kinds = {n["kind"] for n in with_papers["honesty_notes"]}
    assert kinds == {"method_card", "reproduce"}
    assert "还没写方法" in with_papers["notes"]
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g75ol")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Keep", "kind": "other", "content_md": "ORIGINAL_BODY"},
        headers=h,
    ).json()
    paper = _paper(client, h, lab, "Outline Bare Paper", "graphs")
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [paper["id"]], "manuscript_id": ms["id"]},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    body = ol.json()
    assert "还没写方法" in (body.get("notes") or "")
    kinds = {n.get("kind") for n in body.get("honesty_notes") or []}
    assert "method_card" in kinds and "reproduce" in kinds
    assert body.get("writeback") in (None, {}, False) or "writeback" not in body
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert got["content_md"] == "ORIGINAL_BODY"
    bare = client.post(f"/api/v1/labs/{lab}/writing/outline", json={"title": "综述", "kind": "survey"}, headers=h)
    assert bare.status_code == 200, bare.text
    assert bare.json().get("notes") == "未结合文献"


def test_g76_review_simulate_unofficial_and_honesty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g76rv")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Draft", "kind": "conference", "content_md": "引言\n\n我们成功完成实验。\n\n结论"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    before = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    r = client.post(f"/api/v1/manuscripts/{mid}/review-simulate", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("official") is False
    assert "非知网" in (body.get("disclaimer") or "")
    assert body.get("ungrounded_reproduce")
    assert "成功完成实验" in body["ungrounded_reproduce"]
    after = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    assert after == before


def test_g77_kb_query_has_separate_gap(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g77kb")
    paper = _paper(client, h, lab, "KB Spine Paper", "UNIQUE_KB_TOKEN appears in methods.")
    kb = client.post(f"/api/v1/literature/{paper['id']}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "UNIQUE_KB_TOKEN"}, headers=h)
    assert q.status_code == 200, q.text
    body = q.json()
    assert "reproduce_gap_md" in body
    assert body.get("reproduce_gap_md") != (body.get("answer") or "x")
    assert paper["id"] in (body.get("reproduce_gap_md") or "")
    hits = body.get("hits") or []
    assert hits
    assert any(h.get("href") or h.get("literature_item_id") == paper["id"] for h in hits)
    sm = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    assert "reproduce_gap_md" in sm.json()
    assert sm.json().get("reproduce_gap_md") != (sm.json().get("summary") or "x")
