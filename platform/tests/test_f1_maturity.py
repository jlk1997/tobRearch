from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openlens_ai.utils.literature_bib import render_literature_review_bib, write_literature_review_bib
from openlens_ai.utils.literature_coverage import literature_search_should_stop, unique_paper_count
from openlens_ai.utils.paper_dedupe import dedupe_paper_dicts, paper_identity_key
from openlens_ai.utils.search_adapter import dedupe_search_hits

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.research_engine import (
    auto_resume_gate_allowed,
    literature_bib_gate,
    prepare_engine_cfg_context,
    research_output_gate,
)
from fengsuan.app.services.writing import core_literature_coverage, is_related_work_heading


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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f1_situation_and_ai_hidden_from_peers(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f1sit")
    sh = _invite_student(client, h, lab, "f1stu@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    student_id = me.json()["id"]
    client.post(f"/api/v1/labs/{lab}/kb/documents", files={"file": ("a.txt", b"AlphaOnlyToken methods.", "text/plain")}, headers=h)
    client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "AlphaOnlyToken"}, headers=h)
    client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "AlphaOnlyToken"}, headers=sh)
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    body = st.json()
    assert body.get("situation", {}).get("lines")
    keys = {x["key"] for x in body["situation"]["lines"]}
    assert {"people", "direction", "time", "honesty", "ai", "risk"} <= keys
    assert body.get("ai", {}).get("peer_usage_hidden") is False
    stu = client.get(f"/api/v1/labs/{lab}/stats", headers=sh)
    assert stu.status_code == 200, stu.text
    sai = stu.json().get("ai") or {}
    assert sai.get("peer_usage_hidden") is True
    for row in sai.get("usage_by_member") or []:
        assert row["user_id"] == student_id
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "situation" in csv.text
    assert "ai" in csv.text
    assert "this_week" in csv.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert cockpit.json().get("situation", {}).get("lines")
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    assert digest.json().get("ai_brief_md")
    assert "模型概述" in digest.json()["ai_brief_md"]


def test_f2_jump_search_href_and_library_stamp(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f2jmp")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Unique Jump Paper", "doi": "10.1000/f2jmp", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    miss = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "zzzz-no-such-object"}, headers=h)
    assert miss.status_code == 200, miss.text
    assert miss.json().get("items") == []
    assert "tab=search" in (miss.json().get("search_href") or "")
    assert "zzzz-no-such-object" in (miss.json().get("search_href") or "")
    hit = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Unique Jump"}, headers=h)
    assert hit.status_code == 200, hit.text
    assert any(x.get("type") == "literature" for x in hit.json().get("items") or [])
    search = client.post(
        f"/api/v1/labs/{lab}/literature/search",
        json={"q": "Unique Jump Paper", "sources": ["lab"]},
        headers=h,
    )
    assert search.status_code == 200, search.text
    items = search.json().get("items") or []
    assert items
    assert any(x.get("already_in_library") for x in items)


def test_f3_related_work_and_core_claims(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f3rw")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图网络", "keywords": ["gnn"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Core Graph Network Paper", "abstract": "method", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "我的稿", "kind": "conference", "content_md": "## 引言\n还没有点名。", "direction_ids": [did]},
        headers=h,
    ).json()
    claims = client.get(f"/api/v1/manuscripts/{ms['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    body = claims.json()
    assert body.get("core_total", 0) >= 1
    assert body.get("core_missing_count", 0) >= 1
    sec = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "", "literature_item_ids": [paper["id"]], "manuscript_id": ms["id"]},
        headers=h,
    )
    assert sec.status_code == 200, sec.text
    assert sec.json().get("related_work") is True
    assert is_related_work_heading("Related Works")
    db = SessionLocal()
    try:
        from fengsuan.app.models.manuscript import Manuscript

        row = db.get(Manuscript, ms["id"])
        assert row is not None
        cov = core_literature_coverage(db, row)
        assert cov["core_total"] >= 1
    finally:
        db.close()


