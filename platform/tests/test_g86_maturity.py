from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openlens_ai.utils.job_context import bind_job_save_path
from openlens_ai.utils.literature_bib import persist_search_hits, title_mentioned_in
from fengsuan.app.services.research_engine import list_artifacts, literature_bib_gate


def test_g86_search_hits_listed_as_artifact(tmp_path):
    dest = tmp_path / "tool_calls"
    dest.mkdir()
    (dest / "search_hits.jsonl").write_text(
        '{"title":"Visible Hit Paper Title","doi":"10.1000/vis"}\n',
        encoding="utf-8",
    )
    (dest / "noise.txt").write_text("ignore", encoding="utf-8")
    names = {x["name"] for x in list_artifacts(str(tmp_path))}
    assert "search_hits.jsonl" in names
    assert "noise.txt" not in names


def test_g87_persist_rewrites_unique(tmp_path):
    bind_job_save_path(str(tmp_path))
    try:
        row = {"title": "Persisted Coverage Paper Here", "doi": "10.1000/persist"}
        persist_search_hits([row])
        persist_search_hits([row, {"title": "Persisted Coverage Paper Here.", "doi": "10.1000/persist"}])
        text = (tmp_path / "tool_calls" / "search_hits.jsonl").read_text(encoding="utf-8")
        assert text.count("Persisted Coverage Paper Here") == 1
        assert text.count("\n") == 1 or text.strip().count("\n") == 0
    finally:
        bind_job_save_path("")


def test_g88_unused_mark_counts_as_mention(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述。未使用：Unseen Title Paper。", encoding="utf-8")
    (ws / "literature_review.bib").write_text(
        "@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    assert literature_bib_gate(tmp_path, cfg, run) == ""
    assert title_mentioned_in("未使用：Unseen Title Paper", "Unseen Title Paper")


def test_g88_missing_titles_named_in_gate(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (ws / "literature_review.bib").write_text(
        "@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    hits = tmp_path / "tool_calls"
    hits.mkdir()
    (hits / "search_hits.jsonl").write_text(
        '{"title":"Unseen Title Paper","doi":"10.1000/u"}\n',
        encoding="utf-8",
    )
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    msg = literature_bib_gate(tmp_path, cfg, run)
    assert "未点名" in msg
    assert "Unseen Title Paper" in msg
    assert "1 条" in msg
    assert "未使用" in msg


def test_g86_empty_bib_mentions_hits(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("综述", encoding="utf-8")
    (ws / "literature_review.bib").write_text("", encoding="utf-8")
    hits = tmp_path / "tool_calls"
    hits.mkdir()
    (hits / "search_hits.jsonl").write_text(
        '{"title":"Persisted Coverage Paper Here","doi":"10.1000/persist"}\n',
        encoding="utf-8",
    )
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    msg = literature_bib_gate(tmp_path, cfg, run)
    assert "literature_review.bib" in msg
    assert "1 条" in msg
