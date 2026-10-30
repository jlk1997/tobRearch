"""方向简报和组里一页纸能从现有论文里写出来。"""

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.lab import Lab, ResearchDirection
from fengsuan.app.services.lab_brief import refresh_direction_brief, refresh_lab_brief, sample_title, spoken_detail


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access']}"}


def test_direction_brief_and_home(client):
    h = _admin(client)
    lab = client.post("/api/v1/labs", json={"name": "简报组", "code": "brieflab", "description": "看图"}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    direction = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "图神经网络", "description": "节点分类", "keywords": ["gnn"]},
        headers=h,
    )
    assert direction.status_code == 200, direction.text
    db = SessionLocal()
    try:
        row = db.get(ResearchDirection, direction.json()["id"])
        brief = refresh_direction_brief(db, row, force=True)
        assert "图神经网络" in brief["one_line"] or brief["one_line"]
        lab_row = db.get(Lab, lab_id)
        page = refresh_lab_brief(db, lab_row, force=True)
        db.commit()
        assert "图神经网络" in page["text"]
    finally:
        db.close()
    home = client.get(f"/api/v1/labs/{lab_id}/assistant/home", headers=h)
    assert home.status_code == 200, home.text
    assert "图神经网络" in (home.json().get("lab_brief") or "")
    assert home.json().get("suggested")


def test_spoken_detail_does_not_repeat_the_title():
    raw = "Camera Resilient Watermarking：Print-Camera Resilient Image Watermarking"
    said = spoken_detail("Camera Resilient Watermarking", raw)
    assert said == ""
    sample = sample_title("Camera Resilient Watermarking", raw)
    assert "Camera Resilient Watermarking：" not in sample
    assert "Print-Camera" in sample