def test_f4_dedupe_bib_and_gate(tmp_path):
    rows = [
        {"title": "Same Paper", "doi": "10.1/x", "arxiv_id": "2301.00001"},
        {"title": "Same Paper Copy", "arxiv_id": "2301.00001"},
        {"title": "Other", "paper_id": "2301.99999", "source": "arxiv"},
    ]
    out = dedupe_paper_dicts(rows)
    assert len(out) == 2
    assert paper_identity_key({"arxiv_id": "arxiv:2301.00001v2"}).startswith("arxiv:")
    shared = dedupe_search_hits(rows)
    assert len(shared) == 2
    bib = render_literature_review_bib([{"title": "Must Cite Graph Paper", "authors": ["Ada"], "year": 2020, "doi": "10.1/y"}])
    assert "Must Cite Graph Paper" in bib
    path = write_literature_review_bib(tmp_path, [{"title": "Must Cite Graph Paper", "authors": ["Ada"], "year": 2020}])
    assert path.name == "literature_review.bib"
    ws = tmp_path / "workspace"
    (ws / "literature_review.md").write_text("综述 Must Cite Graph Paper。", encoding="utf-8")
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    assert literature_bib_gate(tmp_path, cfg, run) == ""
    (ws / "literature_review.bib").write_text("@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n", encoding="utf-8")
    assert "没写到" in literature_bib_gate(tmp_path, cfg, run)
    (ws / "literature_review.bib").write_text("", encoding="utf-8")
    (tmp_path / "plan.md").write_text("计划 Must Cite Graph Paper", encoding="utf-8")
    msg = research_output_gate(cfg, run, {"literature": [{"title": "Must Cite Graph Paper"}]})
    assert "literature_review.bib" in msg


def test_f5_coverage_stop_is_cap():
    cfg = SimpleNamespace(workflow=SimpleNamespace(literature_search_min_tool_call=10))
    state = {"literature_tool_call_counter": 1, "messages": []}
    assert literature_search_should_stop(state, cfg) is False
    state = {
        "literature_tool_call_counter": 10,
        "messages": [SimpleNamespace(content='{"title": "A long enough paper title here"}')],
    }
    assert literature_search_should_stop(state, cfg) is True
    rich = {
        "literature_tool_call_counter": 3,
        "messages": [
            SimpleNamespace(
                content='[{"title": "Paper Alpha Title Here"},{"title": "Paper Beta Title Here"},{"title": "Paper Gamma Title Here","doi":"10.1000/abc"}]'
            )
        ],
        "lab_context": {"literature": []},
    }
    assert unique_paper_count(rich) >= 3
    assert literature_search_should_stop(rich, cfg) is False
    rich["messages"] = [
        SimpleNamespace(
            content='[{"title": "Paper Alpha Title Here","abstract":"'
            + ("Graph convolution abstract text for coverage. " * 4)
            + '"},{"title": "Paper Beta Title Here"},{"title": "Paper Gamma Title Here","doi":"10.1000/abc"}]'
        )
    ]
    assert literature_search_should_stop(rich, cfg) is True


