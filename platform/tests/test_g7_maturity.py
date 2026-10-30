from __future__ import annotations


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


def test_g7_preflight_allows_create_without_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g7pf")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G7 No Card Reproduce TitleX", "abstract": "a"},
        headers=h,
    ).json()
    listed = client.get(
        f"/api/v1/labs/{lab}/reproduce-runs",
        params={"literature_item_id": paper["id"]},
        headers=h,
    )
    assert listed.status_code == 200, listed.text
    pack = listed.json()
    assert isinstance(pack, dict)
    pf = pack.get("preflight") or {}
    assert pf.get("has_method_card") is False
    created = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": paper["id"]},
        headers=h,
    )
    assert created.status_code == 200, created.text
    cpf = created.json().get("preflight") or {}
    assert "has_method_card" in cpf
    if (cpf.get("gpu_mode") or "") == "off":
        hrefs = " ".join((x.get("href") or "") + " " + (x.get("admin_href") or "") for x in cpf.get("missing") or [])
        assert "awaiting_gpu" in hrefs or "/admin" in hrefs
