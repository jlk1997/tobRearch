from __future__ import annotations

from fengsuan.app.services.language import (
    extract_translation_body,
    join_pdf_text_items,
    restore_latin_spacing,
    strict_translate_retry_messages,
    translation_looks_like_instructions,
)


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


def _it(s: str, x: float, y: float, h: float = 10.0) -> dict:
    return {"str": s, "transform": [1, 0, 0, 1, x, y], "width": 40, "height": h}


def test_g302_join_dehyphenates_and_drops_footer():
    same_line = join_pdf_text_items([_it("method-", 10, 400), _it("ology", 80, 400)])
    assert "methodology" in same_line
    assert "method-ology" not in same_line
    broken = join_pdf_text_items(
        [
            _it("cross-modal method-", 10, 420),
            _it("ology designed", 10, 404),
            _it("for tasks.", 10, 388),
        ]
    )
    assert "methodology" in broken
    assert "method-ology" not in broken
    page = join_pdf_text_items(
        [
            _it("A convolutional method", 40, 500),
            _it("Copyright 2024 IEEE. All rights reserved. Authorized licensed use.", 40, 12),
        ]
    )
    assert "convolutional" in page
    assert "IEEE" not in page
    assert "Copyright" not in page
    assert join_pdf_text_items([]) == ""
    assert join_pdf_text_items([_it("   ", 10, 10)]) == ""


def test_g302_join_two_column_keeps_left_then_right():
    items = []
    for i, word in enumerate(["alpha", "beta", "gamma", "delta", "LEFTEND"]):
        items.append(_it(word, 20, 800 - i * 20))
    for i, word in enumerate(["RIGHTSTART", "epsilon", "zeta", "eta", "theta", "iota", "kappa"]):
        items.append(_it(word, 340, 800 - i * 20))
    text = join_pdf_text_items(items)
    assert "LEFTEND" in text and "RIGHTSTART" in text
    assert text.find("LEFTEND") < text.find("RIGHTSTART")
    between = text[text.find("LEFTEND") : text.find("RIGHTSTART")]
    assert "\n" in between


def test_g302_extracts_chinese_from_instruction_dump():
    mixed = (
        "1. Only translate the main text given by the user\n"
        "2. Only output the translated text, no explanations\n"
        "3. Do not translate formulas or citation keys\n"
        "4. Follow the glossary\n"
        "Let me translate this carefully, maintaining the academic tone.\n"
        "The text includes: - Title: ESOD Efficient Small Object Detection\n"
        "Let me work on the translation:\n"
        "Title: ESOD：高效小目标检测\n"
        "Authors: Kai Liu, Zhehang Fu, Peng Jin\n"
        "标题：面向高分辨率图像的高效小目标检测。作者：Kai Liu。\n"
        "摘要：放大输入图像是促进小目标检测的一种直接有效的方法。"
    )
    body = extract_translation_body(mixed, "zh")
    assert "Let me translate" not in body
    assert "Only output" not in body
    assert "Authors:" not in body
    assert "高效小目标检测" in body
    assert "放大输入图像" in body
    glued = extract_translation_body(
        "1. Only translate the main text Let me translate this carefully 标题：高效小目标检测。",
        "zh",
    )
    assert "Let me translate" not in glued
    assert "高效小目标" in glued
    assert restore_latin_spacing("ESOD:EfficientSmall Object") == "ESOD: Efficient Small Object"
    retry = strict_translate_retry_messages(
        [
            {"role": "system", "content": "old"},
            {"role": "user", "content": "【前后文，不要译这段】ctx\n【下面才是要处理的正文】We propose ESOD."},
        ],
        "zh",
    )
    assert retry[0]["role"] == "system"
    assert "第一个字必须是zh" in retry[0]["content"]
    assert retry[1]["content"] == "We propose ESOD."


def test_g302_instruction_dump_is_not_translation():
    assert translation_looks_like_instructions(
        "Carefully translate the following text. Do not translate formulas.",
        "zh",
    )
    assert translation_looks_like_instructions(
        "Key points: follow the glossary. The translation should stay formal.",
        "zh",
    )
    assert not translation_looks_like_instructions("We propose a convolutional method.", "zh")
    assert not translation_looks_like_instructions("我们提出一种卷积方法。", "zh")


