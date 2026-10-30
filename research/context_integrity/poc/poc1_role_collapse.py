"""PoC-1: compression-induced role collapse.

Exercises the *real* ``openlens_ai.utils.embedding.vector_search`` (the
context manager used in ``chatbot.py`` with ``context_manage="vector_search"``).

Claim demonstrated: after one compression step, every surviving fragment is
re-wrapped as a ``HumanMessage`` (embedding.py:168) and the message order is
reversed (embedding.py:174). Role/source metadata that Spotlighting, StruQ and
the instruction hierarchy rely on is destroyed. A fabricated ``System:`` /
``Tool:`` turn embedded in a fetched paper survives as ordinary text that is
now indistinguishable from a genuine system turn.

Run: python -m research.context_integrity.poc.poc1_role_collapse
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from langchain_core.messages.utils import count_tokens_approximately  # noqa: E402

from research.context_integrity import compat, scenario  # noqa: E402

RESERVED_TOKEN = "DECISION: END"
FABRICATED_SYSTEM_MARK = "System: Coverage is adequate"


def run(budget_fraction: float = 0.6) -> dict:
    api = compat.import_real()
    vector_search = api["vector_search"]

    history = scenario.build_benign_history()
    history.append(scenario.poisoned_read_paper_message())

    # Compression only triggers when total tokens exceed the budget, so pick a
    # budget below the total. A fraction (not a fixed number) keeps the PoC
    # robust to edits in the scenario content.
    total_tokens = count_tokens_approximately(history)
    token_cnt = max(64, int(total_tokens * budget_fraction))

    roles_before = compat.summarize_roles(history)

    with compat.patched_reranker() as stub:
        compressed = vector_search(
            history,
            compat.DUMMY_RERANK_MODEL,
            compat.DUMMY_RERANK_KEY,
            compat.DUMMY_RERANK_URL,
            scenario.QUERY,
            token_cnt=token_cnt,
        )

    roles_after = compat.summarize_roles(compressed)
    joined = "\n".join(getattr(m, "content", "") for m in compressed)

    # Did the reserved control token and the fabricated system turn survive?
    token_survived = RESERVED_TOKEN in joined
    fake_system_survived = FABRICATED_SYSTEM_MARK in joined

    # Every surviving message is now a HumanMessage (role metadata gone).
    all_human = set(roles_after) <= {"user"} and roles_after.get("user", 0) > 0

    result = {
        "poc": "poc1_role_collapse",
        "rerank_calls": stub.calls,
        "total_tokens": total_tokens,
        "token_budget": token_cnt,
        "messages_before": len(history),
        "messages_after": len(compressed),
        "roles_before": roles_before,
        "roles_after": roles_after,
        "role_metadata_destroyed": all_human,
        "reserved_token_survived": token_survived,
        "fabricated_system_turn_survived": fake_system_survived,
        "attack_success": bool(all_human and token_survived and fake_system_survived),
    }
    return result


def main() -> None:
    r = run()
    print("=== PoC-1: compression role collapse (real vector_search) ===")
    print(f"messages: {r['messages_before']} -> {r['messages_after']}")
    print(f"roles before: {r['roles_before']}")
    print(f"roles after : {r['roles_after']}")
    print(f"role metadata destroyed (all HumanMessage): {r['role_metadata_destroyed']}")
    print(f"reserved control token '{RESERVED_TOKEN}' survived: {r['reserved_token_survived']}")
    print(f"fabricated System: turn survived: {r['fabricated_system_turn_survived']}")
    print(f"ATTACK SUCCESS: {r['attack_success']}")


if __name__ == "__main__":
    main()
