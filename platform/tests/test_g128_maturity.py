from __future__ import annotations

from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.insight import reproduce_list_empty_next, research_list_empty_next
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


def _rows(payload):
    if isinstance(payload, list):
        return payload
    return list(payload.get("items") or [])


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
        row.error = "综述没写到"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()


def test_g128_reproduce_scope_empty_stays_honest():
    lab = "lab-g128"
    scoped = reproduce_list_empty_next(lab, empty_kind="filter", literature_item_id="lit-1", status="failed")
    assert scoped["title"] == "该文献没有失败的复现"
    ds = reproduce_list_empty_next(lab, empty_kind="filter", dataset_id="ds-1", status="awaiting_gpu")
    assert ds["title"] == "该数据集没有等待实验算力的复现"
    queued = reproduce_list_empty_next(lab, empty_kind="filter", status="queued")
    assert queued["title"] == "没有已入队的复现"
    only_ds = reproduce_list_empty_next(lab, empty_kind="filter", dataset_id="ds-1")
    assert only_ds["title"] == "该数据集没有复现任务"


def test_g129_unused_ignores_status_filter(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g129mux")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "keep unused", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    dest = tmp_path / "job"
    _plant(dest, "Mux Unused Paper")
    _fail_run(rid, dest)
    mixed = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "status": "pending"},
        headers=h,
    )
    assert mixed.status_code == 200, mixed.text
    mixed_body = mixed.json()
    assert _rows(mixed_body) == []
    assert mixed_body.get("empty_kind") == "filter"
    assert "没有综述没写到的文献调研" in ((mixed_body.get("empty_next") or {}).get("title") or "")
    assert "丢掉状态" in ((mixed_body.get("empty_next") or {}).get("text") or "")
    queued = research_list_empty_next(lab, empty_kind="filter", status="queued")
    assert queued["title"] == "没有已入队的文献调研"
    blocked = research_list_empty_next(lab, empty_kind="filter", status="blocked")
    assert blocked["title"] == "没有缺数据的文献调研"
    ds = research_list_empty_next(lab, empty_kind="filter", status="failed", dataset_id="ds-1")
    assert ds["title"] == "该数据集没有失败的文献调研"


def test_g130_dataset_library_empty_is_named(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g130ds")
    empty = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert not _rows(body)
    assert body.get("empty_kind") == "library"
    assert (body.get("empty_next") or {}).get("title") == "还没有数据集"
    created = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "一组数据"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    listed = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    assert listed.json()[0]["id"] == created.json()["id"]
