from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.identity import User
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.services.scale_eval import EVAL_SET, run_eval, seed_eval_lab


def test_stats_questions_go_to_library_stats():
    from fengsuan.app.services.agent.registry import stub_plan

    for q in ("组里一共有多少篇？", "我们一共收了多少篇论文", "组里论文按年份分布", "统计一下组里的论文"):
        assert stub_plan("lab", q)[0] == "get_library_stats", q
    assert stub_plan("lab", "这个组在做什么方向？")[0] == "get_direction_tree"
    assert stub_plan("lab", "Na Li 还写过什么？")[0] == "get_author_works"


def test_scale_eval_set_scores(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    h = {"Authorization": f"Bearer {r.json()['access']}"}
    lab = client.post("/api/v1/labs", json={"name": "eval", "code": "scaleeval", "description": ""}, headers=h).json()["id"]
    db = SessionLocal()
    try:
        uid = db.query(User.id).filter(User.email == "admin@example.com").scalar()
        for title in (
            "Watermarking Robust Noise Layers",
            "Steganography Attention Networks",
            "Image Watermarking Benchmark Study",
            "Camera Resilient Codes",
        ):
            db.add(LiteratureItem(lab_id=lab, title=title, added_by=uid, source_channel="manual"))
        db.flush()
        keymap = seed_eval_lab(db, lab, uid)
        db.commit()
        assert len(EVAL_SET) == 30
        out = run_eval(db, lab, uid, keymap)
        assert out["people"] == 1.0, out["people_wrong"]
        assert out["route"] == 1.0, out["misses"]
        assert out["relation"] == 1.0, out["misses"]
        assert out["recall_at_10"] >= 0.8, out["misses"]
        assert out["recall_at_3"] >= 0.8, out
        assert out["passed"] >= 28, out["misses"]
    finally:
        db.close()
