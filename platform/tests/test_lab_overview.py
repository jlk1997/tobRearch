"""组里一览：方向、在读的人、最近产出。不新开表。"""


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_overview_lists_direction_readers_and_hides_outsiders(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "一览", "code": "glance1", "description": ""}, headers=h).json()["id"]
    other = client.post("/api/v1/labs", json={"name": "另一组", "code": "glance2", "description": ""}, headers=h).json()["id"]
    direction = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "水印"}, headers=h)
    assert direction.status_code == 200, direction.text
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "打印后再拍", "direction_ids": [direction.json()["id"]]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    item_id = paper.json()["id"]
    assert client.post(f"/api/v1/literature/{item_id}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    outsider = client.post(
        f"/api/v1/labs/{other}/literature",
        json={"title": "不该出现"},
        headers=h,
    )
    assert outsider.status_code == 200, outsider.text

    me = client.get("/api/v1/auth/me", headers=h).json()
    hung = client.patch(
        f"/api/v1/labs/{lab}/members/{me['id']}",
        json={"direction_id": direction.json()["id"]},
        headers=h,
    )
    assert hung.status_code == 200, hung.text
    overview = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()["overview"]
    assert overview["counts"]["directions"] >= 1
    assert overview["counts"]["literature"] == 1
    card = next(row for row in overview["directions"] if row["name"] == "水印")
    assert any(paper["title"] == "打印后再拍" for paper in card["reading"])
    assert any(person["user_id"] == me["id"] for person in card["members"])
    mine = next(person for person in overview["people"] if person["user_id"] == me["id"])
    assert "在读" in (mine.get("work_line") or "")
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    rows = listed["items"] if isinstance(listed, dict) else listed
    hit = next(row for row in rows if row["id"] == item_id)
    assert hit["lab_reader_total"] >= 1
    assert hit["lab_readers"]
    detail = client.get(f"/api/v1/literature/{item_id}/detail", headers=h).json()
    assert detail["lab_reader_total"] >= 1
    home = client.get(f"/api/v1/labs/{lab}/assistant/home", headers=h).json()
    assert home["overview"]["counts"]["literature"] == 1


def test_overview_opens_the_page_someone_reached_and_shows_unplaced_reading(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "页码", "code": "glance3", "description": ""}, headers=h).json()["id"]
    direction = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "鲁棒"}, headers=h).json()["id"]
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert client.patch(f"/api/v1/labs/{lab}/members/{me['id']}", json={"direction_id": direction}, headers=h).status_code == 200
    loose = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "还没挂上"}, headers=h).json()["id"]
    assert client.post(f"/api/v1/literature/{loose}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    assert client.post(f"/api/v1/literature/{loose}/progress", json={"page_no": 6}, headers=h).status_code == 200

    overview = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()["overview"]
    card = next(row for row in overview["directions"] if row["name"] == "鲁棒")
    hit = next(paper for paper in card["reading"] if paper["title"] == "还没挂上")
    assert hit["unplaced"] is True
    assert "page=6" in hit["href"]
    mine = next(person for person in overview["people"] if person["user_id"] == me["id"])
    assert "读到第 6 页" in (mine.get("work_line") or "")
    assert any("page=6" in (paper.get("href") or "") for paper in mine["reading"])


def test_stale_work_line_picks_up_the_page_someone_reached(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "简报", "code": "glance4", "description": ""}, headers=h).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "读到这里"}, headers=h).json()["id"]
    assert client.post(f"/api/v1/literature/{paper}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    assert client.post(f"/api/v1/literature/{paper}/progress", json={"page_no": 6}, headers=h).status_code == 200
    me = client.get("/api/v1/auth/me", headers=h).json()

    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.services.lab_brief import refresh_member_work, work_public

    db = SessionLocal()
    try:
        mem = db.query(LabMembership).filter(LabMembership.lab_id == lab, LabMembership.user_id == me["id"]).one()
        mem.work_brief_json = '{"text": "管理员：在读《读到这里》", "sources": [{"kind": "literature", "id": "%s", "title": "读到这里"}], "fingerprint": "stale"}' % paper
        db.commit()
    finally:
        db.close()

    overview = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()["overview"]
    mine = next(person for person in overview["people"] if person["user_id"] == me["id"])
    assert "读到第 6 页" in (mine.get("work_line") or "")

    db = SessionLocal()
    try:
        mem = db.query(LabMembership).filter(LabMembership.lab_id == lab, LabMembership.user_id == me["id"]).one()
        brief = refresh_member_work(db, lab, mem)
        db.commit()
        public = work_public(lab, mem)
    finally:
        db.close()
    assert "读到第 6 页" in (brief.get("text") or "")
    hrefs = [row.get("href") or "" for row in public.get("sources") or []]
    assert any("page=6" in href for href in hrefs)


def test_who_is_reading_names_the_page(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "在读页", "code": "glance5", "description": ""}, headers=h).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "页码这篇"}, headers=h).json()["id"]
    assert client.post(f"/api/v1/literature/{paper}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    assert client.post(f"/api/v1/literature/{paper}/progress", json={"page_no": 6}, headers=h).status_code == 200

    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    rows = listed["items"] if isinstance(listed, dict) else listed
    hit = next(row for row in rows if row["id"] == paper)
    mark = next(row for row in hit["lab_reader_marks"] if row.get("last_page_no") == 6)
    assert mark["name"]
    detail = client.get(f"/api/v1/literature/{paper}/detail", headers=h).json()
    assert any(row.get("last_page_no") == 6 for row in detail.get("lab_reader_marks") or [])

    asked = client.post(
        f"/api/v1/labs/{lab}/ask",
        json={"q": "《页码这篇》谁在读？", "scope": "lab"},
        headers=h,
    )
    assert asked.status_code == 200, asked.text
    answer = asked.json().get("answer") or ""
    assert "读到第 6 页" in answer
    direction = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "页码方向"}, headers=h).json()["id"]
    assert client.patch(f"/api/v1/literature/{paper}", json={"direction_ids": [direction]}, headers=h).status_code == 200
    overview = client.get(f"/api/v1/labs/{lab}/directions/{direction}/overview", headers=h).json()
    paper_row = next(row for row in overview["literature"] if row["id"] == paper)
    assert any(mark.get("last_page_no") == 6 for mark in paper_row.get("lab_reader_marks") or [])
