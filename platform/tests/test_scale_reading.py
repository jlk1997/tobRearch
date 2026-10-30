from __future__ import annotations

import json
import random
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import ResearchDirection
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureDirectionLink, LiteratureItem
from fengsuan.app.services.agent.loop import agent_answer
from fengsuan.app.services.agent.registry import stub_plan
from fengsuan.app.services.lab_themes import refresh_direction_themes, week_change
from fengsuan.app.services.reading_tier import (
    ABSTRACT_FIRST_BACKLOG,
    _deep_spans,
    intake_line,
    intake_progress,
    preread_depth,
    score_items,
    unread_backlog,
    write_deep_notes,
)


def _uid(db):
    return db.query(User.id).filter(User.email == "admin@example.com").scalar()


def _lab(client, code: str):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    h = {"Authorization": f"Bearer {r.json()['access']}"}
    return client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h).json()["id"]


def _vec(center: int, rng: random.Random) -> list[float]:
    v = [rng.gauss(0, 0.05) for _ in range(16)]
    v[center] += 1.0
    return v


def test_scores_and_abstract_first_under_backlog(client):
    lab = _lab(client, "scrd1")
    db = SessionLocal()
    try:
        uid = _uid(db)
        d = ResearchDirection(lab_id=lab, name="水印")
        db.add(d)
        db.flush()
        old = utcnow() - timedelta(days=30)
        core = LiteratureItem(lab_id=lab, title="Core one", abstract="a" * 300, added_by=uid, created_at=old, read_level=0)
        plain = LiteratureItem(lab_id=lab, title="Plain one", abstract="b" * 300, added_by=uid, created_at=old, read_level=0)
        fresh = LiteratureItem(lab_id=lab, title="Fresh one", abstract="c" * 300, added_by=uid, read_level=0)
        db.add_all([core, plain, fresh])
        db.flush()
        db.add(LiteratureDirectionLink(item_id=core.id, direction_id=d.id, is_core=True))
        db.commit()
        scores = score_items(db, lab, [core, plain, fresh])
        assert scores[core.id] > scores[fresh.id] > scores[plain.id]
        assert preread_depth(db, plain, ABSTRACT_FIRST_BACKLOG + 1) == "abstract"
        assert preread_depth(db, plain, 3) == "auto"
        assert unread_backlog(db, lab) == 3
    finally:
        db.close()


def test_deep_notes_level_three_and_pause_line(client, monkeypatch):
    lab = _lab(client, "scrd2")
    db = SessionLocal()
    try:
        uid = _uid(db)
        it = LiteratureItem(lab_id=lab, title="Deep paper", added_by=uid, read_level=2)
        db.add(it)
        db.flush()
        pages = ["Intro text", "3 Method we embed bits", "More method", "4 Experiments on COCO", "Results table"]
        spans = _deep_spans(pages, [{"title": "3 Method", "page_no": 2}, {"title": "4 Experiments", "page_no": 4}])
        assert spans == [("3 Method", 2, 4), ("4 Experiments", 4, 5)]
        monkeypatch.setattr("fengsuan.app.services.pagecache.item_pages", lambda _db, _it: (pages, "cache"))
        monkeypatch.setattr("fengsuan.app.services.pagecache.item_structure", lambda _db, _iid: {"sections": [{"title": "3 Method", "page_no": 2}]})
        monkeypatch.setattr(
            "fengsuan.app.services.llm.chat",
            lambda *a, **k: json.dumps({"notes": [{"text": "用可微噪声层模拟拍照", "page": 3}, {"text": "页码乱写", "page": 99}]}),
        )
        db.add(
            LiteratureAnalysis(
                item_id=it.id,
                analysis_type="method_card",
                result_json=json.dumps({"source": "pdf", "problem": {"text": "x", "pages": [1]}}),
                citations_json="[]",
                created_by=uid,
            )
        )
        db.flush()
        row = write_deep_notes(db, it, uid)
        assert row is not None
        notes = json.loads(row.result_json)["sections"][0]["notes"]
        assert [n["page"] for n in notes] == [3, 2]
        assert it.read_level == 3
        monkeypatch.setattr("fengsuan.app.services.digest.digest_room", lambda _db, _lab: False)
        p = intake_progress(db, lab)
        assert "额度用到九成" in p["paused"]
        assert "额度用到九成" in intake_line(p)
    finally:
        db.rollback()
        db.close()


def test_themes_split_keep_names_and_week_change(client):
    lab = _lab(client, "scrd3")
    db = SessionLocal()
    rng = random.Random(7)
    try:
        uid = _uid(db)
        d = ResearchDirection(lab_id=lab, name="图像安全")
        db.add(d)
        db.flush()
        words = ["watermark robust print", "steganography hiding image", "deepfake detection face"]
        items = []
        for c, w in enumerate(words):
            for n in range(6):
                it = LiteratureItem(
                    lab_id=lab,
                    title=f"{w} study {n}",
                    added_by=uid,
                    paper_embedding_json=json.dumps(_vec(c, rng)),
                )
                items.append(it)
        db.add_all(items)
        db.flush()
        for it in items:
            db.add(LiteratureDirectionLink(item_id=it.id, direction_id=d.id))
        db.commit()
        themes = refresh_direction_themes(db, d)
        assert len(themes) == 3
        assert sorted(t["size"] for t in themes) == [6, 6, 6]
        names = {t["name"] for t in themes}
        assert any("watermark" in n or "robust" in n or "print" in n for n in names), names
        db.commit()
        for n in range(3):
            extra = LiteratureItem(lab_id=lab, title=f"watermark robust print new {n}", added_by=uid, paper_embedding_json=json.dumps(_vec(0, rng)))
            db.add(extra)
            db.flush()
            db.add(LiteratureDirectionLink(item_id=extra.id, direction_id=d.id))
        db.commit()
        again = refresh_direction_themes(db, d)
        assert sum(1 for t in again if t["rewritten"]) == 1
        assert {t["name"] for t in again if not t["rewritten"]} <= names
        db.commit()
        week = week_change(db, lab)
        assert week["count"] == 21
        assert "这周进来 21 篇" in week["text"]
        assert week["theme"]
        from fengsuan.app.api.v1.labs import _public_brief

        pub = _public_brief(d.brief_json)
        assert "theme_members" not in pub and pub["themes"][0]["reps"]
    finally:
        db.close()


def test_survey_tool_caps_at_forty(client):
    lab = _lab(client, "scrd4")
    db = SessionLocal()
    try:
        uid = _uid(db)
        for n in range(45):
            db.add(LiteratureItem(lab_id=lab, title=f"Watermark method number {n}", abstract="robust watermark for print camera", added_by=uid))
        db.commit()
        assert stub_plan("lab", "总结一下 watermark 的主要方法")[0] == "get_survey_digests"
        out = agent_answer(db, user_id=uid, lab_id=lab, scope="lab", question="总结一下 watermark 的主要方法", feature="ask")
        step = out["steps"][0]
        assert step["tool"] == "get_survey_digests"
        assert "只看了最相关的 40 篇" in step["result_summary"]
        assert len(out["sources"]) <= 24
    finally:
        db.close()
