from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.analysis import best_method_analysis, card_quality, ground_card
from fengsuan.app.services.writing import describe_writeback
from openlens_ai.agents.paper_reproducer.pipeline import compare_metrics, env_check


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


def test_describe_writeback_modes():
    append = describe_writeback(mode="append", version=2)
    assert "文末追加" in append["summary"]
    assert append["version"] == 2
    headed = describe_writeback(mode="append_heading", heading="相关工作")
    assert "相关工作" in headed["summary"]
    replaced = describe_writeback(mode="replace_selection", selection="old paragraph of text")
    assert "替换了选区" in replaced["summary"]
    assert replaced["selection_preview"]


def test_h58_writeback_summary_and_empty_mode(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h58wb")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "WB", "kind": "other", "content_md": "BODY"},
        headers=h,
    ).json()
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    idle = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "SHOULD_NOT_LAND", "manuscript_id": ms["id"], "write_mode": ""},
        headers=h,
    )
    assert idle.status_code == 200, idle.text
    assert not idle.json().get("wrote")
    assert "SHOULD_NOT_LAND" not in client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["content_md"]
    ok = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "APPENDED_FROM_H58",
            "write_mode": "append",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body.get("wrote") is True
    assert body.get("href") == f"/manuscripts/{ms['id']}"
    wb = body.get("writeback") or {}
    assert wb.get("mode") == "append"
    assert "文末追加" in (wb.get("summary") or "")
    assert wb.get("version") >= 2
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "APPENDED_FROM_H58" in got["content_md"]


def test_h59_best_card_not_overwritten_by_weak(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h59cd")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Card Paper", "abstract": "abs"}, headers=h).json()
    me = client.get("/api/v1/auth/me", headers=h).json()
    db = SessionLocal()
    try:
        weak = LiteratureAnalysis(
            item_id=paper["id"],
            analysis_type="method_card",
            result_json=json.dumps(
                {"source": "abstract_only", "problem": {"text": "", "ungrounded": True}, "warning": "仅摘要"},
                ensure_ascii=False,
            ),
            created_by=me["id"],
        )
        strong = LiteratureAnalysis(
            item_id=paper["id"],
            analysis_type="method_card",
            result_json=json.dumps(
                {"source": "pdf", "problem": {"text": "real excerpt from paper", "pages": [2]}},
                ensure_ascii=False,
            ),
            created_by=me["id"],
        )
        later_weak = LiteratureAnalysis(
            item_id=paper["id"],
            analysis_type="method_card",
            result_json=json.dumps({"source": "abstract_only", "problem": {"text": "later weak", "ungrounded": True}}, ensure_ascii=False),
            created_by=me["id"],
        )
        db.add_all([weak, strong, later_weak])
        db.commit()
        db.refresh(weak)
        db.refresh(strong)
        db.refresh(later_weak)
        best = best_method_analysis(db, paper["id"])
        assert best is not None
        assert best.id == strong.id
        assert card_quality(json.loads(best.result_json))["weak"] is False
    finally:
        db.close()
    detail = client.get(f"/api/v1/literature/{paper['id']}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    cards = [a for a in detail.json().get("analyses") or [] if a.get("analysis_type") == "method_card"]
    assert cards
    assert cards[0].get("is_best") is True
    assert (cards[0].get("result") or {}).get("source") == "pdf"
    other = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Other Card", "abstract": "y"}, headers=h).json()
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=other["id"],
                analysis_type="method_card",
                result_json=json.dumps({"source": "abstract_only", "problem": {"text": "only abs", "ungrounded": True}}, ensure_ascii=False),
                created_by=me["id"],
            )
        )
        db.commit()
    finally:
        db.close()
    cmp = client.post("/api/v1/literature/compare", json={"lab_id": lab, "item_ids": [paper["id"], other["id"]]}, headers=h)
    assert cmp.status_code == 200, cmp.text
    items = {x["id"]: x for x in cmp.json().get("items") or []}
    assert items[paper["id"]].get("card_quality", {}).get("weak") is False
    assert items[other["id"]].get("card_quality", {}).get("weak") is True


