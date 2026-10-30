"""A-ENGINE：并行边、ALTER_PLAN 反馈、返回值替代 sys.exit。"""

from __future__ import annotations

from types import SimpleNamespace

from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from fengsuan.app.services.research_engine import classify_engine_exit
from openlens_ai.graph_wiring import enabled_subgraphs, wire_parent_graph


class _S(TypedDict, total=False):
    x: int


def _router(_state):
    return "NONE"


def test_classify_engine_exit_accepts_return_status():
    assert classify_engine_exit(0) == "succeeded"
    assert classify_engine_exit(None) == "succeeded"
    assert classify_engine_exit(0, interrupt_after="coder") == "paused"
    assert classify_engine_exit(1) == "failed"
    assert classify_engine_exit({"status": "completed"}) == "succeeded"
    assert classify_engine_exit({"status": "paused"}) == "paused"
    assert classify_engine_exit({"status": "failed"}) == "failed"
    assert classify_engine_exit("completed", interrupt_after="coder") == "paused"


def test_wire_parent_parallel_and_feedback():
    g = StateGraph(_S)
    for name in ("literature_reviewer", "data_analyzer", "supervisor", "coder", "latex_writer", "end"):
        g.add_node(name, lambda s: s)
    desc = wire_parent_graph(
        g,
        ["literature_reviewer", "data_analyzer", "supervisor", "coder", "latex_writer"],
        _router,
    )
    pairs = set(desc["edges"])
    assert (str(START), "literature_reviewer") in pairs
    assert (str(START), "data_analyzer") in pairs
    assert ("literature_reviewer", "supervisor") in pairs
    assert ("data_analyzer", "supervisor") in pairs
    cond = desc["conditionals"]["coder"]
    assert cond["DECISION: ALTER_PLAN"] == "supervisor"
    assert cond["DECISION: REANALYZE_DATA"] == "data_analyzer"
    assert cond["NONE"] == "latex_writer"


def test_enabled_subgraphs_respects_flags():
    wf = SimpleNamespace(
        enable_literature_review=False,
        enable_latex_writer=True,
        enable_artifact_publisher=False,
    )
    cfg = SimpleNamespace(workflow=wf)
    seq = enabled_subgraphs(cfg)
    assert "literature_reviewer" not in seq
    assert "data_analyzer" in seq
    assert seq[0] == "data_analyzer"
    assert "latex_writer" in seq
    assert "artifact_publisher" not in seq
