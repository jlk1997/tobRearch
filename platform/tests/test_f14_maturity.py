from __future__ import annotations

from unittest.mock import patch

from fengsuan.app.services.insight import build_member_stuck


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
            "display_name": "生十四",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f14_hub_counts_beyond_preview(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f14hub")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "全量卡住", "keywords": ["full"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    old = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F14最旧未写", "kind": "other", "content_md": "## 引言\n草稿。", "direction_ids": [did]},
        headers=h,
    )
    assert old.status_code == 200, old.text
    for i in range(20):
        ok = client.post(
            f"/api/v1/labs/{lab}/manuscripts",
            json={
                "title": f"F14预览{i}",
                "kind": "other",
                "content_md": "## 相关工作\n已写。",
                "direction_ids": [did],
            },
            headers=h,
        )
        assert ok.status_code == 200, ok.text
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    assert len(ov.json().get("manuscripts") or []) == 20
    rw = next(x for x in (ov.json().get("stuck") or {}).get("lines") or [] if x.get("key") == "related_work")
    assert rw.get("count") == 1
    listed = client.get(
        f"/api/v1/labs/{lab}/manuscripts",
        params={"direction_id": did, "related_work": False},
        headers=h,
    )
    assert listed.status_code == 200, listed.text
    assert {x["id"] for x in listed.json()} == {old.json()["id"]}


def test_f14_member_stuck_sees_fourth_manuscript(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f14mem")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员全量", "keywords": ["m"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f14stu@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    sid = me.json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F14 Member Read Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    assert client.post(f"/api/v1/literature/{lit.json()['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    hidden = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F14第七篇未写", "kind": "other", "content_md": "## 引言\n没有节。", "direction_ids": [did]},
        headers=sh,
    )
    assert hidden.status_code == 200, hidden.text
    for i in range(6):
        ok = client.post(
            f"/api/v1/labs/{lab}/manuscripts",
            json={
                "title": f"F14卡上{i}",
                "kind": "other",
                "content_md": "## 相关工作\n已写。",
                "direction_ids": [did],
            },
            headers=sh,
        )
        assert ok.status_code == 200, ok.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    stu = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    assert len(stu.get("manuscripts") or []) == 3
    assert all(m.get("related_work_written") for m in stu.get("manuscripts") or [])
    stuck = stu.get("stuck") or {}
    assert stuck.get("count") == len(stuck.get("steps") or [])
    assert any(x.get("key") == "related_work" for x in stuck.get("steps") or [])
    detail = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert len(detail.json().get("manuscripts") or []) == 6
    assert all(m.get("related_work_written") for m in detail.json().get("manuscripts") or [])
    dstuck = detail.json().get("stuck") or {}
    assert dstuck.get("count") == len(dstuck.get("steps") or [])
    assert any(x.get("key") == "related_work" for x in dstuck.get("steps") or [])


def test_f14_language_expand_writeback_related_work(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f14lang")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "译润点名", "keywords": ["lang"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F14 Must Cite Language Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "译润稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    with patch("fengsuan.app.api.v1.language.chat", return_value="一段没有题名的拓展。"):
        exp = client.post(
            f"/api/v1/labs/{lab}/language/expand",
            json={
                "text": "补一段相关工作。",
                "expand_goal": "related_work",
                "manuscript_id": ms["id"],
                "write_mode": "append_heading",
                "heading": "语言工作台",
                "expected_updated_at": ms["updated_at"],
                "literature_item_ids": [paper["id"]],
            },
            headers=h,
        )
    assert exp.status_code == 200, exp.text
    body = exp.json()
    assert body.get("wrote") is True
    assert body.get("related_work_written") is True
    mentioned = {x.get("title") for x in (body.get("core_coverage") or {}).get("core_mentioned") or []}
    assert "F14 Must Cite Language Title" in mentioned
    saved = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "## 相关工作" in (saved.get("content_md") or "")
    assert "F14 Must Cite Language Title" in (saved.get("content_md") or "")
    assert "## 语言工作台" not in (saved.get("content_md") or "")


def test_f14_student_home_scans_all_manuscripts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f14home")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "首页全量", "keywords": ["home"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f14home@example.com")
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F14 Home Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    assert client.post(f"/api/v1/literature/{lit.json()['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    hidden = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F14首页第七篇", "kind": "other", "content_md": "## 引言\n旧稿。", "direction_ids": [did]},
        headers=sh,
    )
    assert hidden.status_code == 200, hidden.text
    for i in range(6):
        ok = client.post(
            f"/api/v1/labs/{lab}/manuscripts",
            json={
                "title": f"F14首页新{i}",
                "kind": "other",
                "content_md": "## 相关工作\nF14 Home Core Paper Title 已写入。",
                "direction_ids": [did],
            },
            headers=sh,
        )
        assert ok.status_code == 200, ok.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    assert any((x.get("key") or "").startswith("related-") for x in home.json().get("next_steps") or [])


def test_f14_member_stuck_count_matches_steps():
    out = build_member_stuck(
        "lab-x",
        role="student",
        direction_id=None,
        reading_count=0,
        manuscripts=[
            {"id": "m1", "title": "稿", "related_work_written": False, "ungrounded_reproduce": "还没复现就写成成功"},
        ],
        overdue_n=1,
        awaiting_n=1,
        blocked_n=1,
    )
    assert out["count"] == len(out["steps"])
    assert out["count"] >= 6
    assert {x["key"] for x in out["steps"]} >= {"direction", "reading", "related_work", "ungrounded", "overdue", "awaiting", "blocked"}


def test_f14_stats_member_core_missing(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f14stat")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员核心列", "keywords": ["st"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f14stat@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    sid = me.json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F14 Stats Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "统计稿", "kind": "other", "content_md": "## 相关工作\n还没写到。", "direction_ids": [did]},
        headers=sh,
    )
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    row = next(x for x in st.json().get("by_member") or [] if x.get("user_id") == sid)
    assert row.get("core_missing", 0) >= 1
    assert "core_missing=1" in (row.get("core_missing_href") or "")
    assert int(row.get("stuck_count") or 0) == (1 if int(row.get("reading_count") or 0) == 0 else 0) + int(
        row.get("related_work_missing") or 0
    )
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "core_missing" in csv.text
    assert "stuck,core_missing" in csv.text.replace(" ", "")
