"""阅读能力：结构导航、锚定高亮、自动预读、页译文持久化。

对应 AGENT_ERA_PLAN 工作流 B2。这四项都是「打开就已就绪」而不是「点一次等一次」。
"""

from __future__ import annotations

from fengsuan.app.db.session import SessionLocal

PAGES = [
    "1 Introduction\nWe study retrieval augmented generation for lab notebooks.",
    "2 Method\nWe chunk pages with 15 percent overlap and rerank with a cross encoder.",
    "3 Experiments\nRecall at 8 reaches 0.86 on the internal set.",
]
STRUCTURE = {
    "sections": [
        {"title": "1 Introduction", "page": 1, "level": 1},
        {"title": "2 Method", "page": 2, "level": 1},
        {"title": "3 Experiments", "page": 3, "level": 1},
    ],
    "figures": [{"label": "Figure 1", "page": 2, "caption": "Pipeline overview"}],
}


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _paper(client, h, lab_id: str, *, with_structure: bool = True) -> str:
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "RAG for lab notebooks", "abstract": "Retrieval augmented generation.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    item_id = item.json()["id"]
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureFile
        from fengsuan.app.services.pagecache import store_pages, store_structure

        f = LiteratureFile(
            item_id=item_id,
            file_path=f"/tmp/{item_id}.pdf",
            filename="rag.pdf",
            mime="application/pdf",
            size=4096,
            parse_status="ok",
        )
        db.add(f)
        db.flush()
        store_pages(db, f, PAGES)
        if with_structure:
            store_structure(f, STRUCTURE)
        db.commit()
    finally:
        db.close()
    return item_id


