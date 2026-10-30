from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.kb import query_kb


GOLD = [
    ("attention mechanism transformer", "attention"),
    ("bidirectional encoder representation", "bidirectional"),
]


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_g146_recall_at_8(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "g146rag", "code": "g146rag", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    text = (
        "Transformer attention is all you need. Self-attention computes pairwise scores. "
        "BERT uses bidirectional encoder representations from transformers."
    )
    up = client.post(
        f"/api/v1/labs/{lab_id}/kb/documents",
        files={"file": ("gold.md", text.encode("utf-8"), "text/markdown")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    db = SessionLocal()
    try:
        hits_ok = 0
        rr = 0.0
        for q, needle in GOLD:
            hits, _ = query_kb(db, lab_id, q, top_k=8)
            texts = [(h.get("content") or "").lower() for h in hits]
            rank = next((i for i, t in enumerate(texts) if needle in t), None)
            if rank is not None:
                hits_ok += 1
                rr += 1.0 / (rank + 1)
        recall = hits_ok / len(GOLD)
        mrr = rr / len(GOLD)
        assert recall >= 0.5
        assert mrr >= 0.4
    finally:
        db.close()
    again = client.post(f"/api/v1/kb/documents/{up.json()['id']}/reindex", headers=h)
    assert again.status_code == 200, again.text
    assert again.json().get("status") in {"indexed", "pending"}
