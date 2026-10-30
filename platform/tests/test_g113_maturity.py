from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fengsuan.app.api.v1.literature import _attach_pdf, _create_item_locked
from fengsuan.app.core.concurrency import lab_quota_guard, row_write_guard
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.manuscript import Manuscript
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.insight import digest_markdown
from fengsuan.app.services.literature import collapse_same_hash_items, find_by_content_hash
from fengsuan.app.services.research_engine import persist_unused_diagnosis


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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _pdf(title_line: str = "Concurrent Hash Paper TitleX") -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\n" + title_line.encode("utf-8") + b"\ntrailer<<>>\n"


def _plant(dest: Path, title: str):
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        f"@article{{z,\n  title={{{title}}},\n  author={{A}},\n  year={{2021}},\n  journal={{J}}\n}}\n",
        encoding="utf-8",
    )


def _fail_run(rid: str, dest: Path):
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        dest.mkdir(parents=True, exist_ok=True)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述未点名"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()


def _unused_job(client, h, lab: str, dest: Path, title: str, question: str):
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(dest, title)
    _fail_run(rid, dest)
    return rid


def _ingest_same_pdf(lab: str, user_id: str, payload: bytes) -> None:
    db = SessionLocal()
    try:
        with lab_quota_guard(db, lab):
            digest = hashlib.sha256(payload).hexdigest()
            existing = find_by_content_hash(db, lab, digest)
            if existing is None:
                item, _already = _create_item_locked(
                    db, lab, user_id, {"title": "Uploaded PDF", "authors": []}, "upload", False
                )
                _attach_pdf(db, item, lab, payload, "Concurrent_Hash_Paper.pdf", "application/pdf", sha256=digest)
            collapse_same_hash_items(db, lab)
            db.commit()
    finally:
        db.close()


def test_g113_parallel_same_hash_stays_one_item(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g113hash")
    payload = _pdf()
    user_id = client.get("/api/v1/auth/me", headers=h).json()["id"]

    def go():
        _ingest_same_pdf(lab, user_id, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(go), pool.submit(go)
        first.result()
        second.result()
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    items = rows.get("items") if isinstance(rows, dict) else rows
    live = [it for it in items if not it.get("deleted_at")]
    assert len(live) == 1


def test_g114_persist_unused_keeps_progress_and_writeback_locks(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g114lock")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "persist merge", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    dest = tmp_path / "job"
    _plant(dest, "Persist Keep Paper")
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述未点名"
        row.progress_json = json.dumps(
            {"subgraphs": ["literature_reviewer"], "steps_done": 2, "diagnosis": {"kind": "parse"}},
            ensure_ascii=False,
        )
        persist_unused_diagnosis(row, db)
        db.commit()
        fresh = db.get(ResearchRun, rid)
        blob = json.loads(fresh.progress_json or "{}")
        assert blob.get("subgraphs") == ["literature_reviewer"]
        assert blob.get("steps_done") == 2
        assert "unused_bib_titles" in (blob.get("diagnosis") or {})
        assert "Persist Keep Paper" in (blob.get("diagnosis") or {}).get("unused_bib_titles", [])
    finally:
        db.close()

    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "锁稿", "kind": "other", "content_md": "## 相关工作\n初稿。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    first = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={"manuscript_id": mid, "text": "第一段。", "write_mode": "append", "heading": "相关工作"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={"manuscript_id": mid, "text": "第二段。", "write_mode": "append", "heading": "相关工作"},
        headers=h,
    )
    assert second.status_code == 200, second.text
    body = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    assert "第一段" in body and "第二段" in body

    stamp = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["updated_at"]
    ok = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"content_md": "## 相关工作\n已保存。", "expected_updated_at": stamp},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    stale = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"content_md": "覆盖失败", "expected_updated_at": stamp},
        headers=h,
    )
    assert stale.status_code == 409


def test_g114_row_lock_holds_until_commit(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g114hold")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "并发稿", "kind": "other", "content_md": "BASE"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    errors: list[BaseException] = []

    def append(tag: str) -> None:
        db = SessionLocal()
        try:
            with row_write_guard(db, Manuscript, mid) as row:
                assert row is not None
                row.content_md = (row.content_md or "") + tag
            time.sleep(0.2)
            db.commit()
        except BaseException as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    t1 = threading.Thread(target=append, args=("ONE",))
    t2 = threading.Thread(target=append, args=("TWO",))
    t1.start()
    t2.start()
    t1.join(timeout=8)
    t2.join(timeout=8)
    assert not t1.is_alive() and not t2.is_alive()
    assert not errors, errors
    body = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    assert "ONE" in body and "TWO" in body


def test_g115_teacher_digest_lists_all_unused_titles(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g115dig")
    a = _invite_student(client, h, lab, "g115a@example.com")
    b = _invite_student(client, h, lab, "g115b@example.com")
    _unused_job(client, a, lab, tmp_path / "a", "Alpha Unseen Paper", "digest A")
    _unused_job(client, b, lab, tmp_path / "b", "Beta Unseen Paper", "digest B")

    teach = client.get(f"/api/v1/labs/{lab}/digest", params={"days": 7}, headers=h)
    assert teach.status_code == 200, teach.text
    titles = teach.json().get("unused_titles") or []
    assert "Alpha Unseen Paper" in titles
    assert "Beta Unseen Paper" in titles
    assert teach.json().get("unused_research") == 2
    md = digest_markdown(teach.json())
    assert "Alpha Unseen Paper" in md
    assert "Beta Unseen Paper" in md

    sa = client.get(f"/api/v1/labs/{lab}/digest", params={"days": 7}, headers=a)
    assert sa.status_code == 200, sa.text
    assert "Alpha Unseen Paper" in (sa.json().get("unused_titles") or [])
    assert "Beta Unseen Paper" not in (sa.json().get("unused_titles") or [])
