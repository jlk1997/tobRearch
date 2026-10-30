from __future__ import annotations

from fengsuan.app.services.literature import collapse_same_hash_items
from fengsuan.app.services.research_engine import engine_runtime_missing


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


def _pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\nSame Hash Body\ntrailer<<>>\n"


def test_g63_research_preflight_exposes_engine(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g63eng")
    pf = client.get(f"/api/v1/labs/{lab}/research-preflight", headers=h)
    assert pf.status_code == 200, pf.text
    pack = pf.json()
    assert "engine_ok" in pack
    assert pack.get("engine") in {"ready", "missing"}
    assert pack.get("kind") == "research"
    keys = {m.get("key") for m in pack.get("missing") or []}
    if pack.get("engine_ok"):
        assert "engine" not in keys
        assert not engine_runtime_missing()
    else:
        assert "engine" in keys
    if pack.get("gpu_mode") == "off":
        assert pack.get("suggest_interrupt_after") == "literature_reviewer"


def test_g64_create_run_returns_preflight_suggestion(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g64run")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G64 literature only", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    pf = created.json().get("preflight") or {}
    assert "engine_ok" in pf
    if pf.get("gpu_mode") == "off":
        assert pf.get("suggest_interrupt_after") == "literature_reviewer"


def test_g65_collapse_same_hash_keeps_oldest(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g65hash")
    payload = _pdf()
    first = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("Same_Hash_Paper.pdf", payload, "application/pdf"))],
        headers=h,
    )
    assert first.status_code == 200, first.text
    keeper_id = first.json()["items"][0]["id"]
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem

    db = SessionLocal()
    try:
        keeper = db.get(LiteratureItem, keeper_id)
        orig = db.query(LiteratureFile).filter(LiteratureFile.item_id == keeper_id).first()
        assert orig is not None and orig.content_sha256
        extra = LiteratureItem(
            lab_id=lab,
            title="10382 IEEE TRANSACTIONS DUP",
            added_by=keeper.added_by,
            source_channel="upload",
        )
        db.add(extra)
        db.flush()
        db.add(
            LiteratureFile(
                item_id=extra.id,
                file_path=orig.file_path,
                filename=orig.filename,
                mime="application/pdf",
                size=orig.size,
                content_sha256=orig.content_sha256,
            )
        )
        db.commit()
        extra_id = extra.id
    finally:
        db.close()
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    rows = listed.get("items") if isinstance(listed, dict) else listed
    assert len(rows) == 2
    db = SessionLocal()
    try:
        n = collapse_same_hash_items(db, lab)
        db.commit()
        assert n == 1
        gone = db.get(LiteratureItem, extra_id)
        assert gone is not None and gone.deleted_at is not None
        stay = db.get(LiteratureItem, keeper_id)
        assert stay is not None and stay.deleted_at is None
    finally:
        db.close()
    again = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    left = again.get("items") if isinstance(again, dict) else again
    assert len(left) == 1
    assert left[0]["id"] == keeper_id
