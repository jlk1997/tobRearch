from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["product"] == "蜂算科研"


def test_me_unauthorized(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_register_without_invite(client):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": "nope",
            "email": "a@x.com",
            "password": "password12",
            "consent": True,
        },
    )
    assert r.status_code == 400


def test_invite_login_and_cross_lab_403(client):
    token = _admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    lab_a = client.post("/api/v1/labs", json={"name": "A", "code": "laba", "description": ""}, headers=headers)
    assert lab_a.status_code == 200, lab_a.text
    lab_b = client.post("/api/v1/labs", json={"name": "B", "code": "labb", "description": ""}, headers=headers)
    assert lab_b.status_code == 200
    id_a = lab_a.json()["id"]
    id_b = lab_b.json()["id"]

    inv = client.post(
        f"/api/v1/labs/{id_a}/invites",
        json={"email": "stu@example.com", "role": "student"},
        headers=headers,
    )
    assert inv.status_code == 200, inv.text
    invite_token = inv.json()["token"]

    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": invite_token,
            "email": "stu@example.com",
            "password": "student99",
            "display_name": "学生甲",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    stu = reg.json()["access"]
    sh = {"Authorization": f"Bearer {stu}"}

    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200
    assert me.json()["email"] == "stu@example.com"

    forbidden = client.get(f"/api/v1/labs/{id_b}", headers=sh)
    assert forbidden.status_code == 403

    ok = client.get(f"/api/v1/labs/{id_a}", headers=sh)
    assert ok.status_code == 200


def test_key_mask(client):
    token = _admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    put = client.put(
        "/api/v1/me/llm-credentials",
        json={"provider": "openai", "api_key": "sk-abcdefghijk", "base_url": "https://example.com"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert "sk-abcdefghijk" not in str(body)
    assert body["api_key_masked"].endswith("hijk")
    assert "sk-abcdefghijk" not in put.text


def test_manuscript_peer_read_not_write(client):
    token = _admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "C", "code": "labc", "description": ""}, headers=headers).json()
    lab_id = lab["id"]
    inv1 = client.post(
        f"/api/v1/labs/{lab_id}/invites", json={"email": "s1@example.com", "role": "student"}, headers=headers
    ).json()["token"]
    inv2 = client.post(
        f"/api/v1/labs/{lab_id}/invites", json={"email": "s2@example.com", "role": "student"}, headers=headers
    ).json()["token"]
    a = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv1, "email": "s1@example.com", "password": "student99", "consent": True},
    ).json()["access"]
    b = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv2, "email": "s2@example.com", "password": "student99", "consent": True},
    ).json()["access"]
    ha = {"Authorization": f"Bearer {a}"}
    hb = {"Authorization": f"Bearer {b}"}
    ms = client.post(
        f"/api/v1/labs/{lab_id}/manuscripts",
        json={"title": "我的手稿", "content_md": "正文"},
        headers=ha,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    read = client.get(f"/api/v1/manuscripts/{mid}", headers=hb)
    assert read.status_code == 200
    assert "正文" in read.json()["content_md"]
    patch = client.patch(f"/api/v1/manuscripts/{mid}", json={"content_md": "篡改"}, headers=hb)
    assert patch.status_code == 403


def test_stats_match_db(client):
    token = _admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    lab_id = client.post("/api/v1/labs", json={"name": "S", "code": "labs", "description": ""}, headers=headers).json()["id"]
    client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Paper One", "authors": ["A"], "publish_year": 2024, "doi": "10.1/x"},
        headers=headers,
    )
    client.post(
        f"/api/v1/labs/{lab_id}/manuscripts",
        json={"title": "草稿", "kind": "conference"},
        headers=headers,
    )
    st = client.get(f"/api/v1/labs/{lab_id}/stats", headers=headers)
    assert st.status_code == 200
    data = st.json()
    assert data["literature_count"] == 1
    assert data["manuscript_count"] == 1


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]
