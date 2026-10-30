"""去压力化：计算仍在，呈现变少、变中性。"""

from __future__ import annotations

from fengsuan.app.services.insight import present_student_next_steps


def test_present_student_next_steps_caps_and_neutralizes():
    raw = [
        {"key": "reading", "label": "开始阅读", "text": "打开文献库，把一篇标成在读。", "href": "/l"},
        {"key": "related-1", "label": "写相关工作", "text": "「稿」还没有相关工作。", "href": "/w"},
        {"key": "ungrounded-2", "label": "还没复现就写成成功", "text": "把未测写成成功。", "href": "/m"},
    ]
    shown = present_student_next_steps(raw)
    assert len(shown) == 2
    assert shown[0]["label"] == "打开文献"
    assert "催" not in shown[0]["text"]
    assert shown[1]["key"] == "related-1"
    assert shown[0]["href"] == "/l"


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def test_home_keeps_full_next_steps_and_adds_shown(client):
    h = {"Authorization": f"Bearer {_admin(client)}"}
    lab = client.post("/api/v1/labs", json={"name": "dep", "code": "dep", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab.json()["id"]}, headers=h)
    assert home.status_code == 200, home.text
    body = home.json()
    assert "next_steps" in body
    assert "next_steps_shown" in body
    assert isinstance(body["next_steps_shown"], list)
    assert len(body["next_steps_shown"]) <= max(2, len(body["next_steps"]))
