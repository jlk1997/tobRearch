"""C237：按缺口草拟阅读路线，跳过已读，投稿去向不排名。"""

from fengsuan.app.services.reading_route import draft_reading_route, next_on_route, outlet_text, venue_outlets


def test_c237_draft_splits_stages_and_skips_finished():
    route = draft_reading_route(
        [
            {"title": "A Survey of Watermarks", "shelf": "入门综述"},
            {"title": "The Classic Mark", "shelf": "奠基经典"},
            {"title": "This Year", "shelf": "近两年代表"},
        ],
        [{"title": "组里还没读的"}],
    )
    stages = [row["stage"] for row in route]
    assert stages[:3] == ["入门", "进阶", "前沿"]
    assert route[-1]["title"] == "组里还没读的"
    progress = next_on_route(
        route,
        [{"title": "A Survey of Watermarks", "status": "finished"}],
    )
    assert progress["finished"] == ["A Survey of Watermarks"]
    assert progress["next"]["title"] == "The Classic Mark"
    assert progress["next"]["state"] == "还没读"
    text = outlet_text(venue_outlets([{"venue": "CVPR"}, {"venue": "CVPR"}, {"venue": "NeurIPS"}]))
    assert "CVPR" in text and "不是排名" in text


def test_c237_teacher_can_draft_without_saving(client):
    token = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"}).json()["access"]
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "c237", "code": "c237", "description": ""}, headers=h).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "水印", "keywords": ["watermark"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "C237 还没人读", "abstract": "wm"},
        headers=h,
    ).json()
    assert client.patch(f"/api/v1/literature/{paper['id']}", json={"direction_ids": [did]}, headers=h).status_code == 200
    drafted = client.post(f"/api/v1/labs/{lab}/directions/{did}/route-draft", headers=h)
    assert drafted.status_code == 200, drafted.text
    titles = [row["title"] for row in drafted.json().get("route") or []]
    assert "C237 还没人读" in titles
    saved = client.get(f"/api/v1/labs/{lab}/directions/{did}", headers=h).json()
    assert not (saved.get("frontier") or {}).get("route")