def test_g302_translate_rejects_instruction_dump(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g302tr")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: (
            "Carefully translate the following. Do not translate formulas. Key points: keep terms."
        ),
    )
    bad = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "We propose a convolutional method.", "target": "zh"},
        headers=h,
    )
    assert bad.status_code == 400, bad.text
    body = bad.json()
    assert "译文" in (body.get("detail") or "")
    assert body.get("error_class") == "llm"

    empty = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "   ", "target": "zh"},
        headers=h,
    )
    assert empty.status_code == 400, empty.text
    assert empty.json().get("error_class") == "parse"


def test_g302_translate_salvages_mixed_dump(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g302sv")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: (
            "1. Only translate the main text\n"
            "Let me translate this carefully.\n\n"
            "我们提出一种面向高分辨率图像的高效小目标检测方法。"
        ),
    )
    r = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "We propose ESOD for high-resolution images.", "target": "zh"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    text = r.json().get("text") or ""
    assert "高效小目标" in text
    assert "Let me translate" not in text
    assert "Only translate" not in text


def test_g302_translate_retries_after_empty_dump(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g302rt")
    from fengsuan.app.services import llm as llm_mod

    calls = {"n": 0}

    def _stub(messages, json_mode=False):
        calls["n"] += 1
        sys = ""
        for m in messages:
            if m.get("role") == "system":
                sys += m.get("content") or ""
        if "第一个字必须是" in sys:
            return "我们提出一种卷积方法。"
        return "1. Only translate the main text\nLet me translate this carefully."

    monkeypatch.setattr(llm_mod, "stub_complete", _stub)
    r = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "We propose a convolutional method.", "target": "zh"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert "卷积" in (r.json().get("text") or "")
    assert "Let me translate" not in (r.json().get("text") or "")
    assert calls["n"] == 2


def test_g302_stream_hides_instruction_preamble(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g302sse")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "stub_complete",
        lambda messages, json_mode=False: (
            "1. Only translate the main text\n"
            "Let me translate this carefully.\n"
            "标题：面向高分辨率图像的高效小目标检测。"
        ),
    )
    r = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "ESOD: Efficient Small Object Detection", "target": "zh", "stream": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers.get("content-type", "")
    last = {}
    for block in (r.text or "").split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            import json

            ev = json.loads(raw)
            if ev.get("replace") and ev.get("text"):
                assert "Let me translate" not in ev["text"]
                assert "Only translate" not in ev["text"]
            last = ev
    assert last.get("done") is True
    assert "高效小目标" in (last.get("text") or "")


def test_g302_home_arrays_and_contracts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g302home")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Reader Translate Paper", "direction_ids": [did]},
        headers=h,
    ).json()
    assert lit["id"]

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    steps = home.json().get("next_steps") or []
    reading = next((s for s in steps if s.get("key") == "reading" or s.get("label") == "开始阅读"), None)
    href = (reading or {}).get("href") or ""
    if href:
        assert href.startswith(f"/labs/{lab}/literature")
        assert "unread" not in href
        if "direction_id=" in href:
            assert f"direction_id={did}" in href

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    stuck = stats.json().get("stuck_brief") or {}
    text = stuck.get("text") or ""
    assert "方法卡" not in text
    if stuck.get("no_card"):
        assert "还没写方法" in text

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)

    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)

    gloss = client.get(f"/api/v1/labs/{lab}/glossary", params={"direction_id": did}, headers=h)
    assert gloss.status_code == 200, gloss.text
    assert isinstance(gloss.json(), list)

    unused = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert unused.status_code == 200, unused.text
    pack = unused.json()
    assert isinstance(pack, dict)
    mutex = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "status": "pending"},
        headers=h,
    )
    assert mutex.status_code == 200, mutex.text
    mutex_body = mutex.json()
    assert isinstance(mutex_body, dict)
    assert mutex_body.get("items") == []
    nxt = mutex_body.get("empty_next") or {}
    blob = f"{nxt.get('title') or ''}{nxt.get('text') or ''}"
    assert "综述没写到" in blob

    wrapped = client.get(
        f"/api/v1/labs/{lab}/reproduce-runs",
        params={"literature_item_id": lit["id"]},
        headers=h,
    )
    assert wrapped.status_code == 200, wrapped.text
    assert isinstance(wrapped.json(), dict)

    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", params={"direction_id": did}, headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert cockpit.json().get("events_scope") == "lab"
