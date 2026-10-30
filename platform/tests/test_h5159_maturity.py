from __future__ import annotations

import io
import json
import zipfile

from fengsuan.app.services.insight import digest_markdown
from fengsuan.app.services.research_engine import dishonest_reproduce_claims


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


def test_h5159_sidecar_includes_mentioned_uncited(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5159zp")
    title = "Zip Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "导出稿J", "kind": "conference", "content_md": f"结论：我们成功完成实验。参见 {title}。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    z = client.post(f"/api/v1/manuscripts/{mid}/export-zip", headers=h)
    assert z.status_code == 200, z.text
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = set(zf.namelist())
        assert "main.tex" in names
        assert "reproduce_gaps.md" in names
        tex = zf.read("main.tex").decode("utf-8")
        gaps = zf.read("reproduce_gaps.md").decode("utf-8")
        assert title in gaps
        assert "组内复现差距" in gaps
        assert "组内复现差距" not in tex


def test_h5160_language_names_title_without_manuscript(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5160lg")
    title = "Lang Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    pol = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": f"我们成功完成实验，复现了 {title}。"},
        headers=h,
    )
    assert pol.status_code == 200, pol.text
    warn = pol.json().get("ungrounded_reproduce") or ""
    assert "成功完成实验" in warn
    assert title in warn


def test_h5161_claims_lists_mentioned_only(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5161cl")
    title = "Claim Mention Unmeasured Paper"
    iid, _rid = _lit_and_run(client, h, lab, title, tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "主张稿J", "kind": "other", "content_md": f"结论：我们成功完成实验。参见 {title}。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    claims = client.get(f"/api/v1/manuscripts/{mid}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    body = claims.json()
    assert "成功完成实验" in (body.get("ungrounded_reproduce") or "")
    assert title in (body.get("ungrounded_reproduce") or "")
    assert body.get("mentioned_only_count", 0) >= 1
    extra = next(x for x in body.get("items") or [] if x.get("literature_item_id") == iid)
    assert extra.get("mentioned_only") is True
    assert extra.get("cited_in_body") is True
    assert body.get("citation_count") == 0


def test_h5162_stats_and_cockpit_mark_ungrounded(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5162st")
    title = "Stats Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "统计稿J", "kind": "conference", "content_md": f"结论：我们成功完成实验。参见 {title}。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    assert st.json().get("manuscripts_ungrounded", 0) >= 1
    assert "ungrounded=1" in (st.json().get("manuscripts_ungrounded_href") or "")
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "manuscripts_ungrounded" in csv.text
    me = client.get("/api/v1/auth/me", headers=h).json()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == me["id"])
    hit = next(x for x in (card.get("manuscripts") or []) if x.get("id") == mid)
    assert hit.get("ungrounded_reproduce")
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h)
    assert member.status_code == 200, member.text
    row = next(x for x in (member.json().get("manuscripts") or []) if x.get("id") == mid)
    assert row.get("ungrounded_reproduce")


def test_h5163_digest_stamps_manuscript_warning(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5163dg")
    title = "Digest Mention Unmeasured Paper"
    _lit_and_run(client, h, lab, title, tmp_path)
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "周报稿J", "kind": "other", "content_md": f"结论：我们成功完成实验。参见 {title}。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    payload = digest.json()
    assert payload.get("manuscripts_ungrounded", 0) >= 1
    assert "ungrounded=1" in (payload.get("manuscripts_ungrounded_href") or "")
    events = [x for x in (payload.get("items") or []) if x.get("object_id") == mid]
    assert events
    assert any(x.get("ungrounded_reproduce") for x in events)
    md = digest_markdown(payload)
    assert "还没复现就写成成功" in md


def test_h5164_gate_reads_lab_context_titles(tmp_path):
    title = "Context Only Unmeasured Paper"
    ctx = {"literature": [{"id": "z", "title": title, "reproduce": {"measured": False}}]}
    (tmp_path / "lab_context.json").write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "lab_context.json").write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
    (ws / "literature_review.md").write_text("综述。我们成功完成实验。", encoding="utf-8")
    (tmp_path / "plan.md").write_text("计划", encoding="utf-8")
    measured = {"literature": [{"id": "z", "title": title, "reproduce": {"measured": True}}]}
    msg = dishonest_reproduce_claims(tmp_path, measured)
    assert "成功完成实验" in msg
    assert title in msg
    assert "成功完成实验" in dishonest_reproduce_claims(tmp_path, {})
    only = tmp_path / "sidecar_only"
    only.mkdir()
    (only / "workspace").mkdir()
    (only / "lab_context.json").write_text(
        json.dumps({"literature": ctx["literature"], "note": "我们成功完成实验。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert dishonest_reproduce_claims(only, {}) == ""
