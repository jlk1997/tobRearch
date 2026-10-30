from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.identity import User
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.services.agent.loop import agent_answer
from fengsuan.app.services.agent.registry import stub_plan
from fengsuan.app.services.agent.routing import allocate, approx_tokens, budget_block, classify_question
from fengsuan.app.services.biblio import save_references, sync_authorships


def _uid(db):
    return db.query(User.id).filter(User.email == "admin@example.com").scalar()


def _lab(client, code: str):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    h = {"Authorization": f"Bearer {r.json()['access']}"}
    return client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h).json()["id"]


def test_new_routes_sit_behind_old_ones():
    assert stub_plan("lab", "这个组在做什么方向？")[0] == "get_direction_tree"
    assert stub_plan("lab", "Wei Zhang 还写过什么？")[0] == "get_author_works"
    assert stub_plan("lab", "这篇引了哪些？")[0] == "get_paper_references"
    assert stub_plan("lab", "组里谁引了这篇？")[0] == "get_citing_papers"
    assert stub_plan("lab", "和这篇相关的论文有哪些")[0] == "get_related_papers"
    assert stub_plan("lab", "被谁引用")[0] == "get_citation_links"
    assert classify_question("Wei Zhang 还写过什么？") == "person"
    assert classify_question("这篇引了哪些？") == "relation"
    assert classify_question("原文怎么说的") == "passage"
    assert classify_question("总结一下这个方向的主要方法") == "survey"


def test_material_budget_keeps_quota_and_groups_papers():
    long = "水印 " * 3000
    items = [{"label": f"S{i}", "kind": "page", "item_id": "p1" if i < 3 else f"p{i}", "title": f"T{i}", "excerpt": long} for i in range(1, 25)]
    items.append({"label": "S25", "kind": "author", "title": "Wei Zhang", "excerpt": "组里 3 篇"})
    alloc = allocate(items, "person")
    assert sum(alloc.values()) <= 12000
    assert alloc["S25"] == approx_tokens("组里 3 篇")
    block = budget_block(items, "passage")
    assert block.count("── 同一篇：《T1》") == 1
    assert block.index("[S1]") < block.index("[S2]") < block.index("[S3]")


def test_same_name_asks_back_then_resolves(client):
    lab = _lab(client, "scag1")
    db = SessionLocal()
    try:
        uid = _uid(db)
        a = LiteratureItem(lab_id=lab, title="Vision Watermark Survey", authors_json=json.dumps(["Wei Zhang", "Na Li"]), added_by=uid)
        b = LiteratureItem(lab_id=lab, title="Organic Chemistry Catalysts", authors_json=json.dumps(["Wei Zhang", "Bob Stone"]), added_by=uid)
        db.add_all([a, b])
        db.flush()
        for it in (a, b):
            sync_authorships(db, it)
        db.commit()
        out = agent_answer(db, user_id=uid, lab_id=lab, scope="lab", question="Wei Zhang 还写过什么？", feature="ask")
        pack = out.get("ask_back")
        assert pack and len(pack["options"]) == 2, out
        assert "哪一位" in out["answer"]
        assert out["citations"], out["answer"]
        pick = next(o for o in pack["options"] if "Organic" in o["question"])
        again = agent_answer(db, user_id=uid, lab_id=lab, scope="lab", question=pick["question"], feature="ask")
        assert not again.get("ask_back")
        titles = {s.get("title") for s in again["sources"]}
        assert "Organic Chemistry Catalysts" in titles
        assert "Vision Watermark Survey" not in titles
    finally:
        db.close()


def test_relation_answer_carries_groups(client):
    lab = _lab(client, "scag2")
    db = SessionLocal()
    try:
        uid = _uid(db)
        x = LiteratureItem(lab_id=lab, title="Camera Resilient Watermarking", added_by=uid)
        y = LiteratureItem(lab_id=lab, title="HiDDeN Hiding Data with Deep Networks", added_by=uid)
        db.add_all([x, y])
        db.flush()
        save_references(
            db,
            x,
            [
                {"position": 0, "title": "HiDDeN Hiding Data with Deep Networks", "year": 2018},
                {"position": 1, "title": "Some outside paper nobody has", "year": 2019},
            ],
            source="grobid",
        )
        db.commit()
        out = agent_answer(
            db, user_id=uid, lab_id=lab, scope="lab", question="Camera Resilient Watermarking 这篇引了哪些？", feature="ask"
        )
        assert out["steps"][0]["tool"] == "get_paper_references"
        assert out["question_type"] == "relation"
        groups = {g["kind"]: g for g in out["relation_groups"]}
        assert "cites" in groups and groups["cites"]["label"] == "它引了"
        assert any(s.get("item_id") == y.id for s in out["sources"])
    finally:
        db.close()
