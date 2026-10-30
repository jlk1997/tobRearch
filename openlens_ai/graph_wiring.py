"""Parent-graph edge plan. No FileManager / agent imports — safe for unit tests."""

from __future__ import annotations

from langgraph.graph import END, START

ALL_SUBGRAPHS = [
    "literature_reviewer",
    "data_analyzer",
    "supervisor",
    "coder",
    "latex_writer",
    "artifact_publisher",
]


def enabled_subgraphs(config, start_subgraph: str | None = None) -> list[str]:
    seq = ALL_SUBGRAPHS.copy()
    if start_subgraph:
        if start_subgraph in seq:
            seq = seq[seq.index(start_subgraph) :]
    wf = getattr(config, "workflow", None)
    if wf is not None:
        if not getattr(wf, "enable_literature_review", True) and "literature_reviewer" in seq:
            seq.remove("literature_reviewer")
        if not getattr(wf, "enable_latex_writer", True) and "latex_writer" in seq:
            seq.remove("latex_writer")
        if not getattr(wf, "enable_artifact_publisher", False) and "artifact_publisher" in seq:
            seq.remove("artifact_publisher")
    return seq


def wire_parent_graph(graph_builder, seq: list[str], keywords_router) -> dict:
    """并行 literature_reviewer+data_analyzer，coder 反馈 ALTER_PLAN / REANALYZE_DATA。"""
    desc: dict = {"edges": [], "conditionals": {}}

    def edge(a, b):
        graph_builder.add_edge(a, b)
        desc["edges"].append((str(a), str(b)))

    can_parallel = len(seq) >= 2 and seq[0] == "literature_reviewer" and seq[1] == "data_analyzer"
    if can_parallel:
        join = seq[2] if len(seq) > 2 else "end"
        edge(START, "literature_reviewer")
        edge(START, "data_analyzer")
        edge("literature_reviewer", join)
        edge("data_analyzer", join)
        remain = seq[2:]
    elif seq:
        edge(START, seq[0])
        remain = seq
    else:
        edge(START, "end")
        remain = []

    i = 0
    while i < len(remain):
        node = remain[i]
        nxt = remain[i + 1] if i + 1 < len(remain) else "end"
        if node == "coder":
            if i > 0:
                edge(remain[i - 1], "coder")
            mapping = {
                "DECISION: ALTER_PLAN": "supervisor",
                "DECISION: REANALYZE_DATA": "data_analyzer",
                "NONE": nxt,
            }
            graph_builder.add_conditional_edges("coder", keywords_router, mapping)
            desc["conditionals"]["coder"] = mapping
            i += 1
            continue
        if i + 1 < len(remain) and remain[i + 1] == "coder":
            i += 1
            continue
        if i == 0 and not can_parallel and i + 1 < len(remain) and remain[i + 1] != "coder":
            edge(node, remain[i + 1])
            i += 1
            continue
        if i > 0 and remain[i - 1] != "coder":
            edge(remain[i - 1], node)
        i += 1

    if remain and remain[-1] != "coder":
        edge(remain[-1], "end")
    edge("end", END)
    return desc
