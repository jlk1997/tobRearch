"""向外看：库外论文去掉组里已有的，作者只说出现了几次。"""

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.lab import ResearchDirection
from fengsuan.app.services import frontier as frontier_mod


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access']}"}


def test_frontier_marks_outside_and_counts_authors(client, monkeypatch):
    h = _admin(client)
    lab = client.post("/api/v1/labs", json={"name": "前沿组", "code": "frontlab", "description": ""}, headers=h)
    lab_id = lab.json()["id"]
    direction = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "图神经网络", "description": "节点分类", "keywords": ["gnn"]},
        headers=h,
    )
    did = direction.json()["id"]

    def fake_search(query, sources, n, extra=""):
        return [
            {
                "title": "New Graph Paper",
                "authors": ["Ada Lovelace", "Alan"],
                "year": 2026,
                "doi": "10.1/new",
                "abstract": "a later graph method",
                "citation_count": 4,
                "source": "arxiv",
                "url": "https://arxiv.org/abs/1",
            },
            {
                "title": "New Graph Paper Two",
                "authors": ["Ada Lovelace"],
                "year": 2025,
                "doi": "10.1/two",
                "abstract": "another graph method",
                "citation_count": 2,
                "source": "openalex",
                "extra": {"institution": "Somewhere"},
            },
        ], False

    import fengsuan.app.services.literature as lit

    monkeypatch.setattr(lit, "cached_search_sources", fake_search)
    db = SessionLocal()
    try:
        row = db.get(ResearchDirection, did)
        out = frontier_mod.refresh_direction_frontier(db, lab_id, row, force=True)
        db.commit()
    finally:
        db.close()
    assert out["papers"]
    assert all(p.get("outside") for p in out["papers"])
    ada = next(a for a in out["authors"] if a["name"] == "Ada Lovelace")
    assert ada["count"] == 2
    assert "出现" in ada["said"]
    got = client.get(f"/api/v1/labs/{lab_id}/directions/{did}/frontier", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["papers"]
