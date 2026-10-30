from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openlens_ai.utils.literature_bib import bib_titles, write_literature_review_bib_from_state
from fengsuan.app.services.research_engine import literature_bib_gate

_ROOT = Path(__file__).resolve().parents[2]


def test_g5_medical_prompt_no_five_call_stop():
    text = (_ROOT / "openlens_ai/prompts/medical/literature_search.md").read_text(encoding="utf-8")
    assert "more than 5 times" not in text.lower()
    assert "coverage gate" in text.lower()
    assert "do not invent" in text.lower()


def test_g5_title_only_fallback_cannot_succeed(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("# A Very Long Review Heading Used As Fake Bib\n", encoding="utf-8")
    path = write_literature_review_bib_from_state(tmp_path, {"messages": []})
    assert path.read_text(encoding="utf-8").strip() == ""
    assert bib_titles(path.read_text(encoding="utf-8")) == []
    cfg = SimpleNamespace(workflow=SimpleNamespace(enable_literature_review=True))
    run = SimpleNamespace(save_path=str(tmp_path), interrupt_after="")
    assert literature_bib_gate(tmp_path, cfg, run)


def test_g5_management_materials_have_bib_discipline():
    mg = (_ROOT / "openlens_ai/prompts/management/literature_search.md").read_text(encoding="utf-8")
    mt = (_ROOT / "openlens_ai/prompts/materials/literature_search.md").read_text(encoding="utf-8")
    assert "do not invent citations" in mg.lower()
    assert "medrxiv" in mg.lower()
    assert "do not invent citations" in mt.lower()
    assert "more than 5 times" not in mg.lower()
    assert "more than 5 times" not in mt.lower()
