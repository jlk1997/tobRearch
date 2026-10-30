"""C235：方向卡住写明缺哪几篇，并能转成文献调研。"""


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_c235_direction_unread_names_the_paper_and_route_survives(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "c235", "code": "c235", "description": ""}, headers=h).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "水印", "keywords": ["watermark"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "C235 方向上还没人读", "abstract": "watermark"},
        headers=h,
    ).json()
    assert client.patch(f"/api/v1/literature/{paper['id']}", json={"direction_ids": [did]}, headers=h).status_code == 200
    saved = client.patch(
        f"/api/v1/directions/{did}",
        json={"route": [{"stage": "入门", "title": "先读综述"}]},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    overview = client.get(f"/api/v1/labs/{lab}/directions/{did}", headers=h).json()
    unread = next(step for step in (overview.get("stuck") or {}).get("active") or [] if step.get("key") == "unread")
    assert any(ev.get("title") == "C235 方向上还没人读" for ev in unread.get("evidence") or [])
    assert "还没人在读" in unread.get("evidence_text", "")
    assert unread.get("search", {}).get("literature_item_ids")
    route = (overview.get("frontier") or {}).get("route") or []
    assert route and route[0]["title"] == "先读综述"
