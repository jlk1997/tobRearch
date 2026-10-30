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


def test_c5_recommend_authors_and_citation_query(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c5auth")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={
            "title": "Residual Detector Lab Paper",
            "abstract": "We study residual detectors.",
            "authors": ["He Kaiming"],
            "direction_ids": [did],
        },
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=paper.json()["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "methods": {"text": "residual detector", "pages": [1]},
                        "related_works": [{"title": "Attention Is All You Need"}],
                        "source": "pdf",
                    },
                    ensure_ascii=False,
                ),
                created_by=uid,
            )
        )
        db.commit()
    finally:
        db.close()

    seen: list[str] = []

    def fake_search(query, sources, max_per_source=8, language=None):
        seen.append(query)
        return [
            {
                "title": f"Open hit for {query}",
                "abstract": "residual detector follow-up",
                "authors": ["He Kaiming"],
                "doi": f"10.1/{len(seen)}",
            }
        ]

    monkeypatch.setattr("fengsuan.app.services.literature.search_sources", fake_search)
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    body = rec.json()
    names = {row["name"] for row in body.get("authors") or []}
    assert "He Kaiming" in names
    he = next(row for row in body["authors"] if row["name"] == "He Kaiming")
    assert he["lab_count"] >= 1
    assert "tab=search" in (he.get("href") or "")
    assert any("Attention Is All You Need" in q for q in seen)
    assert "residual" in (body.get("query") or "").lower() or any("residual" in q.lower() for q in seen)


def test_c5_graph_has_author_nodes(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c5graph")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={
            "title": "Residual Detector Graph Paper",
            "authors": ["He Kaiming"],
            "direction_ids": [did],
        },
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h, params={"direction_id": did})
    assert graph.status_code == 200, graph.text
    body = graph.json()
    authors = [n for n in body.get("nodes") or [] if n.get("type") == "author"]
    assert authors
    assert authors[0]["title"] == "He Kaiming"
    assert "tab=search" in (authors[0].get("href") or "")
    wrote = [e for e in body.get("edges") or [] if e.get("kind") == "wrote"]
    assert wrote
    assert wrote[0]["target"] == paper.json()["id"]


def test_c5_empty_tables_still_have_empty_next(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c5empty")
    monkeypatch.setattr("fengsuan.app.services.literature.search_sources", lambda *_a, **_k: [])
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", json={}, headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert not body.get("items")
    assert not body.get("lab_unread")
    assert not body.get("lab_related")
    assert body.get("empty_next")
