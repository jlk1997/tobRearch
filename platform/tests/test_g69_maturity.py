from __future__ import annotations

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.services.analysis import empty_method_card, extract_json_object, merge_method_cards, page_batches
from fengsuan.app.services.llm import chat, is_transient_llm_error, strip_model_reasoning


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


def _paper(client, h, lab: str, title: str, abstract: str = "") -> dict:
    r = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": title, "abstract": abstract},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _sse_last(text: str) -> dict:
    last = {}
    for block in (text or "").split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            import json

            last = json.loads(raw)
    return last


def test_g69_tutor_history_exposes_ungrounded(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g69ug")
    paper = _paper(client, h, lab, "Ungrounded tutor paper", "Only qualitative discussion, no numbers.")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "stub_complete", lambda messages, json_mode=False: "作者报告准确率达到 99.7%。")
    r = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "准确率是多少？", "session_id": "s-g69"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ungrounded") is True
    assert body.get("next_question")
    assert body.get("session_id") == "s-g69"
    detail = client.get(
        f"/api/v1/literature/{paper['id']}/detail",
        params={"session": "s-g69", "include": "tutor"},
        headers=h,
    )
    assert detail.status_code == 200, detail.text
    items = (detail.json().get("tutor_history") or {}).get("items") or []
    assert items
    assert items[0].get("ungrounded") is True
    assert items[0].get("next_question")


def test_g69_tutor_regenerate_same_session(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g69rg")
    paper = _paper(client, h, lab, "Regenerate tutor", "Convolution is used.")
    q = "这一页的方法在做什么？"
    first = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": q, "session_id": "s-rg"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": q, "session_id": "s-rg"},
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert first.json().get("session_id") == second.json().get("session_id") == "s-rg"
    hist = client.get(
        f"/api/v1/literature/{paper['id']}/detail",
        params={"session": "s-rg", "include": "tutor"},
        headers=h,
    )
    assert hist.status_code == 200
    items = (hist.json().get("tutor_history") or {}).get("items") or []
    assert len(items) >= 2
    assert all(t.get("session_id") == "s-rg" for t in items)


def test_g70_transient_retry_and_chat(client, monkeypatch):
    assert is_transient_llm_error(ClassifiedHTTPException(502, "模型请求超时，请稍后重试", "llm"))
    assert not is_transient_llm_error(ClassifiedHTTPException(429, "额度", "quota"))
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g70retry")
    from fengsuan.app.services import llm as llm_mod

    n = {"i": 0}

    def boom(_llm, _messages, _json_mode):
        n["i"] += 1
        if n["i"] == 1:
            raise ClassifiedHTTPException(502, "模型请求超时，请稍后重试", "llm")
        return "recovered after retry", {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(llm_mod, "llm_stub_enabled", lambda: False)
    monkeypatch.setattr(llm_mod, "assert_lab_quota", lambda *a, **k: None)
    monkeypatch.setattr(
        llm_mod,
        "resolve_llm",
        lambda *a, **k: llm_mod.ResolvedLLM("https://example.invalid/v1", "k", "m", "env"),
    )
    monkeypatch.setattr(llm_mod, "assert_llm_base_url", lambda url, kind=None: url)
    monkeypatch.setattr(llm_mod, "_complete_chat", boom)
    monkeypatch.setattr(llm_mod, "_ledger", lambda *a, **k: None)
    from fengsuan.app.db.session import SessionLocal

    db = SessionLocal()
    try:
        out = chat(db, user_id="admin", lab_id=lab, messages=[{"role": "user", "content": "hi"}], feature="tutor")
    finally:
        db.close()
    assert out == "recovered after retry"
    assert n["i"] == 2


def test_g70_tutor_and_translate_stream(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g70sse")
    paper = _paper(client, h, lab, "Stream paper", "Page text for tutoring.")
    tutor = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "这一页在讲什么？", "session_id": "s-sse", "stream": True},
        headers=h,
    )
    assert tutor.status_code == 200, tutor.text
    assert "text/event-stream" in tutor.headers.get("content-type", "")
    assert "data:" in tutor.text
    last = _sse_last(tutor.text)
    assert last.get("done") is True
    assert last.get("answer")
    assert "<think>" not in (last.get("answer") or "")
    tr = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "Convolution residual network.", "target": "zh", "stream": True},
        headers=h,
    )
    assert tr.status_code == 200, tr.text
    assert "text/event-stream" in tr.headers.get("content-type", "")
    payload = _sse_last(tr.text)
    assert payload.get("done") is True
    assert payload.get("text")
    assert payload.get("reasoning_stripped") is True


def test_g70_detail_include_page_text(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g70inc")
    paper = _paper(client, h, lab, "Slim detail", "Abstract only body.")
    slim = client.get(
        f"/api/v1/literature/{paper['id']}/detail",
        params={"page": 1, "include": "page_text"},
        headers=h,
    )
    assert slim.status_code == 200, slim.text
    data = slim.json()
    assert "page_text" in data
    assert "tutor_history" not in data
    assert "notes" not in data


def test_g71_write_section_rewrite(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g71rw")
    first = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "先写一稿"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    draft = first.json().get("text") or ""
    second = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "", "rewrite": True, "prior_draft": draft},
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert second.json().get("text")


def test_g72_bad_json_not_dumped(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g72bad")
    paper = _paper(client, h, lab, "Bad json card", "A long abstract that should not be dumped as problem.")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "stub_complete", lambda messages, json_mode=False: "这不是JSON <think>hidden</think> 垃圾字段")
    r = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 200, r.text
    result = r.json().get("result") or {}
    problem = result.get("problem") if isinstance(result.get("problem"), dict) else {}
    assert "垃圾字段" not in str(problem.get("text") or "")
    assert "这不是JSON" not in str(problem.get("text") or "")
    assert result.get("source") in {"parse_failed", "abstract_only"}
    assert result.get("warning")
    empty = r.json().get("card_empty") or {}
    assert empty.get("kind")


def test_g72_page_batches_and_merge():
    pages = ["a" * 8000, "b" * 8000, "c" * 100]
    batches = page_batches(pages, max_chars=12000)
    assert len(batches) == 2
    assert extract_json_object("nope") is None
    assert extract_json_object('{"problem":"x"}') == {"problem": "x"}
    merged = merge_method_cards(
        [
            {"problem": {"text": "p1", "ungrounded": True}, "methods": {"text": "m1"}, "source": "pdf"},
            {"problem": {"text": "p2"}, "methods": {"text": "m2", "ungrounded": True}, "claims": [{"text": "c"}]},
        ]
    )
    assert merged.get("problem", {}).get("text") == "p2"
    assert merged.get("chunks") == 2
    empty = empty_method_card(source="parse_failed", warning="x")
    assert empty["problem"]["ungrounded"] is True
    cleaned, stripped = strip_model_reasoning("<think>x</think>可见")
    assert cleaned == "可见"
    assert stripped is True
