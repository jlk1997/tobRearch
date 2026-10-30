from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fengsuan.app.services.research_engine import literature_sidecar, research_diagnosis


def _plant(dest: Path, *, mention_lab: bool = False):
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "tool_calls").mkdir(parents=True, exist_ok=True)
    md = "综述 Must Cite Graph Paper。" if mention_lab else "综述正文没有点名。"
    (dest / "workspace" / "literature_review.md").write_text(md, encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{z,\n  title={Unseen Title Paper},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    (dest / "tool_calls" / "search_hits.jsonl").write_text(
        '{"title":"Unseen Title Paper","doi":"10.1000/u"}\n',
        encoding="utf-8",
    )
    (dest / "lab_context.json").write_text(
        json.dumps({"literature": [{"title": "Must Cite Graph Paper"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_g92_sidecar_has_injected_and_hit_titles(tmp_path):
    _plant(tmp_path)
    extra = literature_sidecar(tmp_path)
    assert extra["unused_injected_titles"] == ["Must Cite Graph Paper"]
    assert extra["unused_bib_titles"] == ["Unseen Title Paper"]
    assert "Unseen Title Paper" in extra["search_hit_titles"]
    run = SimpleNamespace(
        save_path=str(tmp_path),
        status="failed",
        error="组内已关联文献未在综述/结论中点名",
        error_class="parse",
        progress_json="{}",
    )
    d = research_diagnosis(run)
    assert d["unused_injected_titles"] == ["Must Cite Graph Paper"]
    assert d["search_hit_titles"] == ["Unseen Title Paper"]


def test_g92_mentioned_lab_title_not_unused(tmp_path):
    _plant(tmp_path, mention_lab=True)
    extra = literature_sidecar(tmp_path)
    assert extra["unused_injected_titles"] == []
    assert extra["unused_bib_titles"] == ["Unseen Title Paper"]
