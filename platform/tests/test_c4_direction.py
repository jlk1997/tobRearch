from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis


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


def _dir(client, h, lab: str, name: str, keywords=None):
    r = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": name, "keywords": keywords or [], "description": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _paper(client, h, lab: str, title: str, abstract: str = "") -> dict:
    r = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": title, "abstract": abstract or title, "direction_ids": []},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _put_card(item_id: str, user_id: str, text: str) -> None:
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=item_id,
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "problem": {"text": text, "pages": [1]},
                        "claims": [{"text": text, "pages": [1]}],
                        "methods": {"text": text, "pages": [1]},
                        "source": "pdf",
                    },
                    ensure_ascii=False,
                ),
                created_by=user_id,
            )
        )
        db.commit()
    finally:
        db.close()


def test_c4_method_card_hangs_residual_english_to_chinese_direction(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c4res")
    a = _paper(client, h, lab, "Residual Anomaly Detector Alpha")
    b = _paper(client, h, lab, "Residual Anomaly Detector Beta")
    q1 = _paper(client, h, lab, "Quantum Computing Primer Notes")
    q2 = _paper(client, h, lab, "Quantum Computing Survey Notes")
    did = _dir(client, h, lab, "残差检测", [])
    before = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    hang_before = {x["item_id"] for x in (before.get("direction_gap") or {}).get("hangable") or []}
    assert a["id"] not in hang_before
    assert b["id"] not in hang_before
    _put_card(a["id"], uid, "We propose a residual detector. 残差检测。")
    _put_card(b["id"], uid, "A residual detector baseline. 残差检测。")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert pack.status_code == 200, pack.text
    body = pack.json()
    gap = body.get("direction_gap") or {}
    assert gap.get("scan_window") == 200
    hang_ids = {x["item_id"] for x in gap.get("hangable") or []}
    assert hang_ids == {a["id"], b["id"]}
    assert did in (gap["hangable"][0].get("direction_ids") or [])
    drafts = body.get("direction_drafts") or []
    draft_ids = set()
    for d in drafts:
        draft_ids |= set(d.get("item_ids") or [])
    assert q1["id"] in draft_ids
    assert q2["id"] in draft_ids
    assert a["id"] not in draft_ids
    assert "200" in (gap.get("text") or "")


def test_c4_get_does_not_write_links(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c4ro")
    paper = _paper(client, h, lab, "Residual Anomaly Detector Gamma")
    did = _dir(client, h, lab, "残差检测", [])
    _put_card(paper["id"], uid, "residual detector 残差检测")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    row = next(x for x in pack["items"] if x["id"] == paper["id"])
    assert did not in (row.get("direction_ids") or [])
    assert paper["id"] in {x["item_id"] for x in (pack.get("direction_gap") or {}).get("hangable") or []}
