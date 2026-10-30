from __future__ import annotations

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


def test_g52_archive_twice_is_404(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g52dl")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "归档一次", "keywords": [], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    assert client.delete(f"/api/v1/directions/{did}", headers=h).status_code == 200
    again = client.delete(f"/api/v1/directions/{did}", headers=h)
    assert again.status_code == 404


def test_g54_cannot_pick_other_lab_direction(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "g54aa")
    lab_b = _lab(client, h, "g54bb")
    d = client.post(
        f"/api/v1/labs/{lab_b}/directions",
        json={"name": "乙组", "keywords": ["b"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    stolen = client.put(f"/api/v1/labs/{lab_a}/me/direction", json={"direction_id": d.json()["id"]}, headers=h)
    assert stolen.status_code == 404


def test_g53_cloud_url_rejects_loopback_and_metadata():
    with pytest.raises(ClassifiedHTTPException):
        assert_llm_base_url("http://127.0.0.1:8000/v1", kind="cloud")
    with pytest.raises(ClassifiedHTTPException):
        assert_llm_base_url("http://localhost/v1", kind="cloud")
    with pytest.raises(ClassifiedHTTPException):
        assert_llm_base_url("http://10.0.0.8/v1", kind="cloud")
    with pytest.raises(ClassifiedHTTPException):
        assert_http_url("http://169.254.169.254/latest", allow_private=True)
    onprem = assert_llm_base_url("http://127.0.0.1:8000/v1", kind="onprem")
    assert onprem.startswith("http://127.0.0.1")
    public = assert_llm_base_url("https://api.openai.com/v1", kind="cloud")
    assert "openai.com" in public


def test_g53_cloud_credential_loopback_rejected(client):
    token = _admin(client)
    h = _h(token)
    bad = client.put(
        "/api/v1/me/llm-credentials",
        json={"provider": "openai", "api_key": "sk-cloudloop", "kind": "cloud", "base_url": "http://127.0.0.1:9/v1"},
        headers=h,
    )
    assert bad.status_code == 422, bad.text
    ok = client.put(
        "/api/v1/me/llm-credentials",
        json={"provider": "vllm", "api_key": "sk-onpremloop", "kind": "onprem", "base_url": "http://127.0.0.1:8000/v1"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
