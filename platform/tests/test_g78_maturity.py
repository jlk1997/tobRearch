from __future__ import annotations

from fengsuan.app.api.v1.collab import ground_llm_actions
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import UsageLedger
from fengsuan.app.services.llm import chat
from fengsuan.app.services.prompts import PROMPT_DIR, load_prompt


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


def test_g78_ground_llm_actions_must_be_substring():
    notes = "讨论进度\n行动: 读一篇论文\n闲聊"
    grounded, ungrounded = ground_llm_actions(notes, "读一篇论文\n幻觉任务XYZ")
    assert any(t == "读一篇论文" for _, t in grounded)
    assert "幻觉任务XYZ" in ungrounded
    assert all("幻觉" not in t for _, t in grounded)


def test_g78_minutes_drops_ungrounded_and_keeps_note_actions(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g78ug")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "stub_complete", lambda messages, json_mode=False: "幻觉任务XYZ\n写摘要")
    notes = "行动: 写摘要\n讨论进度"
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": notes}, headers=h)
    assert m.status_code == 200, m.text
    out = client.post(f"/api/v1/meetings/{m.json()['id']}/minutes", json={"notes_raw": notes}, headers=h)
    assert out.status_code == 200, out.text
    body = out.json()
    titles = [a["title"] for a in body.get("actions") or []]
    assert titles == ["写摘要"]
    assert any((x.get("title") if isinstance(x, dict) else x) == "幻觉任务XYZ" for x in body.get("ungrounded") or [])
    detail = client.get(f"/api/v1/meetings/{m.json()['id']}", headers=h)
    assert detail.status_code == 200
    assert "幻觉任务XYZ" in (detail.json().get("ungrounded") or [])


def test_g78_minutes_llm_error_still_extracts_notes(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g78err")
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "llm_stub_enabled", lambda: False)
    monkeypatch.setattr(llm_mod, "assert_lab_quota", lambda *a, **k: None)
    monkeypatch.setattr(
        llm_mod,
        "resolve_llm",
        lambda *a, **k: llm_mod.ResolvedLLM("https://example.invalid/v1", "k", "m", "env"),
    )
    monkeypatch.setattr(
        llm_mod,
        "_complete_chat",
        lambda *a, **k: (_ for _ in ()).throw(ClassifiedHTTPException(400, "模型拒绝", "llm")),
    )
    notes = "行动: 读一篇论文"
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": notes}, headers=h).json()
    out = client.post(f"/api/v1/meetings/{m['id']}/minutes", json={"notes_raw": notes}, headers=h)
    assert out.status_code == 200, out.text
    assert [a["title"] for a in out.json().get("actions") or []] == ["读一篇论文"]
    assert out.json().get("llm_error")


def test_g79_prompts_are_readonly_markdown():
    assert (PROMPT_DIR / "tutor.md").is_file()
    assert (PROMPT_DIR / "method_card.md").is_file()
    assert (PROMPT_DIR / "meeting_minutes.md").is_file()
    assert "苏格拉底" in load_prompt("tutor")
    assert "method_card" in load_prompt("method_card")
    polished = load_prompt("language_polish", polish_level="academic", venue_style_notes="无")
    assert "academic" in polished
    assert "{polish_level}" not in polished


def test_g80_failed_chat_is_ledgered_once(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g80fail")
    me = client.get("/api/v1/auth/me", headers=h).json()
    from fengsuan.app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod, "llm_stub_enabled", lambda: False)
    monkeypatch.setattr(llm_mod, "assert_lab_quota", lambda *a, **k: None)
    monkeypatch.setattr(
        llm_mod,
        "resolve_llm",
        lambda *a, **k: llm_mod.ResolvedLLM("https://example.invalid/v1", "k", "m", "env"),
    )
    n = {"i": 0}

    def boom(_llm, _messages, _json_mode):
        n["i"] += 1
        raise ClassifiedHTTPException(400, "模型拒绝", "llm")

    monkeypatch.setattr(llm_mod, "_complete_chat", boom)
    db = SessionLocal()
    try:
        before = db.query(UsageLedger).filter(UsageLedger.lab_id == lab, UsageLedger.credential_scope == "failed").count()
        try:
            chat(db, user_id=me["id"], lab_id=lab, messages=[{"role": "user", "content": "hi"}], feature="tutor")
            raise AssertionError("should fail")
        except ClassifiedHTTPException:
            pass
        db.commit()
        after = db.query(UsageLedger).filter(UsageLedger.lab_id == lab, UsageLedger.credential_scope == "failed").count()
    finally:
        db.close()
    assert n["i"] == 1
    assert after == before + 1
