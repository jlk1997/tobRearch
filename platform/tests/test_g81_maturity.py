from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openlens_ai.utils.literature_bib import (
    finalize_literature_review,
    render_literature_review_bib,
    valid_bib_titles,
    write_literature_review_bib_from_state,
)
from openlens_ai.utils.literature_coverage import literature_search_should_stop, unique_paper_count
from openlens_ai.utils.paper_dedupe import dedupe_paper_dicts, paper_identity_key
from fengsuan.app.services.research_engine import literature_bib_gate, research_output_gate

_ROOT = Path(__file__).resolve().parents[2]


def test_g81_url_and_punctuation_collapse():
    rows = [
        {"title": "A Paper About Graphs.", "url": "https://arxiv.org/abs/2301.00001"},
        {"title": "a paper about graphs", "arxiv_id": "2301.00001v3"},
        {"title": "Other Distinct Graph Paper", "url": "https://doi.org/10.1000/xyz"},
        {"title": "Other Distinct Graph Paper!", "doi": "10.1000/xyz"},
    ]
    out = dedupe_paper_dicts(rows)
    assert len(out) == 2
    assert paper_identity_key({"arxiv_id": "arxiv:2301.00001v2"}).startswith("arxiv:")
    assert paper_identity_key({"url": "https://arxiv.org/pdf/2301.00001v2.pdf"}).startswith("arxiv:")
    assert paper_identity_key({"url": "https://doi.org/10.1000/xyz"}).startswith("doi:")


def test_g81_short_title_without_id_still_dedupes():
    rows = [
        {"title": "Other", "doi": ""},
        {"title": "Other", "doi": ""},
        {"title": "Same", "doi": "10.1/abc"},
        {"title": "Same again", "doi": "10.1/abc"},
    ]
    out = dedupe_paper_dicts(rows)
    assert len(out) == 2
    assert any(r.get("doi") == "10.1/abc" for r in out)


def test_g81_pubmed_url_identity():
    rows = [
        {"title": "Clinical Trial Paper Here", "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"},
        {"title": "Clinical Trial Paper Here.", "pubmed_id": "12345678"},
    ]
    assert len(dedupe_paper_dicts(rows)) == 1


def test_g81_untitled_not_written_to_bib():
    bib = render_literature_review_bib(
        [
            {"title": "untitled", "doi": "10.1000/abc"},
            {"title": "Untitled Paper"},
            {"title": ""},
            {"title": "Must Cite Graph Paper", "authors": ["Ada"], "year": 2020},
        ]
    )
    assert "Must Cite Graph Paper" in bib
    assert "untitled" not in bib.lower()
    assert valid_bib_titles(bib) == ["Must Cite Graph Paper"]


def test_g81_punctuation_variants_do_not_early_stop():
    cfg = SimpleNamespace(workflow=SimpleNamespace(literature_search_min_tool_call=10))
    same = {
        "literature_tool_call_counter": 3,
        "messages": [
            SimpleNamespace(
                content='[{"title": "Paper Alpha Title Here."},{"title": "paper alpha title here"},{"title": "Paper Alpha Title Here!"}]'
            )
        ],
        "lab_context": {"literature": []},
    }
    assert unique_paper_count(same) == 1
    assert literature_search_should_stop(same, cfg) is False


def test_g81_url_only_paper_counts_for_coverage():
    state = {
        "literature_tool_call_counter": 3,
        "messages": [
            SimpleNamespace(
                content='[{"url":"https://arxiv.org/abs/2301.00001"},{"url":"https://arxiv.org/abs/2301.00002"},{"doi":"10.1000/abc","title":"Named Coverage Paper Here"}]'
            )
        ],
        "lab_context": {"literature": []},
    }
    assert unique_paper_count(state) >= 3


def test_g81_unique_count_falls_back_when_parse_raises(monkeypatch):
    from openlens_ai.utils import literature_bib

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(literature_bib, "papers_from_messages", boom)
    state = {"messages": [SimpleNamespace(content='{"title": "A long enough paper title here"}')]}
    assert unique_paper_count(state) >= 1


def test_g81_finalize_reads_utf8_and_skips_empty_bib(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述 中文题名 Must Cite Graph Paper。", encoding="utf-8")
    bib_path, report = finalize_literature_review(
        tmp_path,
        {"messages": [SimpleNamespace(content='{"title":"untitled","url":"https://arxiv.org/abs/2301.00001"}')]},
    )
    assert "Must Cite Graph Paper" in report
    assert bib_path.read_text(encoding="utf-8").strip() == ""


def test_g82_untitled_bib_cannot_succeed(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述正文没有可用书目。", encoding="utf-8")
    (ws / "literature_review.bib").write_text(
        "@article{z,\n  title={untitled},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    msg = literature_bib_gate(tmp_path, cfg, run)
    assert "untitled" in msg
    assert "不得标完成" in msg
    assert research_output_gate(cfg, run, None)


def test_g82_real_title_without_doi_still_passes(tmp_path):
    write_literature_review_bib_from_state(
        tmp_path,
        {"messages": [SimpleNamespace(content='{"title":"Must Cite Graph Paper","authors":["Ada"],"year":2020}')]},
    )
    ws = tmp_path / "workspace"
    (ws / "literature_review.md").write_text("综述 Must Cite Graph Paper。", encoding="utf-8")
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    assert literature_bib_gate(tmp_path, cfg, run) == ""


def test_g82_wf_none_still_skips_gate(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述", encoding="utf-8")
    cfg = SimpleNamespace()
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    assert literature_bib_gate(tmp_path, cfg, run) == ""


def test_g81_prompts_record_identifier_and_reject_untitled():
    search = (_ROOT / "openlens_ai/prompts/general/literature_search.md").read_text(encoding="utf-8")
    report = (_ROOT / "openlens_ai/prompts/general/literature_review_report.md").read_text(encoding="utf-8")
    related = (_ROOT / "openlens_ai/prompts/general/latex_related_works.md").read_text(encoding="utf-8")
    assert "landing url" in search.lower()
    assert "untitled" in search.lower()
    assert "section headings" in report.lower()
    assert "untitled" in related.lower()
    assert "do not invent" in search.lower()
