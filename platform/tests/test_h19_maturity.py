from __future__ import annotations

import os


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


def test_dataset_literature_titles_and_patch(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "dslit")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Linked Dataset Paper", "authors": []},
        headers=h,
    ).json()
    created = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "WithLit", "literature_item_ids": [lit["id"]]},
        headers=h,
    )
    assert created.status_code == 200, created.text
    titles = [x.get("title") for x in created.json().get("literature") or []]
    assert "Linked Dataset Paper" in titles
    detail = client.get(f"/api/v1/datasets/{created.json()['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    assert any(x.get("id") == lit["id"] for x in detail.json().get("literature") or [])
    cleared = client.patch(f"/api/v1/datasets/{created.json()['id']}", json={"literature_item_ids": []}, headers=h)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json().get("literature") == []


def test_reproduce_list_exposes_linked_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rplist")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "List Paper", "abstract": "Training uses CUDA GPU.", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ds_row = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "ListSet"}, headers=h)
    assert ds_row.status_code == 200, ds_row.text
    root = os.environ.get("FILE_ROOT") or "."
    os.makedirs(root, exist_ok=True)
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit.json()["id"],
            "dataset_id": ds_row.json()["id"],
            "dataset_path": root,
            "claimed_metrics": {"acc": 0.9},
        },
        headers=h,
    )
    assert run.status_code == 200, run.text
    assert run.json().get("literature_title") == "List Paper"
    listed = client.get(f"/api/v1/labs/{lab}/reproduce-runs", headers=h)
    assert listed.status_code == 200, listed.text
    hit = next(x for x in listed.json() if x["id"] == run.json()["id"])
    assert hit.get("literature_title") == "List Paper"
    assert (hit.get("dataset") or {}).get("name") == "ListSet"