def test_literature_evidence_sidecar_and_search_ledger(tmp_path):
    from openlens_ai.tools.paper_search_tool import papers_to_dicts
    from openlens_ai.utils.job_context import bind_job_save_path
    from openlens_ai.utils.literature_bib import persist_search_hits, write_literature_evidence

    bind_job_save_path(str(tmp_path))
    persist_search_hits([{"title": "Old Paper Title Here", "doi": "10.1000/old", "abstract": "A" * 90}])

    class _Hit:
        def to_dict(self):
            return {"title": "Old Paper Title Here", "doi": "10.1000/old", "abstract": "A" * 90}

    out = papers_to_dicts([_Hit()])
    assert out[0].get("_search_ledger") is True
    assert int(out[0].get("duplicates_skipped") or 0) >= 1
    path = write_literature_evidence(tmp_path)
    assert path is not None and path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Old Paper Title Here" in text
    assert "尚无检索账本" not in text
    assert "有摘要" in text
    assert "已读全文" not in text
    (tmp_path / "downloads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "downloads" / "10.1000_old.pdf").write_bytes(b"%PDF-1.4")
    path = write_literature_evidence(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "已读全文" in text
    from openlens_ai.utils.job_context import inject_job_context

    filled = inject_job_context("Ledger:\n{literature_evidence}", SimpleNamespace(save_path=str(tmp_path)))
    assert "Old Paper Title Here" in filled
    assert "{literature_evidence}" not in filled
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "actual_metrics.json").write_text('{"acc": 0.81}', encoding="utf-8")
    metrics = inject_job_context("M:\n{actual_metrics}", SimpleNamespace(save_path=str(tmp_path)))
    assert "0.81" in metrics
    assert "{actual_metrics}" not in metrics
    empty_m = inject_job_context("M:\n{actual_metrics}", SimpleNamespace(save_path=str(tmp_path / "missing")))
    assert "不得编造实测指标" in empty_m


def test_search_query_replay_skips_external_and_failed_fetch_retries(tmp_path):
    from openlens_ai.tools.paper_search_tool import run_search
    from openlens_ai.utils.job_context import bind_job_save_path
    from openlens_ai.utils.literature_bib import persist_search_hits, remember_search_query

    bind_job_save_path(str(tmp_path))
    try:
        persist_search_hits([{"title": "Old Paper Title Here", "doi": "10.1000/old", "abstract": "A" * 90}])
        remember_search_query("dummy", "graph convolution")
        n = {"calls": 0}

        def boom():
            n["calls"] += 1
            raise AssertionError("should not fetch")

        replay = run_search("dummy", "graph convolution", boom)
        assert n["calls"] == 0
        assert replay[0].get("already_searched") is True
        assert any(r.get("doi") == "10.1000/old" for r in replay)

        def fail_once():
            raise RuntimeError("network")

        try:
            run_search("dummy", "fresh query", fail_once)
        except RuntimeError:
            pass

        class _Hit:
            def to_dict(self):
                return {"title": "New Paper Title Here", "doi": "10.1000/new", "abstract": "B" * 90}

        later = {"calls": 0}

        def ok():
            later["calls"] += 1
            return [_Hit()]

        out = run_search("dummy", "fresh query", ok)
        assert later["calls"] == 1
        assert int(out[0].get("new") or 0) >= 1
    finally:
        bind_job_save_path("")


def test_auto_resume_gate_allowed_once_when_hits_exist():
    assert auto_resume_gate_allowed({}, {"search_hit_count": 3}) is True
    assert auto_resume_gate_allowed({"auto_resumed_gate": True}, {"search_hit_count": 3}) is False
    assert auto_resume_gate_allowed({}, {"search_hit_count": 0}) is False
    assert auto_resume_gate_allowed(None, None) is False


def test_cached_search_sources_replays_within_ttl(tmp_path, monkeypatch):
    from fengsuan.app.core.config import get_settings

    monkeypatch.setenv("FILE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    n = {"calls": 0}

    def fake(query, sources, max_per_source=8, language=None):
        n["calls"] += 1
        return [{"title": "Ext Paper Title Here", "doi": "10.1000/ext"}]

    monkeypatch.setattr("fengsuan.app.services.literature.search_sources", fake)
    from fengsuan.app.services.literature import cached_search_sources

    try:
        first, cached_first = cached_search_sources("graph convolution", ["arxiv"])
        second, cached_second = cached_search_sources("graph convolution", ["arxiv"])
        assert n["calls"] == 1
        assert cached_first is False
        assert cached_second is True
        assert first[0]["doi"] == "10.1000/ext"
        assert second[0]["doi"] == "10.1000/ext"
    finally:
        get_settings.cache_clear()


def test_prepare_engine_cfg_enables_authenticated_context():
    cfg = SimpleNamespace(context=SimpleNamespace(authenticated_context=False))
    prepare_engine_cfg_context(cfg)
    assert cfg.context.authenticated_context is True
    bare = SimpleNamespace()
    prepare_engine_cfg_context(bare)
    assert not hasattr(bare, "context")
