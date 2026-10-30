from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.kb import KbChunk
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.services.kb import query_kb
from fengsuan.app.services.literature import search_local
from fengsuan.app.services.search_index import cap_per_doc, rrf_fuse, search_terms


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access']}"}


def _lab(client, h, code):
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, h, lab, name, text):
    r = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": (name, text.encode("utf-8"), "text/markdown")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_search_terms_drop_question_words():
    terms = search_terms("组里有哪些关于图像水印鲁棒性的论文？ robust watermarking")
    assert "robust" in terms and "watermarking" in terms
    assert all("哪些" not in t and "论文" not in t for t in terms)
    assert any("水印" in t for t in terms)


def test_rrf_and_per_doc_cap():
    vec = [("a", "d1", 1, 0.9), ("b", "d1", 2, 0.8), ("c", "d2", 1, 0.7)]
    kw = [("c", "d2", 1, 1.0), ("a", "d1", 1, 0.5)]
    fused = rrf_fuse([(1.0, vec), (1.0, kw)])
    assert {row[1] for row in fused[:2]} == {"a", "c"}
    many = [(1.0 - i * 0.01, f"x{i}", "same", i) for i in range(6)]
    assert len(cap_per_doc(many)) == 3


def test_chunks_carry_lab_and_hybrid_finds_chinese(client):
    h = _admin(client)
    lab = _lab(client, h, "scalekb1")
    other = _lab(client, h, "scalekb2")
    _upload(client, h, lab, "wm.md", "本文提出一种抗打印拍摄的图像水印方法，鲁棒性在多种相机上验证。")
    _upload(client, h, lab, "seg.md", "We study semantic segmentation with transformers on street scenes.")
    _upload(client, h, other, "wm2.md", "另一个组的图像水印论文，不该被看到。")
    db = SessionLocal()
    try:
        rows = db.query(KbChunk).filter(KbChunk.lab_id == lab).all()
        assert rows and all(r.kind in {"page", "abstract"} and r.token_count > 0 for r in rows)
        assert all(r.embed_model for r in rows)
        hits, _ = query_kb(db, lab, "图像水印的鲁棒性怎么样", top_k=4)
        assert hits and "水印" in hits[0]["content"]
        assert all("另一个组" not in (x["content"] or "") for x in hits)
        seg, _ = query_kb(db, lab, "semantic segmentation", top_k=4)
        assert seg and "segmentation" in seg[0]["content"]
    finally:
        db.close()


def test_search_local_matches_tokens_and_authors(client):
    h = _admin(client)
    lab = _lab(client, h, "scalelit")
    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User

        uid = db.query(User.id).filter(User.email == "admin@example.com").scalar()
        db.add(
            LiteratureItem(
                lab_id=lab,
                title="Camera Resilient Watermarking for Printed Images",
                abstract="A print-camera robust watermark.",
                authors_json=json.dumps(["Wei Zhang", "Li Na"]),
                added_by=uid,
            )
        )
        db.add(
            LiteratureItem(
                lab_id=lab,
                title="Graph Neural Networks Survey",
                abstract="Survey of message passing.",
                authors_json=json.dumps(["Other Person"]),
                added_by=uid,
            )
        )
        db.commit()
        by_words = search_local(db, lab, "watermarking printed camera")
        assert [x["title"] for x in by_words][:1] == ["Camera Resilient Watermarking for Printed Images"]
        by_author = search_local(db, lab, "Wei Zhang")
        assert any("Watermarking" in x["title"] for x in by_author)
        assert all("Graph Neural" not in x["title"] for x in by_author)
    finally:
        db.close()
