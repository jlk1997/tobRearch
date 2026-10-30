"""Probe which real repo functions we can import in this environment.

Run: python research/context_integrity/_probe_imports.py
"""
from __future__ import annotations

MODS = [
    ("tool_utils.route_by_keywords", "from openlens_ai.tools.tool_utils import route_by_keywords"),
    ("literature_coverage", "from openlens_ai.utils.literature_coverage import literature_search_should_stop, unique_paper_count, lab_titles_covered"),
    ("job_context", "from openlens_ai.utils.job_context import read_job_sidecar, prepend_lab_context, inject_job_context"),
    ("embedding", "from openlens_ai.utils.embedding import vector_search, perform_rerank, vector_search_match_type"),
    ("paper_dedupe", "from openlens_ai.utils.paper_dedupe import dedupe_paper_dicts"),
    ("config.Config", "from openlens_ai.utils.config import Config"),
    ("langchain messages", "from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage"),
    ("get_buffer_string", "from langchain_core.messages.utils import get_buffer_string, count_tokens_approximately"),
]


def main() -> None:
    ok, fail = [], []
    for name, stmt in MODS:
        try:
            exec(stmt, {})
            ok.append(name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, type(e).__name__, str(e)[:180]))
    print("=== IMPORT OK ===")
    for n in ok:
        print("  OK  ", n)
    print("=== IMPORT FAIL ===")
    for n, t, m in fail:
        print("  FAIL", n, "|", t, "|", m)


if __name__ == "__main__":
    main()
