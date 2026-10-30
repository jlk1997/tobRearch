"""Compatibility / harness layer for the context-integrity study.

Goal: let PoCs and the eval harness exercise the *real* OpenLens functions
that live in ``openlens_ai`` while staying fully offline and deterministic.

Two things are handled here:

1. ``install_file1agent_stub()`` registers a minimal ``file1agent`` module so
   that ``openlens_ai.state`` (and therefore ``openlens_ai.tools.tool_utils``)
   imports without the heavy optional dependency. Only the import-time symbol
   ``file1agent.file_manager.FileManager`` is needed; we never call it.

2. ``fake_reranker`` / ``failing_reranker`` patch the network call inside
   ``openlens_ai.utils.embedding`` so that the genuine ``vector_search`` /
   ``perform_rerank`` code paths run against a deterministic *surrogate*
   reranker instead of a remote API. The surrogate scores a document by how
   many query terms it contains (a faithful stand-in for the lexical/semantic
   relevance that a real cross-encoder such as bge-reranker rewards). This is
   what makes the "rank-and-survive" objective reproducible without a key.

If you have a real rerank endpoint, set RERANK_MODEL / RERANK_API_KEY /
RERANK_BASE_URL and call the real functions directly; the surrogate is only a
convenience for CI-style reproduction.
"""

from __future__ import annotations

import contextlib
import re
import sys
import types
from typing import Callable, Iterable

# Rerank endpoint placeholders used when driving the real vector_search code.
# The surrogate ignores these; they only need to be non-empty strings.
DUMMY_RERANK_MODEL = "surrogate-reranker"
DUMMY_RERANK_KEY = "surrogate-key"
DUMMY_RERANK_URL = "http://surrogate.local/"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def install_file1agent_stub() -> None:
    """Register a minimal ``file1agent`` package so ``openlens_ai`` imports."""
    if "file1agent" in sys.modules:
        return
    pkg = types.ModuleType("file1agent")
    pkg.__path__ = []  # mark as package
    fm_mod = types.ModuleType("file1agent.file_manager")

    class FileManager:  # noqa: D401 - import-time placeholder only
        """Placeholder; the study never instantiates the real FileManager."""

        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover
            raise RuntimeError("file1agent stub is import-only")

    fm_mod.FileManager = FileManager
    pkg.file_manager = fm_mod
    sys.modules["file1agent"] = pkg
    sys.modules["file1agent.file_manager"] = fm_mod


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def query_term_score(query: str, doc: str) -> float:
    """Surrogate relevance: term-frequency of query terms in the doc.

    Rewards documents that contain (and repeat) query terms, which is exactly
    the signal an attacker maximises to keep a payload alive across rounds.
    """
    q_terms = set(tokenize(query))
    if not q_terms:
        return 0.0
    d_terms = tokenize(doc)
    if not d_terms:
        return 0.0
    hits = sum(1 for t in d_terms if t in q_terms)
    # Normalise softly by length so pure length-stuffing is not free, but
    # repeating query terms still pays off.
    return hits / (1.0 + 0.02 * len(d_terms))


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _RerankStub:
    """Stands in for the ``requests`` module used inside embedding.py."""

    def __init__(self, scorer: Callable[[str, str], float] | None, fail: bool):
        self._scorer = scorer or query_term_score
        self._fail = fail
        self.calls = 0

    def post(self, url, json=None, headers=None, **kwargs):  # noqa: A002
        self.calls += 1
        if self._fail:
            # Emulate a broken / unreachable rerank service: a body with no
            # usable "results" key drives perform_rerank into its fail-open
            # branch (embedding.py lines ~105-108).
            return _FakeResponse({"error": "rerank backend unavailable"})
        query = (json or {}).get("query", "")
        docs = (json or {}).get("documents", [])
        results = []
        for i, doc in enumerate(docs):
            results.append(
                {
                    "index": i,
                    "document": doc,
                    "relevance_score": float(self._scorer(query, doc)),
                }
            )
        return _FakeResponse({"results": results})


@contextlib.contextmanager
def patched_reranker(scorer: Callable[[str, str], float] | None = None, fail: bool = False):
    """Patch embedding.requests with a deterministic surrogate reranker.

    Also neutralises the module's retry ``time.sleep`` so the fail-open branch
    (which the real code retries 5x10s before falling back) runs instantly.
    This changes timing only, not behaviour.
    """
    from openlens_ai.utils import embedding as emb

    stub = _RerankStub(scorer, fail)
    original_requests = emb.requests
    original_time = emb.time
    emb.requests = stub  # type: ignore[assignment]
    # Replace the module reference (not the global time module) with a shim that
    # exposes an instant sleep; perform_rerank only uses time.sleep.
    emb.time = types.SimpleNamespace(sleep=lambda *_a, **_k: None)  # type: ignore[assignment]
    try:
        yield stub
    finally:
        emb.requests = original_requests  # type: ignore[assignment]
        emb.time = original_time  # type: ignore[assignment]


def import_real():
    """Import the real repo functions under study, returning them in a dict."""
    install_file1agent_stub()
    from openlens_ai.utils.embedding import vector_search, perform_rerank
    from openlens_ai.utils.job_context import (
        inject_job_context,
        prepend_lab_context,
        read_job_sidecar,
    )
    from openlens_ai.utils.literature_coverage import (
        lab_titles_covered,
        literature_search_should_stop,
        unique_paper_count,
    )
    from openlens_ai.tools.tool_utils import route_by_keywords

    return {
        "vector_search": vector_search,
        "perform_rerank": perform_rerank,
        "route_by_keywords": route_by_keywords,
        "read_job_sidecar": read_job_sidecar,
        "prepend_lab_context": prepend_lab_context,
        "inject_job_context": inject_job_context,
        "literature_search_should_stop": literature_search_should_stop,
        "unique_paper_count": unique_paper_count,
        "lab_titles_covered": lab_titles_covered,
    }


def role_of(message) -> str:
    """Best-effort role label for a LangChain message object."""
    t = message.__class__.__name__
    return {
        "SystemMessage": "system",
        "HumanMessage": "user",
        "AIMessage": "assistant",
        "ToolMessage": "tool",
    }.get(t, t)


def summarize_roles(messages: Iterable) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in messages:
        r = role_of(m)
        out[r] = out.get(r, 0) + 1
    return out
