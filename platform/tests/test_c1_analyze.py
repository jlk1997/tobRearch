from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureFile
from fengsuan.app.services.analysis import (
    paper_needs_defer,
    readable_method_card,
    running_method_card,
    select_card_batches,
)


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


def test_c1_select_card_batches_prefers_methods_page():
    batches = [[(i, f"page {i}")] for i in range(1, 8)]
    picked = select_card_batches(batches, structure={"sections": [{"title": "Methods", "page": 5}]})
    assert picked[0] is batches[0]
    assert any(5 in [p for p, _ in batch] for batch in picked)
    assert batches[-1] in picked
    assert len(picked) <= 4


def test_c1_paper_needs_defer_only_when_more_than_two_batches():
    short = ["abstract only"]
    assert paper_needs_defer(short) is False
    long_pages = ["x" * 4000] * 9
    assert paper_needs_defer(long_pages) is True


def test_c1_readable_method_card_keeps_said():
    card = readable_method_card(
        {
            "problem": {"text": "", "said": "the paper said residual", "pages": []},
            "methods": {"text": "", "said": "we use a residual detector", "pages": [2]},
            "source": "pdf",
        }
    )
    assert card["problem"].get("said") == "the paper said residual" or card["problem"].get("text")
    assert "residual" in json.dumps(card, ensure_ascii=False)


def test_c1_scan_fails_fast(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c1scan")
    paper = _paper(client, h, lab, "Scanned Notes", "")
    db = SessionLocal()
    try:
        db.add(
            LiteratureFile(
                item_id=paper["id"],
                file_path=f"/tmp/{paper['id']}.pdf",
                filename="scan.pdf",
                mime="application/pdf",
                size=12,
                parse_status="unusable",
                parse_error="scan",
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 400, r.text
    assert "可复制" in r.text


def test_c1_long_paper_returns_running(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c1run")
    paper = _paper(client, h, lab, "Long Residual Paper", "short abstract")
    pages = ["x" * 4000] * 9
    monkeypatch.setattr("fengsuan.app.services.pagecache.item_pages", lambda *_a, **_k: (pages, "pdf"))
    r = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "running"
    assert (body.get("result") or {}).get("status") == "running" or running_method_card()["status"] == "running"
    again = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert again.status_code == 200, again.text
    assert again.json().get("status") == "running"
