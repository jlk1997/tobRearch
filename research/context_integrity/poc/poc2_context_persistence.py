"""PoC-2: context persistence and the "rank-and-survive" objective.

A long-running agent repeatedly compacts its history. Whether an injected
payload is still present after round N depends only on its reranker score, not
on who inserted it or whether it is malicious. We therefore define a new attack
objective: maximise *context persistence* - the number of compaction rounds a
payload survives.

This PoC runs the real ``vector_search`` for several rounds and compares two
payloads that carry the identical injected instruction:
  * naive     - no query-term stuffing (a plausible, low-effort injection);
  * optimized - stuffed with query terms so the reranker keeps ranking it high.

Run: python -m research.context_integrity.poc.poc2_context_persistence
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from langchain_core.messages.utils import count_tokens_approximately  # noqa: E402

from research.context_integrity import compat, scenario  # noqa: E402


def _joined(messages) -> str:
    return "\n".join(getattr(m, "content", "") for m in messages)


def _simulate(payload_msg, canary: str, rounds: int, token_budget: int) -> list[bool]:
    api = compat.import_real()
    vector_search = api["vector_search"]

    ctx = scenario.build_benign_history()
    ctx.append(payload_msg)

    survival: list[bool] = []
    with compat.patched_reranker():
        for r in range(1, rounds + 1):
            # New agent activity arrives, then the context is compacted to a
            # fixed budget (as a real deployment's compaction threshold would).
            ctx = ctx + scenario.benign_round_batch(r)
            ctx = vector_search(
                ctx,
                compat.DUMMY_RERANK_MODEL,
                compat.DUMMY_RERANK_KEY,
                compat.DUMMY_RERANK_URL,
                scenario.QUERY,
                token_cnt=token_budget,
            )
            survival.append(canary in _joined(ctx))
    return survival


def _survived_rounds(survival: list[bool]) -> int:
    """Consecutive rounds survived from the start (expected-lifetime proxy)."""
    n = 0
    for alive in survival:
        if not alive:
            break
        n += 1
    return n


def run(rounds: int = 8, token_budget: int = 600) -> dict:
    naive = _simulate(
        scenario.persistence_payload_naive(), scenario.CANARY_NAIVE, rounds, token_budget
    )
    optimized = _simulate(
        scenario.persistence_payload_optimized(), scenario.CANARY_OPTIMIZED, rounds, token_budget
    )
    return {
        "poc": "poc2_context_persistence",
        "rounds": rounds,
        "token_budget": token_budget,
        "naive_survival_curve": naive,
        "optimized_survival_curve": optimized,
        "naive_survived_rounds": _survived_rounds(naive),
        "optimized_survived_rounds": _survived_rounds(optimized),
        # The attack "works" when optimisation strictly extends persistence.
        "rank_and_survive_effective": _survived_rounds(optimized) > _survived_rounds(naive),
    }


def main() -> None:
    r = run()
    print("=== PoC-2: context persistence (rank-and-survive) ===")
    print(f"rounds={r['rounds']} token_budget={r['token_budget']}")
    print(f"naive     survival curve: {r['naive_survival_curve']}")
    print(f"optimized survival curve: {r['optimized_survival_curve']}")
    print(f"naive survived rounds    : {r['naive_survived_rounds']}")
    print(f"optimized survived rounds: {r['optimized_survived_rounds']}")
    print(f"RANK-AND-SURVIVE EFFECTIVE: {r['rank_and_survive_effective']}")


if __name__ == "__main__":
    main()
