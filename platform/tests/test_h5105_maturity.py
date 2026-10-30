from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import (
    compose_injected_question,
    execute_research_run,
    research_output_gate,
    unused_injected_titles,
)
from openlens_ai.utils.job_context import inject_job_context, prepend_lab_context
from openlens_ai.utils.paper_dedupe import dedupe_paper_dicts

_ROOT = Path(__file__).resolve().parents[2]


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


class _FakeCfg:
    def __init__(self, *, save_path: str = "", workflow=None):
        self.question = ""
        self.dataset_path = ""
        self.domain = ""
        self.language = ""
        self.save_root = ""
        self.thread_id = ""
        self.save_path = save_path
        self.workflow = workflow
        self.code_hint = ""
        self.llm = SimpleNamespace(
            chat=SimpleNamespace(api_key="", base_url="", model=""),
            vision=SimpleNamespace(api_key="", base_url="", model=""),
            language="chs",
        )

    def save_toml(self, path):
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")


def test_h5105_format_prompt_reads_sidecars(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "lab_context.json").write_text('{"literature":[{"title":"Cite Me Paper"}]}', encoding="utf-8")
    (ws / "data_report.md").write_text("columns: year, score", encoding="utf-8")
    cfg = SimpleNamespace(save_path=str(tmp_path))
    filled = inject_job_context(
        "CTX:{lab_context}\nREP:{data_report}\nQ:{question}",
        cfg,
        {"save_path": str(tmp_path)},
    )
    assert "Cite Me Paper" in filled
    assert "columns: year, score" in filled
    empty = inject_job_context("{data_report}", SimpleNamespace(save_path=str(tmp_path / "missing")), {})
    assert "不得假装" in empty


def test_h5106_supervisor_prompts_require_data_report():
    plan = (_ROOT / "openlens_ai/prompts/management/supervisor_plan.md").read_text(encoding="utf-8")
    alter = (_ROOT / "openlens_ai/prompts/management/supervisor_alter_plan.md").read_text(encoding="utf-8")
    assert "{lab_context}" in plan and "{data_report}" in plan
    assert "do not pretend" in plan.lower()
    assert "{fail_log}" in alter
    assert "MUST mention" in alter
    medical = (_ROOT / "openlens_ai/prompts/medical/supervisor_plan.md").read_text(encoding="utf-8")
    assert "{lab_context}" not in medical


def test_h5107_adaptor_runs_without_file_manager(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "lab_context.json").write_text('{"note":"linked"}', encoding="utf-8")
    prompt = prepend_lab_context("write plan.md", str(tmp_path))
    assert "write plan.md" in prompt
    assert "linked" in prompt
    assert "lab_context.json" in prompt
    assert prepend_lab_context("write plan.md", str(tmp_path / "missing")) == "write plan.md"


def test_h5108_literature_prompt_and_dedupe():
    text = (_ROOT / "openlens_ai/prompts/management/literature_search.md").read_text(encoding="utf-8")
    assert "Coverage gate" in text
    assert "full text" in text
    assert "upper bound" in text
    rows = dedupe_paper_dicts(
        [
            {"title": "Same", "doi": "10.1/abc"},
            {"title": "Same again", "doi": "10.1/abc"},
            {"title": "Other", "doi": ""},
            {"title": "Other", "doi": ""},
        ]
    )
    assert len(rows) == 2
    assert rows[0]["doi"] == "10.1/abc"


def test_h5111_data_report_not_medical_crossover():
    mgmt = (_ROOT / "openlens_ai/prompts/management/data_report.md").read_text(encoding="utf-8")
    general = (_ROOT / "openlens_ai/prompts/general/data_report.md").read_text(encoding="utf-8")
    assert "Medical Data Processing Agent" not in mgmt
    assert "data_show" in mgmt
    assert "Medical Data Processing Agent" not in general
    medical = (_ROOT / "openlens_ai/prompts/medical/data_report.md").read_text(encoding="utf-8")
    assert "Medical Data Processing Agent" in medical


def test_h5109_missing_plan_is_not_succeeded(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5109pl")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "need plan", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
        cfg = _FakeCfg(workflow=SimpleNamespace(enable_literature_review=True))
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=cfg),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
            patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "failed", (row.status, row.error)
        assert row.error_class == "parse"
        assert "plan.md" in (row.error or "")
        detail = client.get(f"/api/v1/research-runs/{rid}", headers=h)
        assert detail.status_code == 200, detail.text
        assert "plan.md" in (detail.json().get("diagnosis") or {}).get("next_step", "")
    finally:
        db.close()


def test_h5110_unused_title_and_cite_ok(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5110un")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Must Cite Graph Paper", "abstract": "g"},
        headers=h,
    ).json()
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "domain": "general", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert created.status_code == 200, created.text
    inj = created.json().get("injected") or {}
    q = compose_injected_question("研究问题", inj)
    assert "Must Cite Graph Paper" in q
    assert "未使用" in q
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        dest = Path(db.get(ResearchRun, rid).save_path)
    finally:
        db.close()
    assert unused_injected_titles(dest, inj) == ["Must Cite Graph Paper"]
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述。未使用：Must Cite Graph Paper。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{must2020,\n  title={Must Cite Graph Paper},\n  author={A},\n  year={2020},\n  journal={X}\n}\n",
        encoding="utf-8",
    )
    (dest / "plan.md").write_text("计划引用 Must Cite Graph Paper", encoding="utf-8")
    assert unused_injected_titles(dest, inj) == []
    cfg = _FakeCfg(workflow=SimpleNamespace(enable_literature_review=True))
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert research_output_gate(cfg, row, inj) == ""
        llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=cfg),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
            patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "succeeded", (row.status, row.error)
    finally:
        db.close()


def test_h5112_injected_note_still_on_create(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5112nt")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Side Car Paper", "abstract": "x"},
        headers=h,
    ).json()
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [paper["id"]], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    note = (run.json().get("injected") or {}).get("note") or ""
    assert "lab_context.json" in note
    q = compose_injected_question("研究问题", run.json().get("injected"))
    assert "Side Car Paper" in q
