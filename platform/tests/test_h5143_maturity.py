from __future__ import annotations

import io
import zipfile

from fengsuan.app.services.research_engine import dishonest_reproduce_claims
from fengsuan.app.services.reproduce import text_claims_reproduce_success


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
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


def _lit_and_run(client, h, lab: str, title: str, tmp_path, claimed=None):
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": title, "abstract": "cpu"}, headers=h)
    assert lit.status_code == 200, lit.text
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": f"{title}Set"}, headers=h)
    assert ds.status_code == 200, ds.text
    body = {"literature_item_id": lit.json()["id"], "dataset_id": ds.json()["id"], "dataset_path": str(tmp_path)}
    if claimed is not None:
        body["claimed_metrics"] = claimed
    run = client.post(f"/api/v1/labs/{lab}/reproduce-runs", json=body, headers=h)
    assert run.status_code == 200, run.text
    return lit.json()["id"], run.json()["id"]


def test_h5143_manuscript_warns_but_saves(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5143ms")
    iid, _rid = _lit_and_run(client, h, lab, "Writeback Spine Paper", tmp_path, {"acc": 0.9})
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "写回稿H", "kind": "conference", "content_md": "引言"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    cite = client.post(
        f"/api/v1/manuscripts/{mid}/citations",
        json={"literature_item_id": iid, "citation_key": "hkey"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    stamp = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["updated_at"]
    patched = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"content_md": "结论：我们成功完成实验，见 [@hkey]。", "expected_updated_at": stamp},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    warn = patched.json().get("ungrounded_reproduce") or ""
    assert "成功完成实验" in warn
    assert "尚无实测" in warn
    got = client.get(f"/api/v1/manuscripts/{mid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json().get("content_md", "").startswith("结论")
    assert "成功完成实验" in (got.json().get("ungrounded_reproduce") or "")


def test_h5144_notifications_and_home_stamp_evidence(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5144nt")
    sh = _invite_student(client, h, lab, "h5144stu@example.com")
    iid, rid = _lit_and_run(client, sh, lab, "Notify Spine Paper", tmp_path, {"acc": 0.8})
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    lit_notes = [x for x in notes.json() if x.get("object_type") == "literature" and x.get("object_id") == iid]
    assert lit_notes
    ev = lit_notes[0].get("latest_reproduce") or {}
    assert ev.get("id") == rid
    assert ev.get("measured") is False
    assert ev.get("honest") == "not_run"
    repro_notes = [x for x in notes.json() if x.get("object_type") in {"reproduce", "reproduce_run"} and x.get("object_id") == rid]
    if repro_notes:
        assert (repro_notes[0].get("latest_reproduce") or {}).get("honest") == "not_run"
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    unread = [x for x in (home.json().get("unread_items") or []) if x.get("object_id") == iid]
    if unread:
        assert (unread[0].get("latest_reproduce") or {}).get("id") == rid


def test_h5145_literature_reproduce_filter(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5145lt")
    iid, _rid = _lit_and_run(client, h, lab, "Filter Spine Paper", tmp_path)
    bare = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Bare Paper", "abstract": "cpu"}, headers=h)
    assert bare.status_code == 200, bare.text
    not_run = client.get(f"/api/v1/labs/{lab}/literature", params={"reproduce": "not_run"}, headers=h)
    assert not_run.status_code == 200, not_run.text
    rows = not_run.json() if isinstance(not_run.json(), list) else not_run.json().get("items") or []
    ids = {x.get("id") for x in rows}
    assert iid in ids
    assert bare.json()["id"] not in ids
    none = client.get(f"/api/v1/labs/{lab}/literature", params={"reproduce": "none"}, headers=h)
    assert none.status_code == 200, none.text
    none_rows = none.json() if isinstance(none.json(), list) else none.json().get("items") or []
    none_ids = {x.get("id") for x in none_rows}
    assert bare.json()["id"] in none_ids
    assert iid not in none_ids


def test_h5146_reproduce_honest_filter(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5146hn")
    iid, rid = _lit_and_run(client, h, lab, "Honest Filter Paper", tmp_path, {"acc": 0.5})
    rows = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"honest": "not_run"}, headers=h)
    assert rows.status_code == 200, rows.text
    hit = next(x for x in rows.json() if x.get("id") == rid)
    assert hit.get("honest") == "not_run"
    assert hit.get("measured") is False
    assert hit.get("literature_item_id") == iid
    measured = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"honest": "measured"}, headers=h)
    assert measured.status_code == 200, measured.text
    assert all(x.get("id") != rid for x in measured.json())


def test_h5147_overleaf_zip_has_gaps_sidecar(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5147zp")
    iid, _rid = _lit_and_run(client, h, lab, "Zip Spine Paper", tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "导出稿H", "kind": "conference", "content_md": "正文"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    client.post(f"/api/v1/manuscripts/{mid}/citations", json={"literature_item_id": iid, "citation_key": "zipk"}, headers=h)
    z = client.post(f"/api/v1/manuscripts/{mid}/export-zip", headers=h)
    assert z.status_code == 200, z.text
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = set(zf.namelist())
        assert "main.tex" in names
        assert "refs.bib" in names
        assert "reproduce_gaps.md" in names
        tex = zf.read("main.tex").decode("utf-8")
        gaps = zf.read("reproduce_gaps.md").decode("utf-8")
        assert "\\section" in tex or "正文" in tex
        assert "组内复现差距" in gaps
        assert "尚无实测" in gaps
        assert "组内复现差距" not in tex


def test_h5148_language_flags_success_boilerplate(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5148lg")
    iid, _rid = _lit_and_run(client, h, lab, "Lang Spine Paper", tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "语言稿H", "kind": "other", "content_md": "草稿"},
        headers=h,
    )
    mid = ms.json()["id"]
    client.post(f"/api/v1/manuscripts/{mid}/citations", json={"literature_item_id": iid, "citation_key": "lk"}, headers=h)
    pol = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "我们成功完成实验，复现了 Lang Spine Paper。"},
        headers=h,
    )
    assert pol.status_code == 200, pol.text
    assert "成功完成实验" in (pol.json().get("ungrounded_reproduce") or "")
    tr = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "we successfully reproduced the claimed result", "target": "zh", "manuscript_id": mid},
        headers=h,
    )
    assert tr.status_code == 200, tr.text
    assert tr.json().get("ungrounded_reproduce")
    wb = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={"manuscript_id": mid, "text": "结论：成功完成实验。", "write_mode": "append"},
        headers=h,
    )
    assert wb.status_code == 200, wb.text
    assert "成功完成实验" in (wb.json().get("ungrounded_reproduce") or "")


def test_h5140_gate_still_honest(tmp_path):
    inj = {"literature": [{"id": "x", "title": "Keep Honest Paper", "reproduce": {"measured": False}}]}
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "literature_review.md").write_text("综述 Keep Honest Paper。我们成功完成实验。", encoding="utf-8")
    (tmp_path / "plan.md").write_text("计划引用 Keep Honest Paper", encoding="utf-8")
    assert text_claims_reproduce_success("我们成功完成实验") == "成功完成实验"
    assert "成功完成实验" in dishonest_reproduce_claims(tmp_path, inj)
