from __future__ import annotations

from unittest.mock import patch


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
            "display_name": "生十五",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f15_student_home_ungrounded_and_live_reading(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f15home")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "诚实下一步", "keywords": ["honest"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f15home@example.com")
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F15 Home Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    pid = paper.json()["id"]
    for i in range(8):
        extra = client.post(
            f"/api/v1/labs/{lab}/literature",
            json={"title": f"F15 Finished Paper {i} TitleX", "abstract": "f", "direction_ids": [did]},
            headers=h,
        )
        assert extra.status_code == 200, extra.text
        assert (
            client.post(f"/api/v1/literature/{extra.json()['id']}/mark-read", json={"status": "finished"}, headers=sh).status_code
            == 200
        )
    assert client.post(f"/api/v1/literature/{pid}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "F15未测稿",
            "kind": "other",
            "content_md": "## 相关工作\nF15 Home Core Paper Title 已写入。我们成功完成实验。",
            "direction_ids": [did],
        },
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    steps = home.json().get("next_steps") or []
    assert not any(x.get("key") == "reading" for x in steps)
    ug = next(x for x in steps if (x.get("key") or "").startswith("ungrounded-"))
    assert "还没复现就写成成功" in (ug.get("label") or "")
    assert f"/manuscripts/{ms.json()['id']}" in (ug.get("href") or "")


def test_f15_language_expand_append_writes_related_work(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f15lang")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "文末追加", "keywords": ["ap"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F15 Must Cite Append Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "追加稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    with patch("fengsuan.app.api.v1.language.chat", return_value="一段没有题名的拓展。"):
        exp = client.post(
            f"/api/v1/labs/{lab}/language/expand",
            json={
                "text": "补一段相关工作。",
                "expand_goal": "related_work",
                "manuscript_id": ms["id"],
                "write_mode": "append",
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
    assert "F15 Must Cite Append Title" in mentioned
    saved = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "## 相关工作" in (saved.get("content_md") or "")
    assert "F15 Must Cite Append Title" in (saved.get("content_md") or "")
    assert "## 语言工作台" not in (saved.get("content_md") or "")


def test_f15_stats_member_ungrounded(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f15stat")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员未测", "keywords": ["ug"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f15stat@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    sid = me.json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F15 Stats Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "统计未测",
            "kind": "other",
            "content_md": "## 相关工作\nF15 Stats Core Paper Title 已写入。我们成功完成实验。",
            "direction_ids": [did],
        },
        headers=sh,
    )
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    row = next(x for x in st.json().get("by_member") or [] if x.get("user_id") == sid)
    assert row.get("ungrounded", 0) >= 1
    assert "ungrounded=1" in (row.get("ungrounded_href") or "")
    assert int(row.get("stuck_count") or 0) == (1 if int(row.get("reading_count") or 0) == 0 else 0) + int(
        row.get("related_work_missing") or 0
    )
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "ungrounded" in csv.text
    assert "stuck,ungrounded" in csv.text.replace(" ", "")
