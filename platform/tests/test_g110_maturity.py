from __future__ import annotations

from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.insight import digest_markdown
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


def _direction(client, h, lab: str, name: str):
    r = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": name, "keywords": [name.lower()], "description": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_g110_direction_unused_list_is_not_lab_wide(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g110dir")
    a = _invite_student(client, h, lab, "g110a@example.com")
    b = _invite_student(client, h, lab, "g110b@example.com")
    aid = _direction(client, h, lab, "DirA")
    bid = _direction(client, h, lab, "DirB")
    cid = _direction(client, h, lab, "DirC")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": aid}, headers=a).status_code == 200
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": bid}, headers=b).status_code == 200
    rid_a = _unused_job(client, a, lab, tmp_path / "a", "Alpha Unseen Paper", "student A unused")
    rid_b = _unused_job(client, b, lab, tmp_path / "b", "Beta Unseen Paper", "student B unused")

    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    rows = {x["id"]: x for x in listed.json()}
    assert rows[aid].get("unused_research") == 1
    assert rows[bid].get("unused_research") == 1
    assert rows[cid].get("unused_research") == 0
    assert f"direction_id={aid}" in (rows[aid].get("unused_research_href") or "")
    assert rows[aid].get("stuck_count") == (rows[aid].get("unread_count") or 0) + (
        rows[aid].get("no_card_count") or 0
    ) + (rows[aid].get("related_work_missing") or 0) + (rows[aid].get("reproduce_not_run") or 0) + (
        rows[aid].get("manuscripts_ungrounded") or 0
    )

    scoped = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "direction_id": aid}, headers=h)
    assert scoped.status_code == 200, scoped.text
    ids = {row["id"] for row in _rows(scoped.json())}
    assert ids == {rid_a}

    other = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "direction_id": bid}, headers=h)
    assert {row["id"] for row in _rows(other.json())} == {rid_b}

    empty = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "direction_id": cid}, headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert not _rows(body)
    assert body.get("empty_kind") == "filter"
    assert (body.get("empty_next") or {}).get("title") == "该方向没有综述没写到的文献调研"

    whole = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert {row["id"] for row in _rows(whole.json())} == {rid_a, rid_b}


def test_g111_student_digest_does_not_see_others_unused(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g111dig")
    a = _invite_student(client, h, lab, "g111a@example.com")
    b = _invite_student(client, h, lab, "g111b@example.com")
    _unused_job(client, a, lab, tmp_path / "a", "Alpha Unseen Paper", "digest A")
    _unused_job(client, b, lab, tmp_path / "b", "Beta Unseen Paper", "digest B")

    sa = client.get(f"/api/v1/labs/{lab}/digest", params={"days": 7}, headers=a)
    assert sa.status_code == 200, sa.text
    pa = sa.json()
    assert pa.get("scope") == "self"
    assert pa.get("unused_research") == 1
    assert "Alpha Unseen Paper" in (pa.get("unused_titles") or [])
    assert "Beta Unseen Paper" not in (pa.get("unused_titles") or [])
    md_a = digest_markdown(pa)
    assert "Alpha Unseen Paper" in md_a
    assert "Beta Unseen Paper" not in md_a

    sb = client.get(f"/api/v1/labs/{lab}/digest", params={"days": 7}, headers=b)
    assert sb.status_code == 200, sb.text
    assert sb.json().get("unused_research") == 1
    assert "Beta Unseen Paper" in (sb.json().get("unused_titles") or [])

    teach = client.get(f"/api/v1/labs/{lab}/digest", params={"days": 7}, headers=h)
    assert teach.status_code == 200, teach.text
    pt = teach.json()
    assert pt.get("scope") == "lab"
    assert pt.get("unused_research") == 2
    md_t = digest_markdown(pt)
    assert "综述没写到 2 次" in md_t


def test_g112_graph_manuscript_unused_and_compare_leftover(client, tmp_path, monkeypatch):
    monkeypatch.setattr("fengsuan.app.services.analysis.chat", lambda *a, **k: "{}")
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g112gph")
    stu = _invite_student(client, h, lab, "g112s@example.com")
    rid = _unused_job(client, stu, lab, tmp_path / "job", "Alpha Unseen Paper", "graph unused")

    unnamed = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "未点名稿", "kind": "other", "content_md": "## 相关工作\n还没点名。"},
        headers=stu,
    )
    assert unnamed.status_code == 200, unnamed.text
    named = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "已点名稿", "kind": "other", "content_md": "## 相关工作\n点名 Alpha Unseen Paper。"},
        headers=stu,
    )
    assert named.status_code == 200, named.text
    uid, nid = unnamed.json()["id"], named.json()["id"]

    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=stu)
    assert graph.status_code == 200, graph.text
    nodes = {n["id"]: n for n in graph.json().get("nodes") or []}
    assert "Alpha Unseen Paper" in ((nodes[uid].get("unused_research") or {}).get("text") or "")
    assert (nodes[uid].get("unused_research") or {}).get("href") == f"/runs/{rid}"
    assert not nodes[nid].get("unused_research")

    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=stu)
    assert listed.status_code == 200, listed.text
    ms_rows = {x["id"]: x for x in _rows(listed.json())}
    assert ms_rows[uid].get("unused_research")
    assert not ms_rows[nid].get("unused_research")

    p1 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G112 Compare Alpha TitleX", "abstract": "a"},
        headers=stu,
    ).json()
    p2 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G112 Compare Beta TitleX", "abstract": "b"},
        headers=stu,
    ).json()
    cmp = client.post(
        f"/api/v1/labs/{lab}/literature/compare",
        json={"item_ids": [p1["id"], p2["id"]]},
        headers=stu,
    )
    assert cmp.status_code == 200, cmp.text
    assert not cmp.json().get("unused_research")
