from __future__ import annotations

from types import SimpleNamespace

from openlens_ai.utils.literature_coverage import lab_titles_covered, unique_paper_count


def test_g4_sidecar_titles_not_covered_without_mention(tmp_path):
    (tmp_path / "lab_context.json").write_text(
        '{"literature":[{"title":"Sidecar Injected Title Alpha"}]}',
        encoding="utf-8",
    )
    state = {"messages": [SimpleNamespace(content="found three other papers")], "save_path": str(tmp_path)}
    assert lab_titles_covered(state, str(tmp_path)) is False
    state["messages"] = [SimpleNamespace(content="Sidecar Injected Title Alpha is cited")]
    assert lab_titles_covered(state, str(tmp_path)) is True


def test_g4_unused_mark_covers_sidecar(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "lab_context.json").write_text(
        '{"literature":[{"title":"Workspace Sidecar Title Beta"}]}',
        encoding="utf-8",
    )
    state = {"messages": [SimpleNamespace(content="未使用：Workspace Sidecar Title Beta")], "save_path": str(tmp_path)}
    assert lab_titles_covered(state) is True


def test_g4_unique_count_prefers_tool_calls(tmp_path):
    tc = tmp_path / "tool_calls"
    tc.mkdir()
    (tc / "hits.json").write_text(
        '[{"title":"Tool Paper One Title"},{"title":"Tool Paper Two Title","doi":"10.1000/aaa"}]',
        encoding="utf-8",
    )
    state = {
        "save_path": str(tmp_path),
        "messages": [SimpleNamespace(content='no titles here 10.9/x')],
    }
    assert unique_paper_count(state, str(tmp_path)) >= 2
