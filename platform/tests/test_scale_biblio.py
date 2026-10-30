from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.identity import User
from fengsuan.app.models.literature import Author, Authorship, LiteratureItem, LiteratureReference
from fengsuan.app.services.authors import find_authors, merge_authors, name_key, split_author
from fengsuan.app.services.biblio import link_item_as_cited, save_references, sync_authorships
from fengsuan.app.services.relations import related_papers
from fengsuan.app.services.tei import chunk_structured, parse_tei

TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader>
  <fileDesc>
   <titleStmt><title level="a" type="main">Camera Resilient Watermarking</title></titleStmt>
   <sourceDesc><biblStruct><analytic>
    <author><persName><forename type="first">Wei</forename><surname>Zhang</surname></persName>
      <idno type="ORCID">0000-0002-1825-0097</idno>
      <affiliation><orgName type="institution">Tsinghua University</orgName></affiliation></author>
    <author><persName><forename type="first">Na</forename><surname>Li</surname></persName></author>
   </analytic><idno type="DOI">10.1000/abc.123</idno></biblStruct></sourceDesc>
  </fileDesc>
  <profileDesc><abstract><div><p>We propose a watermark that survives printing and camera capture on many phones.</p></div></abstract></profileDesc>
 </teiHeader>
 <text><body>
  <div><head n="1">Introduction</head>
   <p coords="1,10,10,100,10">Watermarking protects images from misuse and is widely studied in the literature of media security.</p>
   <p coords="2,10,10,100,10">Print-camera channels distort images through perspective, blur, moire and lighting changes that break codes.</p>
  </div>
  <div><head n="2">Method</head>
   <p coords="3,10,10,100,10">Our encoder embeds a message with a learned residual and a differentiable noise layer simulating the channel.</p>
   <p coords="3,10,10,100,10">The decoder recovers bits after geometric correction and we train end to end with a perceptual loss.</p>
  </div>
  <figure><head>Fig. 1</head><figDesc>Overview of the print camera watermark pipeline with encoder and decoder.</figDesc></figure>
 </body>
 <back><div type="references"><listBibl>
  <biblStruct><analytic><title level="a">HiDDeN: Hiding Data with Deep Networks</title>
   <author><persName><forename>Jiren</forename><surname>Zhu</surname></persName></author></analytic>
   <monogr><title level="m">ECCV</title><imprint><date when="2018"/></imprint></monogr></biblStruct>
  <biblStruct><analytic><title level="a">StegaStamp: Invisible Hyperlinks in Physical Photographs</title></analytic>
   <monogr><imprint><date when="2020"/></imprint></monogr><idno type="arXiv">1904.05343</idno></biblStruct>
 </listBibl></div></back>
 </text>
