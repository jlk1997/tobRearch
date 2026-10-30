from __future__ import annotations

import pytest

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.netguard import assert_git_https
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.identity import Role, User, UserRole


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


def _grant_admin(email: str) -> str:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        role = db.query(Role).filter(Role.code == "admin").first()
        assert u is not None and role is not None
        if db.query(UserRole).filter(UserRole.user_id == u.id, UserRole.role_id == role.id).first() is None:
            db.add(UserRole(user_id=u.id, role_id=role.id))
            db.commit()
        return u.id
    finally:
        db.close()


def test_g59_second_admin_disable_then_last_blocked(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g59ad")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "g59b@example.com", "role": "teacher"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "g59b@example.com", "password": "teacher99", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    sid = _grant_admin("g59b@example.com")
    assert client.patch(f"/api/v1/admin/users/{sid}", json={"is_active": False}, headers=h).status_code == 200
    me = client.get("/api/v1/auth/me", headers=h).json()["id"]
    last = client.patch(f"/api/v1/admin/users/{me}", json={"is_active": False}, headers=h)
    assert last.status_code == 400, last.text


def test_g60_manuscript_delete_twice_and_overleaf_404(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g60ms")
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "删一次", "kind": "other"}, headers=h)
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    assert client.delete(f"/api/v1/manuscripts/{mid}", headers=h).status_code == 200
    assert client.delete(f"/api/v1/manuscripts/{mid}", headers=h).status_code == 404
    sync = client.post(f"/api/v1/manuscripts/{mid}/overleaf-sync", json={"git_url": "https://git.overleaf.com/abc"}, headers=h)
    assert sync.status_code == 404


def test_g61_overleaf_rejects_metadata_keeps_public():
    with pytest.raises((ClassifiedHTTPException, RuntimeError)):
        assert_git_https("https://169.254.169.254/repo")
    with pytest.raises((ClassifiedHTTPException, RuntimeError)):
        assert_git_https("http://git.overleaf.com/abc")
    ok = assert_git_https("https://git.overleaf.com/abc")
    assert "overleaf.com" in ok


def test_g61_manuscript_overleaf_metadata_rejected(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g61ol")
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "同步", "kind": "other"}, headers=h)
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    bad = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"overleaf_git_url": "https://169.254.169.254/latest"},
        headers=h,
    )
    assert bad.status_code == 400, bad.text
    good = client.patch(
        f"/api/v1/manuscripts/{mid}",
        json={"overleaf_git_url": "https://git.overleaf.com/abc"},
        headers=h,
    )
    assert good.status_code == 200, good.text


def test_g61_grobid_metadata_skipped(monkeypatch):
    from fengsuan.app.services import pdfparse

    class _Settings:
        grobid_url = "http://169.254.169.254"

    monkeypatch.setattr(pdfparse, "get_settings", lambda: _Settings())
    assert pdfparse._try_grobid(b"%PDF-1.4") is None


def test_g62_duplicate_lab_code_and_member(client):
    token = _admin(client)
    h = _h(token)
    assert client.post("/api/v1/labs", json={"name": "甲", "code": "g62dup", "description": ""}, headers=h).status_code == 200
    again = client.post("/api/v1/labs", json={"name": "乙", "code": "g62dup", "description": ""}, headers=h)
    assert again.status_code == 400, again.text
    lab = _lab(client, h, "g62mb")
    other = _lab(client, h, "g62mc")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "g62m@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "g62m@example.com", "password": "student99", "consent": True},
    )
    sid = client.get("/api/v1/auth/me", headers=_h(client.post("/api/v1/auth/login", json={"email": "g62m@example.com", "password": "student99"}).json()["access"])).json()["id"]
    first = client.post(f"/api/v1/labs/{other}/members", json={"user_id": sid, "role": "student"}, headers=h)
    assert first.status_code == 200, first.text
    dup = client.post(f"/api/v1/labs/{other}/members", json={"user_id": sid, "role": "student"}, headers=h)
    assert dup.status_code == 400, dup.text