def test_structure_tree_drives_section_navigation(client):
    h = _h(_admin(client))
    lab = _lab(client, h, "capstruct")
    item_id = _paper(client, h, lab)
    r = client.get(f"/api/v1/literature/{item_id}/structure", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_structure"] is True
    assert not body["next_step"]
    titles = [s["title"] for s in body["sections"]]
    assert titles == ["1 Introduction", "2 Method", "3 Experiments"]
    assert all(1 <= s["page"] <= len(PAGES) for s in body["sections"]), "章节页码要能跳到真实页"
    assert body["figures"][0]["page"] == 2


def test_structure_absent_says_so_instead_of_faking(client):
    h = _h(_admin(client))
    lab = _lab(client, h, "capnostruct")
    item_id = _paper(client, h, lab, with_structure=False)
    r = client.get(f"/api/v1/literature/{item_id}/structure", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_structure"] is False
    assert body["sections"] == []
    assert "还没有章节结构" in body["next_step"]


def test_anchored_highlight_round_trips_and_rejects_fake_quotes(client):
    h = _h(_admin(client))
    lab = _lab(client, h, "capanchor")
    item_id = _paper(client, h, lab)
    anchor = {
        "page_no": 2,
        "quote": "rerank with a cross encoder",
        "rects": [{"x": 0.1, "y": 0.2, "w": 0.4, "h": 0.03}],
    }
    r = client.post(
        f"/api/v1/literature/{item_id}/notes",
        json={"content_md": "这句是检索重排", "page_no": 2, "anchor": anchor},
        headers=h,
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["anchor"]["page_no"] == 2
    assert got["anchor"]["quote"] == "rerank with a cross encoder"
    assert got["anchor"]["rects"][0]["w"] == 0.4

    listed = client.get(f"/api/v1/literature/{item_id}/notes", headers=h)
    assert listed.status_code == 200
    assert listed.json()[0]["anchor"]["rects"], "高亮层要能从列表接口拿到矩形"

    fake = client.post(
        f"/api/v1/literature/{item_id}/notes",
        json={
            "content_md": "编的",
            "page_no": 2,
            "anchor": {"page_no": 2, "quote": "quantum teleportation of grant money", "rects": [{"x": 0, "y": 0, "w": 0.5, "h": 0.02}]},
        },
        headers=h,
    )
    assert fake.status_code == 200, fake.text
    assert fake.json()["anchor"]["quote"] == "", "不在该页的原文子串必须被清掉"

    bad_rects = client.post(
        f"/api/v1/literature/{item_id}/notes",
        json={"content_md": "越界", "page_no": 2, "anchor": {"page_no": 2, "quote": "", "rects": [{"x": 9, "y": 9, "w": 9, "h": 9}]}},
        headers=h,
    )
    assert bad_rects.status_code == 200, bad_rects.text
    assert bad_rects.json().get("anchor") is None, "越界矩形不入库"


def test_page_translation_is_cached_server_side(client):
    h = _h(_admin(client))
    lab = _lab(client, h, "captrans")
    item_id = _paper(client, h, lab)
    miss = client.get(f"/api/v1/literature/{item_id}/page-translation", params={"page": 2}, headers=h)
    assert miss.status_code == 200, miss.text
    assert miss.json()["hit"] is False
    fingerprint = miss.json()["text_hash"]
    assert fingerprint

    put = client.put(
        f"/api/v1/literature/{item_id}/page-translation",
        json={"page_no": 2, "lang": "zh", "text": "我们按 15% 重叠切块，并用交叉编码器重排。"},
        headers=h,
    )
    assert put.status_code == 200, put.text
    assert put.json()["text_hash"] == fingerprint

    hit = client.get(f"/api/v1/literature/{item_id}/page-translation", params={"page": 2}, headers=h)
    assert hit.status_code == 200
    assert hit.json()["hit"] is True
    assert "交叉编码器" in hit.json()["text"]

    # 原文重抽之后指纹变了，旧译文必须失效，不许拿旧译文冒充。
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureFile
        from fengsuan.app.services.pagecache import store_pages

        f = db.query(LiteratureFile).filter(LiteratureFile.item_id == item_id).first()
        store_pages(db, f, [PAGES[0], "2 Method\nCompletely different text now.", PAGES[2]])
        db.commit()
    finally:
        db.close()
    stale = client.get(f"/api/v1/literature/{item_id}/page-translation", params={"page": 2}, headers=h)
    assert stale.status_code == 200
    assert stale.json()["hit"] is False
    assert stale.json()["text_hash"] != fingerprint


def test_preread_generates_method_card_in_background(client):
    """入库后后台先把方法卡做好，学生打开时不必点一次等一次。"""
    h = _h(_admin(client))
    lab = _lab(client, h, "cappreread")
    item_id = _paper(client, h, lab)
    db = SessionLocal()
    try:
        from fengsuan.app.services.analysis import load_best_method_card
        from fengsuan.app.services.worker import _preread_items

        _row, before = load_best_method_card(db, item_id)
        assert not before
        done = _preread_items(db, limit=3)
        assert item_id in done, done
        _row2, after = load_best_method_card(db, item_id)
        assert after, "预读后应当已有方法卡"
        # 再跑一轮不应重复烧配额。
        assert item_id not in _preread_items(db, limit=3)
    finally:
        db.close()


def test_page_translation_double_write_keeps_one_row(client):
    """同一页同一语言并发写入应落到一行，第二次是更新。"""
    h = _h(_admin(client))
    lab = _lab(client, h, "captr2")
    item_id = _paper(client, h, lab)
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteraturePageTranslation
        from fengsuan.app.services.pagecache import page_fingerprint, write_page_translation

        fp = page_fingerprint("same")
        write_page_translation(db, item_id, 2, "zh", "第一稿", fingerprint=fp)
        write_page_translation(db, item_id, 2, "zh", "第二稿", fingerprint=fp)
        db.commit()
        rows = (
            db.query(LiteraturePageTranslation)
            .filter(
                LiteraturePageTranslation.item_id == item_id,
                LiteraturePageTranslation.page_no == 2,
                LiteraturePageTranslation.lang == "zh",
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].text == "第二稿"
    finally:
        db.close()
