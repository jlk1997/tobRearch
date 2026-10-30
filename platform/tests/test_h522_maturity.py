from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.models.runs import ReproduceRun
from fengsuan.app.services.analysis import claimed_metrics_from_card


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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _insert_card(item_id: str, user_id: str, metrics):
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=item_id,
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "source": "pdf",
                        "problem": {"text": "real excerpt from paper body", "pages": [2]},
                        "metrics": metrics,
                    },
                    ensure_ascii=False,
                ),
                created_by=user_id,
            )
        )
        db.commit()
    finally:
        db.close()


def test_claimed_metrics_from_card_parses_list_and_strings():
    assert claimed_metrics_from_card({"metrics": [{"name": "acc", "value": 0.91}]}) == {"acc": 0.91}
    assert claimed_metrics_from_card({"metrics": ["F1: 0.8", "recall = 0.7"]}) == {"F1": 0.8, "recall": 0.7}
    assert claimed_metrics_from_card({"metrics": {"bleu": 32}}) == {"bleu": 32}
    assert claimed_metrics_from_card({"metrics": []}) == {}
    assert claimed_metrics_from_card(None) == {}


def test_h522_empty_claimed_fills_from_best_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h522cm")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Metric Paper", "abstract": "abs"},
        headers=h,
    ).json()
    me = client.get("/api/v1/auth/me", headers=h).json()
    _insert_card(paper["id"], me["id"], [{"name": "acc", "value": 0.91}, {"name": "f1", "value": 0.8}])
    filled = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": paper["id"], "claimed_metrics": {}},
        headers=h,
    )
    assert filled.status_code == 200, filled.text
    body = filled.json()
    assert body["claimed_metrics"]["acc"] == 0.91
    assert body["claimed_metrics"]["f1"] == 0.8
    assert body.get("claimed_metrics_source") == "method_card"
    assert body.get("actual_metrics") in ({}, None) or not body.get("actual_metrics")
    cancelled = client.post(f"/api/v1/reproduce-runs/{body['id']}/cancel", headers=h)
    assert cancelled.status_code == 200, cancelled.text
    explicit = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": paper["id"], "claimed_metrics": {"acc": 0.5}},
        headers=h,
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["claimed_metrics"]["acc"] == 0.5
    assert explicit.json().get("claimed_metrics_source") == "user"
    assert explicit.json().get("actual_metrics") in ({}, None) or not explicit.json().get("actual_metrics")


def test_h523_injected_literature_has_last_page_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h523inj")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Inject Page Paper"}, headers=h).json()
    prog = client.post(f"/api/v1/literature/{lit['id']}/progress", json={"page_no": 4, "page_count": 10}, headers=h)
    assert prog.status_code == 200, prog.text
    empty = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "empty", "domain": "general"}, headers=h)
    assert empty.status_code == 200, empty.text
    arts = client.get(f"/api/v1/research-runs/{empty.json()['id']}/artifacts", headers=h)
    assert arts.status_code == 200
    assert arts.json()["items"] == []
    cancelled = client.post(f"/api/v1/research-runs/{empty.json()['id']}/cancel", headers=h)
    assert cancelled.status_code == 200, cancelled.text
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [lit["id"]], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    detail = client.get(f"/api/v1/research-runs/{run.json()['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    inj = (detail.json().get("injected") or {}).get("literature") or []
    assert inj, detail.json()
    hit = next(x for x in inj if x.get("id") == lit["id"])
    assert hit.get("last_page_no") == 4
    assert "page=4" in (hit.get("href") or "")


def test_h524_home_cockpit_risk_strip(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h524rk")
    sa = _invite_student(client, h, lab, "h524a@example.com")
    sb = _invite_student(client, h, lab, "h524b@example.com")
    sid = client.get("/api/v1/auth/me", headers=sa).json()["id"]
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "风险方向", "description": ""}, headers=h).json()
    created = client.post(
        f"/api/v1/labs/{lab}/directions/{d['id']}/milestones",
        json={"kind": "proposal", "title": "开题截止", "owner_id": sid, "due_at": "2020-01-02"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert created.json().get("due_at")
    assert created.json().get("overdue") is True
    home_a = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sa).json()
    home_b = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sb).json()
    home_t = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    assert "queue" in home_a and "milestones_upcoming" in home_a
    assert any(x["id"] == created.json()["id"] for x in home_a["milestones_upcoming"])
    assert not any(x["id"] == created.json()["id"] for x in home_b["milestones_upcoming"])
    assert any(x["id"] == created.json()["id"] for x in home_t["milestones_upcoming"])
    me = client.get("/api/v1/auth/me", headers=h).json()
    db = SessionLocal()
    try:
        db.add(
            ReproduceRun(
                lab_id=lab,
                requester_id=me["id"],
                status="awaiting_gpu",
                current_stage="env_check",
                claimed_metrics_json="{}",
            )
        )
        db.add(
            ReproduceRun(
                lab_id=lab,
                requester_id=me["id"],
                status="blocked",
                current_stage="env_check",
                claimed_metrics_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()
    home_q = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    assert home_q["queue"]["awaiting_gpu"] >= 1
    assert home_q["queue"]["blocked"] >= 1
    assert home_q["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    assert home_q["queue"]["blocked_href"] == f"/labs/{lab}/reproduces?status=blocked"
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    ck = cockpit.json()
    assert ck["queue"]["awaiting_gpu"] >= 1
    assert any(x["id"] == created.json()["id"] for x in ck.get("milestones_upcoming") or [])


def test_h525_claims_attach_latest_reproduce(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h525cl")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Cite Paper"}, headers=h).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "主张稿", "kind": "other", "content_md": "见 citekey。"},
        headers=h,
    ).json()
    cite = client.post(
        f"/api/v1/manuscripts/{ms['id']}/citations",
        json={"literature_item_id": paper["id"], "citation_key": "citekey"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    before = client.get(f"/api/v1/manuscripts/{ms['id']}/claims", headers=h)
    assert before.status_code == 200, before.text
    row = before.json()["items"][0]
    assert row["reproduce_run_id"] is None
    assert f"literature_item_id={paper['id']}" in (row.get("reproduce_href") or "")
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": paper["id"], "claimed_metrics": {"acc": 0.9}},
        headers=h,
    )
    assert run.status_code == 200, run.text
    after = client.get(f"/api/v1/manuscripts/{ms['id']}/claims", headers=h).json()["items"][0]
    assert after["reproduce_run_id"] == run.json()["id"]
    assert after["reproduce_href"] == f"/reproduces/{run.json()['id']}"
    assert after.get("reproduce_status")


def test_h526_dataset_usage_count_and_description(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h526ds")
    ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "用量集", "description": "复现用公开集"},
        headers=h,
    )
    assert ds.status_code == 200, ds.text
    assert ds.json()["description"] == "复现用公开集"
    assert ds.json().get("usage_count") == 0
    listed = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert listed.status_code == 200, listed.text
    hit = next(x for x in listed.json() if x["id"] == ds.json()["id"])
    assert hit["description"] == "复现用公开集"
    assert hit.get("usage_count") == 0
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "用该数据集", "dataset_id": ds.json()["id"], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    listed2 = client.get(f"/api/v1/labs/{lab}/datasets", headers=h).json()
    hit2 = next(x for x in listed2 if x["id"] == ds.json()["id"])
    assert hit2.get("usage_count") == 1
    detail = client.get(f"/api/v1/datasets/{ds.json()['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("usage_count") == 1
