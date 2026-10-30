from __future__ import annotations

import socket

import pytest

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.netguard import assert_http_url, assert_llm_base_url


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


def test_g55_last_teacher_still_blocked(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g55lt")
    me = client.get("/api/v1/auth/me", headers=h).json()["id"]
    assert client.patch(f"/api/v1/labs/{lab}/members/{me}", json={"role": "student"}, headers=h).status_code == 400
    assert client.delete(f"/api/v1/labs/{lab}/members/{me}", headers=h).status_code == 400


def test_g56_invite_revoke_twice_is_404(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g56iv")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "g56@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    iid = inv.json()["id"]
    assert client.delete(f"/api/v1/invites/{iid}", headers=h).status_code == 200
    again = client.delete(f"/api/v1/invites/{iid}", headers=h)
    assert again.status_code == 404
    used = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "g56@example.com", "password": "student99", "consent": True},
    )
    assert used.status_code == 400


def test_g56_used_invite_cannot_register_twice(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g56rg")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "g56b@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    first = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "g56b@example.com", "password": "student99", "consent": True},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "g56b@example.com", "password": "student99", "consent": True},
    )
    assert second.status_code == 400


def test_g57_cloud_rejects_resolved_loopback_and_decimal(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "ssrf.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        if host == "meta.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        raise socket.gaierror("no name")

    monkeypatch.setattr("fengsuan.app.core.netguard.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ClassifiedHTTPException):
        assert_llm_base_url("http://ssrf.test/v1", kind="cloud")
    with pytest.raises(ClassifiedHTTPException):
        assert_http_url("http://meta.test/latest", allow_private=True)
    with pytest.raises(ClassifiedHTTPException):
        assert_llm_base_url("http://2130706433/v1", kind="cloud")
    onprem = assert_llm_base_url("http://ssrf.test/v1", kind="onprem")
    assert "ssrf.test" in onprem
    public = assert_llm_base_url("https://api.openai.com/v1", kind="cloud")
    assert "openai.com" in public


def test_g58_delete_lab_twice_is_404(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g58dl")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "还在", "keywords": [], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    assert client.delete(f"/api/v1/labs/{lab}", headers=h).status_code == 200
    assert client.delete(f"/api/v1/labs/{lab}", headers=h).status_code == 404
    stolen = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": d.json()["id"]}, headers=h)
    assert stolen.status_code == 404