</TEI>"""


def _uid(db):
    return db.query(User.id).filter(User.email == "admin@example.com").scalar()


def _lab(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    h = {"Authorization": f"Bearer {r.json()['access']}"}
    return client.post("/api/v1/labs", json={"name": "bib", "code": f"bib{id(r) % 9999}", "description": ""}, headers=h).json()["id"]


def test_parse_tei_reads_people_sections_and_references():
    parsed = parse_tei(TEI)
    assert parsed["title"] == "Camera Resilient Watermarking"
    assert parsed["doi"] == "10.1000/abc.123"
    assert parsed["authors"][0]["orcid"] == "0000-0002-1825-0097"
    assert "Tsinghua" in parsed["authors"][0]["affiliation"]
    assert [s["title"] for s in parsed["sections"]] == ["Introduction", "Method"]
    assert len(parsed["references"]) == 2
    assert parsed["references"][1]["arxiv_id"] == "1904.05343"
    chunks = chunk_structured(parsed, ["p1", "p2", "p3"])
    assert chunks and chunks[0]["kind"] == "abstract"
    bodies = [c for c in chunks if c["kind"] == "body"]
    assert {c["section"] for c in bodies} == {"Introduction", "Method"}
    assert all(c["page_no"] for c in bodies)
    assert any(c["kind"] == "caption" for c in chunks)
    assert parse_tei("<not tei/>") == {}


def test_name_key_folds_order_and_accents():
    assert name_key("Zhang, Wei") == name_key("Wei Zhang")
    assert name_key("José García") == name_key("Jose Garcia")
    assert name_key("张伟") == "张伟"


def test_same_name_without_evidence_stays_two_people(client):
    lab = _lab(client)
    db = SessionLocal()
    try:
        uid = _uid(db)
        a = LiteratureItem(lab_id=lab, title="Paper A about vision", authors_json=json.dumps(["Wei Zhang", "Na Li"]), added_by=uid)
        b = LiteratureItem(lab_id=lab, title="Paper B about chemistry", authors_json=json.dumps(["Wei Zhang", "Bob Stone"]), added_by=uid)
        c = LiteratureItem(lab_id=lab, title="Paper C about vision again", authors_json=json.dumps(["Wei Zhang", "Na Li"]), added_by=uid)
        db.add_all([a, b, c])
        db.flush()
        for it in (a, b, c):
            sync_authorships(db, it)
        db.commit()
        people = find_authors(db, lab, "Wei Zhang")
        assert len(people) == 2, [p.paper_count for p in people]
        counts = sorted(p.paper_count for p in people)
        assert counts == [1, 2]
        big = max(people, key=lambda p: p.paper_count)
        small = min(people, key=lambda p: p.paper_count)
        kept = merge_authors(db, big.id, small.id, user_id=uid)
        db.commit()
        assert kept.paper_count == 3
        assert db.get(Author, small.id).merged_into == big.id
        again = split_author(db, big.id, [b.id], user_id=uid)
        db.commit()
        assert again.paper_count == 1
        assert db.query(Authorship).filter(Authorship.item_id == b.id, Authorship.position == 0).one().author_id == again.id
    finally:
        db.close()


def test_author_routes_split_merge_claim(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    h = {"Authorization": f"Bearer {r.json()['access']}"}
    lab = client.post("/api/v1/labs", json={"name": "authr", "code": "authr1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "wz@example.com", "role": "student"}, headers=h)
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "wz@example.com", "password": "student99", "display_name": "Wei Zhang", "consent": True},
    )
    hs = {"Authorization": f"Bearer {reg.json()['access']}"}
    for title, names in (("Vision one", ["Wei Zhang", "Na Li"]), ("Chem two", ["Wei Zhang", "Bob Stone"])):
        ok = client.post(f"/api/v1/labs/{lab}/literature", json={"title": title, "authors": names}, headers=h)
        assert ok.status_code == 200, ok.text
    listed = client.get(f"/api/v1/labs/{lab}/authors", params={"q": "Wei Zhang"}, headers=hs).json()
    assert len(listed["items"]) == 2 and listed["same_name"] is True
    a, b = listed["items"]
    denied = client.post(f"/api/v1/authors/{a['id']}/merge", json={"other_id": b["id"]}, headers=hs)
    assert denied.status_code == 403
    merged = client.post(f"/api/v1/authors/{a['id']}/merge", json={"other_id": b["id"]}, headers=h)
    assert merged.status_code == 200, merged.text
    assert merged.json()["paper_count"] == 2
    mine = client.get(f"/api/v1/labs/{lab}/me/author", headers=hs).json()
    assert mine["author"] is None and mine["suggestions"]
    claim = client.post(f"/api/v1/labs/{lab}/me/author", json={"author_id": a["id"]}, headers=hs)
    assert claim.status_code == 200, claim.text
    detail = client.get(f"/api/v1/authors/{a['id']}", headers=h).json()
    assert detail["member"]["name"] == "Wei Zhang"
    assert len(detail["papers"]) == 2
    item_id = detail["papers"][0]["id"]
    rel = client.get(f"/api/v1/literature/{item_id}/relations", headers=hs)
    assert rel.status_code == 200, rel.text
    assert rel.json()["authors"][0]["author_id"] == a["id"]


def test_orcid_joins_people_and_references_link_inside_lab(client):
    lab = _lab(client)
    db = SessionLocal()
    try:
        uid = _uid(db)
        x = LiteratureItem(lab_id=lab, title="Camera Resilient Watermarking", doi="10.1000/abc.123", added_by=uid)
        y = LiteratureItem(lab_id=lab, title="HiDDeN: Hiding Data with Deep Networks", added_by=uid)
        z = LiteratureItem(lab_id=lab, title="Another watermark paper by the same person", added_by=uid)
        db.add_all([x, y, z])
        db.flush()
        orcid = "0000-0002-1825-0097"
        sync_authorships(db, x, [{"name": "Wei Zhang", "orcid": orcid}])
        sync_authorships(db, z, [{"name": "W. Zhang", "orcid": orcid}])
        parsed = parse_tei(TEI)
        save_references(db, x, parsed["references"], source="grobid")
        db.commit()
        people = db.query(Author).filter(Author.lab_id == lab, Author.orcid == orcid).all()
        assert len(people) == 1 and people[0].paper_count == 2
        ref = db.query(LiteratureReference).filter(LiteratureReference.citing_item_id == x.id, LiteratureReference.cited_item_id.isnot(None)).one()
        assert ref.cited_item_id == y.id
        late = LiteratureItem(lab_id=lab, title="StegaStamp: Invisible Hyperlinks in Physical Photographs", arxiv_id="1904.05343", added_by=uid)
        db.add(late)
        db.flush()
        assert link_item_as_cited(db, late) == 1
        db.commit()
        rel = related_papers(db, x, kinds=["cites", "same_author"])
        assert {r["item_id"] for r in rel["cites"]} == {y.id, late.id}
        assert rel["same_author"][0]["item_id"] == z.id
        back = related_papers(db, y, kinds=["cited_by", "co_cited"])
        assert back["cited_by"][0]["item_id"] == x.id
        assert back["co_cited"][0]["item_id"] == late.id
    finally:
        db.close()