def test_h510_discovery_injection_visible(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h510inj")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Injected Paper"}, headers=h).json()
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "InjectSet"}, headers=h).json()
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [lit["id"]], "dataset_id": ds["id"], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    inj = run.json().get("injected") or {}
    assert any(x.get("title") == "Injected Paper" for x in inj.get("literature") or [])
    assert (inj.get("dataset") or {}).get("name") == "InjectSet"
    assert "未改" in (inj.get("note") or "") or "图" in (inj.get("note") or "")
    detail = client.get(f"/api/v1/research-runs/{run.json()['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    dinj = detail.json().get("injected") or {}
    assert (dinj.get("dataset") or {}).get("name") == "InjectSet"


def test_h511_lab_jump_lands_existing_objects(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h511jp")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Jump Paper Unique"}, headers=h).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Jump Manuscript Unique", "kind": "other", "content_md": "x"},
        headers=h,
    ).json()
    task = client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "Jump Task Unique"}, headers=h).json()
    empty = client.get(f"/api/v1/labs/{lab}/jump", params={"q": ""}, headers=h)
    assert empty.status_code == 200
    assert empty.json().get("items") == []
    hit = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Jump"}, headers=h)
    assert hit.status_code == 200, hit.text
    items = hit.json().get("items") or []
    types = {x["type"] for x in items}
    hrefs = {x["href"] for x in items}
    assert "literature" in types
    assert "manuscript" in types
    assert "task" in types
    assert any(lit["id"] in (x.get("href") or "") for x in items)
    assert f"/manuscripts/{ms['id']}" in hrefs
    assert f"open={task['id']}" in "".join(hrefs)


def test_agent_eval_honest_states(tmp_path):
    blocked = env_check(dataset_path="", gpu_mode="off", needs_gpu=False)
    assert blocked["status"] == "blocked"
    data = tmp_path / "tiny.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")
    waiting = env_check(dataset_path=str(data), gpu_mode="off", needs_gpu=True)
    assert waiting["status"] == "awaiting_gpu"
    gap = compare_metrics({"acc": 0.99}, {})
    assert gap["rows"][0]["claimed"] == 0.99
    assert gap["rows"][0]["actual"] is None
    filled = compare_metrics({"acc": 0.99}, {"acc": 0.5})
    assert filled["rows"][0]["actual"] == 0.5


def test_agent_eval_weak_pdf_excerpt():
    from fengsuan.app.services.analysis import locate_quote

    pages = ["This is the only real sentence in the paper about graphs."]
    weak = ground_card(
        {"methods": {"text": "我们发明了一种量子网络。", "quote": "We invent a quantum neural fusion transformer.", "pages": [1]}},
        pages,
    )
    assert weak["methods"].get("unverified") is True
    assert weak["methods"].get("text")
    assert not weak["methods"].get("ungrounded")
    ok = ground_card(
        {"methods": {"text": "文中只有这一句。", "quote": "only real sentence in the paper", "pages": [1]}},
        pages,
    )
    assert not ok["methods"].get("unverified")
    assert ok["methods"]["pages"] == [1]
    hyphen = ["object-\nseeking on high-resolution images uses ﬁlters."]
    assert locate_quote("object-seeking on high-resolution images uses filters.", hyphen, [1]) == 1


def test_claim_next_sql_fallback_without_redis(client):
    from fengsuan.app.core.concurrency import claim_next
    from fengsuan.app.models.runs import ResearchRun

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "qclaim")
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "queued", "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    rid = run.json()["id"]
    db = SessionLocal()
    try:
        claimed = claim_next(db, ResearchRun)
        assert claimed is not None
        assert claimed.id == rid
        assert claimed.status == "running"
        none = claim_next(db, ResearchRun)
        assert none is None
    finally:
        db.close()


def test_degree_milestones_on_direction_and_manuscript(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "deghub")
    direction = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图学习", "description": "", "keywords": ["gcn"]},
        headers=h,
    )
    assert direction.status_code == 200, direction.text
    did = direction.json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "开题稿", "kind": "proposal", "content_md": "draft", "direction_ids": [did]},
        headers=h,
    ).json()
    created = client.post(
        f"/api/v1/labs/{lab}/directions/{did}/milestones",
        json={"kind": "proposal", "title": "开题", "manuscript_id": ms["id"]},
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert created.json()["kind"] == "proposal"
    assert created.json()["manuscript_href"] == f"/manuscripts/{ms['id']}"
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    titles = [m["title"] for m in ov.json().get("milestones") or []]
    assert "开题" in titles
    patched = client.patch(
        f"/api/v1/labs/{lab}/milestones/{created.json()['id']}",
        json={"status": "in_progress"},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "in_progress"
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h)
    assert got.status_code == 200, got.text
    assert any(m.get("kind") == "proposal" for m in got.json().get("milestones") or [])
