from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openlens_ai.tools.paper_search_tool import DummySearchTool, papers_to_dicts
from openlens_ai.tools.paper_search_mcp.paper import Paper
from datetime import datetime

from openlens_ai.utils.job_context import bind_job_save_path
from openlens_ai.utils.literature_bib import (
    harvest_papers_from_text,
    persist_search_hits,
    render_literature_review_bib,
    write_literature_review_bib_from_state,
)
from openlens_ai.utils.literature_coverage import literature_search_should_stop, unique_paper_count
from openlens_ai.utils.paper_dedupe import paper_usable_for_bib

_ROOT = Path(__file__).resolve().parents[2]


def test_g83_harvest_fence_and_buffer_string():
    blob = (
        "Tool Message\nName: search_arxiv_tool\n\n"
        "```json\n"
        '[{"title":"Harvested Graph Paper Here","url":"https://arxiv.org/abs/2301.00001"}]\n'
        "```\n"
        "Human: ignore this heading\n"
    )
    rows = harvest_papers_from_text(blob)
    assert any("Harvested Graph Paper Here" in (r.get("title") or "") for r in rows)
    assert any(str(r.get("arxiv_id") or "") == "2301.00001" or "2301.00001" in str(r.get("url") or "") for r in rows)


def test_g83_heading_only_is_not_a_paper():
    assert harvest_papers_from_text("# A Very Long Review Heading Used As Fake Bib\n") == []


def test_g84_persist_survives_truncated_messages(tmp_path):
    bind_job_save_path(str(tmp_path))
    try:
        persist_search_hits(
            [{"title": "Persisted Coverage Paper Here", "doi": "10.1000/persist", "url": "https://doi.org/10.1000/persist"}]
        )
        path = write_literature_review_bib_from_state(
            tmp_path,
            {"messages": [SimpleNamespace(content="truncated snippet without titles")]},
        )
        text = path.read_text(encoding="utf-8")
        assert "Persisted Coverage Paper Here" in text
        hits = tmp_path / "tool_calls" / "search_hits.jsonl"
        assert hits.is_file()
    finally:
        bind_job_save_path("")


def test_g84_papers_to_dicts_stamps_arxiv_and_persists(tmp_path):
    bind_job_save_path(str(tmp_path))
    try:
        paper = Paper(
            paper_id="2301.00009",
            title="Stamped Arxiv Paper Title Here",
            authors=["A"],
            abstract="abs",
            doi="",
            published_date=datetime.fromtimestamp(0),
            pdf_url="",
            url="https://arxiv.org/abs/2301.00009",
            source="arxiv",
        )
        rows = papers_to_dicts([paper])
        papers = [r for r in rows if not r.get("_search_ledger")]
        assert rows[0].get("_search_ledger") is True
        assert papers[0].get("arxiv_id") == "2301.00009"
        assert (tmp_path / "tool_calls" / "search_hits.jsonl").is_file()
    finally:
        bind_job_save_path("")


def test_g84_dummy_returns_three_distinct():
    rows = DummySearchTool()._run("q", max_results=3)
    papers = [r for r in rows if not r.get("_search_ledger")]
    titles = {r.get("title") for r in papers}
    assert len(titles) == 3
    assert all(paper_usable_for_bib(r) for r in papers)


def test_g85_web_hit_stays_out_of_bib():
    bib = render_literature_review_bib(
        [
            {
                "title": "Random Blog Post About Graphs",
                "url": "https://example.com/blog/graphs",
                "source": "tavily",
            },
            {"title": "Must Cite Graph Paper", "authors": ["Ada"], "year": 2020},
        ]
    )
    assert "Must Cite Graph Paper" in bib
    assert "Random Blog Post" not in bib


def test_g85_title_only_three_do_not_early_stop():
    cfg = SimpleNamespace(workflow=SimpleNamespace(literature_search_min_tool_call=10))
    state = {
        "literature_tool_call_counter": 3,
        "messages": [
            SimpleNamespace(
                content='[{"title": "Paper Alpha Title Here"},{"title": "Paper Beta Title Here"},{"title": "Paper Gamma Title Here"}]'
            )
        ],
        "lab_context": {"literature": []},
    }
    assert unique_paper_count(state) >= 3
    assert literature_search_should_stop(state, cfg) is False


def test_g85_three_with_doi_still_early_stop():
    cfg = SimpleNamespace(workflow=SimpleNamespace(literature_search_min_tool_call=10))
    rich = {
        "literature_tool_call_counter": 3,
        "messages": [
            SimpleNamespace(
                content='[{"title": "Paper Alpha Title Here"},{"title": "Paper Beta Title Here"},{"title": "Paper Gamma Title Here","doi":"10.1000/abc"}]'
            )
        ],
        "lab_context": {"literature": []},
    }
    assert literature_search_should_stop(rich, cfg) is False
    rich["messages"] = [
        SimpleNamespace(
            content='[{"title": "Paper Alpha Title Here","abstract":"'
            + ("Graph convolution abstract text for coverage. " * 4)
            + '"},{"title": "Paper Beta Title Here"},{"title": "Paper Gamma Title Here","doi":"10.1000/abc"}]'
        )
    ]
    assert literature_search_should_stop(rich, cfg) is True


def test_g83_prompts_web_is_not_bib():
    text = (_ROOT / "openlens_ai/prompts/general/literature_search.md").read_text(encoding="utf-8")
    assert "landing pages" in text.lower()
    assert "bibliography" in text.lower()
