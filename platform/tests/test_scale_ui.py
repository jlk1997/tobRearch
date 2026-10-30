from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureItem, LiteratureReference
from fengsuan.app.services.biblio import save_references


def _h(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    return {"Authorization": f"Bearer {r.json()['access']}"}


def test_list_filters_by_author_level_and_citations(client):
    h = _h(client)
    lab = client.post("/api/v1/labs", json={"name": "ui", "code": "scui1", "description": ""}, headers=h).json()["id"]
    made = {}
    for title, names, cited in (
        ("Vision one", ["Wei Zhang", "Na Li"], 5),
        ("Vision two", ["Wei Zhang", "Na Li"], 300),
        ("Chem three", ["Bob Stone"], 40),
    ):
        r = client.post(f"/api/v1/labs/{lab}/literature", json={"title": title, "authors": names}, headers=h)
        assert r.status_code == 200, r.text
        made[title] = r.json()["id"]
    db = SessionLocal()
    try:
        for title, n in (("Vision one", 5), ("Vision two", 300), ("Chem three", 40)):
            it = db.get(LiteratureItem, made[title])
            it.cited_by_count = n
            it.read_level = 3 if title == "Vision two" else 1
        db.commit()
    finally:
        db.close()
    people = client.get(f"/api/v1/labs/{lab}/authors", params={"q": "Na Li"}, headers=h).json()["items"]
    na = people[0]
    page = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "author_id": na["id"]}, headers=h).json()
    assert {x["title"] for x in page["items"]} == {"Vision one", "Vision two"}
    assert page["author"]["name"] == "Na Li"
    assert page["items"][0]["author_links"][0]["name"] in {"Wei Zhang", "Na Li"}
    deep = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "read_level": "deep"}, headers=h).json()
    assert [x["title"] for x in deep["items"]] == ["Vision two"]
    cited = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "min_cited": 30, "sort": "cited"}, headers=h).json()
    assert [x["title"] for x in cited["items"]] == ["Vision two", "Chem three"]
    detail = client.get(f"/api/v1/authors/{na['id']}", headers=h).json()
    assert detail["coauthors"][0]["name"] == "Wei Zhang" and detail["coauthors"][0]["papers_together"] == 2
    assert sum(detail["evidence_counts"].values()) == 2
    status = client.get(f"/api/v1/labs/{lab}/literature/digest-status", headers=h).json()
    assert "month_left" in status and status["intake"]["levels"]["deep"] == 1


def test_adding_a_reference_links_it_back(client):
    h = _h(client)
    lab = client.post("/api/v1/labs", json={"name": "ui2", "code": "scui2", "description": ""}, headers=h).json()["id"]
    src = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Citing paper", "authors": []}, headers=h).json()["id"]
    db = SessionLocal()
    try:
        save_references(
            db,
            db.get(LiteratureItem, src),
            [{"title": "StegaStamp: Invisible Hyperlinks in Physical Photographs", "arxiv_id": "1904.05343", "year": 2020}],
            source="grobid",
        )
        db.commit()
    finally:
        db.close()
    rel = client.get(f"/api/v1/literature/{src}/relations", headers=h).json()
    ref = rel["references"][0]
    assert ref["in_lab_item_id"] == "" and ref["arxiv_id"] == "1904.05343"
    assert rel["lab_id"] == lab
    added = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": ref["title"], "arxiv_id": ref["arxiv_id"], "publish_year": ref["year"], "authors": []},
        headers=h,
    )
    assert added.status_code == 200, added.text
    db = SessionLocal()
    try:
        row = db.query(LiteratureReference).filter(LiteratureReference.citing_item_id == src).one()
        assert row.cited_item_id == added.json()["id"]
    finally:
        db.close()
    again = client.get(f"/api/v1/literature/{src}/relations", headers=h).json()
    assert again["references"][0]["in_lab_item_id"] == added.json()["id"]
