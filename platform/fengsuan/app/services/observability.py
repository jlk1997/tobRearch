"""进程内指标与可选 OpenTelemetry。不新开监控产品页，只暴露 /metrics。"""

from __future__ import annotations

from collections import Counter

_COUNTERS: Counter[str] = Counter()


def inc(name: str, n: int = 1) -> None:
    if name:
        _COUNTERS[name] += int(n)


def render_prometheus() -> str:
    lines = [
        "# HELP fengsuan_up 1 if process is up",
        "# TYPE fengsuan_up gauge",
        "fengsuan_up 1",
    ]
    try:
        from fengsuan.app.services.worker import worker_snapshot

        snap = worker_snapshot() or {}
        busy = 1 if snap.get("busy") or snap.get("running") else 0
        lines += [
            "# HELP fengsuan_worker_busy Worker is executing a job",
            "# TYPE fengsuan_worker_busy gauge",
            f"fengsuan_worker_busy {busy}",
        ]
    except Exception:
        pass
    lines += [
        "# HELP fengsuan_agent_steps_total Agent tool steps recorded in this process",
        "# TYPE fengsuan_agent_steps_total counter",
        f"fengsuan_agent_steps_total {_COUNTERS.get('agent_steps', 0)}",
        "# HELP fengsuan_agent_seed_total Seed-tool packs run for existing AI features",
        "# TYPE fengsuan_agent_seed_total counter",
        f"fengsuan_agent_seed_total {_COUNTERS.get('agent_seeds', 0)}",
        "# HELP fengsuan_parse_jobs_total Background parse / page-text jobs observed",
        "# TYPE fengsuan_parse_jobs_total counter",
        f"fengsuan_parse_jobs_total {_COUNTERS.get('parse_jobs', 0)}",
        "# HELP fengsuan_agent_pending_confirm_total Write/job tools waiting for user confirm",
        "# TYPE fengsuan_agent_pending_confirm_total counter",
        f"fengsuan_agent_pending_confirm_total {_COUNTERS.get('agent_pending_confirm', 0)}",
        "# HELP fengsuan_agent_degraded_total Agent loops that fell back to single-turn chat",
        "# TYPE fengsuan_agent_degraded_total counter",
        f"fengsuan_agent_degraded_total {_COUNTERS.get('agent_degraded', 0)}",
    ]
    return "\n".join(lines) + "\n"


def setup_otel(app) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass
