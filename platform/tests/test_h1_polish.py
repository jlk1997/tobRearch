from __future__ import annotations

import os

from openlens_ai.agents.paper_reproducer.pipeline import env_check, run_until_terminal


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_env_check_awaiting_gpu_when_path_exists():
    env = env_check(dataset_path=".", gpu_mode="off", needs_gpu=True)
    assert env["status"] == "awaiting_gpu"
    assert env["error_class"] == "awaiting_gpu"


def test_pipeline_awaiting_gpu_no_fake_metrics():
    state = run_until_terminal(
        {
            "current_stage": "ingest",
            "title": "toy-gpu",
            "pdf_text": "We train the model with CUDA GPU kernels.",
            "dataset_path": ".",
            "gpu_mode": "off",
            "claimed_metrics": {"acc": 0.99},
            "method_card_json": {"title": "toy-gpu", "problem": "train", "needs_gpu": True},
        }
    )
    assert state["status"] == "awaiting_gpu"
    assert state["error_class"] == "awaiting_gpu"
    assert state.get("actual_metrics_json") in (None, {}, {})
    assert "awaiting_gpu" in (state.get("gap_report_md") or "")
    assert "不伪造" in (state.get("gap_report_md") or "")


def test_refresh_issues_new_access(client):
    pair = _admin(client)
    r = client.post("/api/v1/auth/refresh", json={"refresh": pair["refresh"]})
    assert r.status_code == 200, r.text
    assert r.json()["access"]
    me = client.get("/api/v1/auth/me", headers=_h(r.json()["access"]))
    assert me.status_code == 200
    bad = client.post("/api/v1/auth/refresh", json={"refresh": "not-a-token"})
    assert bad.status_code == 401


def test_home_guide_and_cnki_degraded(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "G", "code": "gd1", "description": ""}, headers=h).json()["id"]
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200
    keys = {x["key"] for x in home.json()["guide"]}
    assert {"invite", "lab_key", "import_lit", "mark_read", "manuscript", "cockpit"} <= keys
    assert any(x["key"] == "invite" and x["done"] is False for x in home.json()["guide"])
    cap = client.post(
        "/api/v1/literature/import-capture",
        json={
            "lab_id": lab,
            "page_url": "https://kns.cnki.net/kcms/detail/fake",
            "title": "知网测试论文",
            "site_adapter": "cnki",
            "authors": [],
        },
        headers=h,
    )
    assert cap.status_code == 200, cap.text
    assert cap.json()["degraded"] is True
    assert cap.json()["hint"]


def test_file_cross_lab_forbidden(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab_a = client.post("/api/v1/labs", json={"name": "FA", "code": "fa1", "description": ""}, headers=h).json()["id"]
    lab_b = client.post("/api/v1/labs", json={"name": "FB", "code": "fb1", "description": ""}, headers=h).json()["id"]
    up = client.post(f"/api/v1/labs/{lab_a}/files", files={"file": ("secret.txt", b"secret", "text/plain")}, headers=h)
    assert up.status_code == 200, up.text
    fid = up.json()["id"]
    inv = client.post(f"/api/v1/labs/{lab_b}/invites", json={"email": "xlab@example.com", "role": "student"}, headers=h).json()
    stu = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "xlab@example.com", "password": "student99", "consent": True, "display_name": "X"},
    )
    assert stu.status_code == 200, stu.text
    denied = client.get(f"/api/v1/files/{fid}", headers=_h(stu.json()["access"]))
    assert denied.status_code == 403
    ok = client.get(f"/api/v1/files/{fid}", headers=h)
    assert ok.status_code == 200


def test_reproduce_api_awaiting_gpu(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "RG", "code": "rgpu", "description": ""}, headers=h).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "GPU Paper", "abstract": "Training uses CUDA GPU.", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ds = os.environ.get("FILE_ROOT") or "."
    os.makedirs(ds, exist_ok=True)
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": lit.json()["id"], "dataset_path": ds, "claimed_metrics": {"acc": 0.9}},
        headers=h,
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "awaiting_gpu"
    assert run.json()["error_class"] == "awaiting_gpu"
    assert run.json()["actual_metrics"] in ({}, None)
    assert "awaiting_gpu" in (run.json().get("gap_report_md") or "")
    detail = client.get(f"/api/v1/reproduce-runs/{run.json()['id']}", headers=h)
    assert detail.status_code == 200
    paths = [x.get("path") for x in detail.json().get("artifacts") or []]
    assert any("gap_report.md" in str(p) for p in paths)


def test_minutes_cartesian_assign(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "MT", "code": "mt2", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "asg@example.com", "role": "student"}, headers=h).json()
    stu = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "asg@example.com", "password": "student99", "consent": True, "display_name": "S"},
    ).json()
    admin_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    stu_id = client.get("/api/v1/auth/me", headers=_h(stu["access"])).json()["id"]
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": "行动: 写摘要"}, headers=h).json()
    out = client.post(
        f"/api/v1/meetings/{m['id']}/minutes",
        json={"notes_raw": "行动: 写摘要", "assign_to": [admin_id, stu_id]},
        headers=h,
    )
    assert out.status_code == 200, out.text
    actions = out.json()["actions"]
    assert len(actions) == 2
    assignees = {a["assignee_id"] for a in actions}
    assert assignees == {admin_id, stu_id}


def test_version_restore_and_outline_writeback(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "MS", "code": "msv1", "description": ""}, headers=h).json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Draft", "kind": "conference", "content_md": "v1-body"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    patched = client.patch(f"/api/v1/manuscripts/{mid}", json={"content_md": "v2-body"}, headers=h)
    assert patched.status_code == 200, patched.text
    vers = client.get(f"/api/v1/manuscripts/{mid}/versions", headers=h)
    assert vers.status_code == 200
    first = min(vers.json(), key=lambda x: x["version"])
    restored = client.post(f"/api/v1/manuscripts/{mid}/versions/{first['id']}/restore", headers=h)
    assert restored.status_code == 200, restored.text
    assert "v1-body" in restored.json()["content_md"]
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "manuscript_id": mid},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    assert "相关工作" in (ol.json().get("markdown") or "")
    got = client.get(f"/api/v1/manuscripts/{mid}", headers=h)
    assert "相关工作" in got.json()["content_md"]


def test_list_invites_and_revoke(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "IV", "code": "invx", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "rev@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200
    listed = client.get(f"/api/v1/labs/{lab}/invites", headers=h)
    assert listed.status_code == 200
    row = next(x for x in listed.json() if x["email"] == "rev@example.com")
    assert "/invite/" in row["invite_url"]
    gone = client.delete(f"/api/v1/invites/{row['id']}", headers=h)
    assert gone.status_code == 200
    listed2 = client.get(f"/api/v1/labs/{lab}/invites", headers=h)
    assert all(x["id"] != row["id"] for x in listed2.json())


def test_events_stream_and_member_role(client):
    token = _admin(client)["access"]
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "EV", "code": "ev1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "role@example.com", "role": "student"}, headers=h).json()
    stu = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "role@example.com", "password": "student99", "consent": True, "display_name": "R"},
    ).json()
    sid = client.get("/api/v1/auth/me", headers=_h(stu["access"])).json()["id"]
    patched = client.patch(f"/api/v1/labs/{lab}/members/{sid}", json={"role": "teacher"}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["role"] == "teacher"
    ev = client.get(f"/api/v1/labs/{lab}/events/stream", headers=h)
    assert ev.status_code == 200
    assert "snapshot" in ev.text
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200
    assert any(x.get("display_name") for x in st.json()["by_member"])
