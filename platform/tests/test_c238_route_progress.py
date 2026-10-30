"""C238：路线标出本人进度；没记下时按前沿草拟并写明不是任务。"""

from fengsuan.app.services.reading_route import annotate_route


def test_c238_annotate_only_this_reader():
    steps = annotate_route(
        [{"stage": "入门", "title": "已读这篇", "item_id": "a"}, {"stage": "进阶", "title": "还没看"}],
        [{"title": "已读这篇", "item_id": "a", "status": "finished"}],
    )
    assert steps[0]["state"] == "已读完"
    assert steps[1]["state"] == "还没读"


def test_c238_overview_marks_my_progress_and_does_not_invent_a_saved_route(client):
    token = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"}).json()["access"]
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "c238", "code": "c238", "description": ""}, headers=h).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "水印", "keywords": ["watermark"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "C238 已经读完", "abstract": "done"},
        headers=h,
    ).json()
    saved = client.patch(
        f"/api/v1/directions/{did}",
        json={"route": [{"stage": "入门", "title": "C238 已经读完", "item_id": paper["id"]}, {"stage": "进阶", "title": "还没看的下一篇"}]},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    marked = client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "finished"}, headers=h)
    assert marked.status_code == 200, marked.text
    overview = client.get(f"/api/v1/labs/{lab}/directions/{did}", headers=h).json()
    mine = overview.get("my_route") or {}
    assert mine.get("saved") is True
    states = {row["title"]: row["state"] for row in mine.get("steps") or []}
    assert states["C238 已经读完"] == "已读完"
    assert states["还没看的下一篇"] == "还没读"
