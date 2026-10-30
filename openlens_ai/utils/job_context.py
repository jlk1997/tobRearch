"""作业 sidecar 读取。供 format_prompt / OpenHands 注入。不改图边。"""

from __future__ import annotations

import os
from contextvars import ContextVar

_JOB_SAVE_PATH: ContextVar[str] = ContextVar("openlens_job_save_path", default="")


def bind_job_save_path(path: str | None) -> None:
    _JOB_SAVE_PATH.set(str(path or "").strip())


def current_job_save_path() -> str:
    return _JOB_SAVE_PATH.get() or os.environ.get("OPENLENS_SAVE_PATH") or ""

_EMPTY_LAB_CONTEXT = "（作业目录尚无 lab_context.json）"
_EMPTY_DATA_REPORT = "（尚无 data_report.md，不得假装已完成数据剖析）"
_EMPTY_FAIL_LOG = "（尚无 openhands_logs，修订计划时须写明将查阅的日志路径 openhands_logs/）"
_EMPTY_ACTUAL_METRICS = "（尚无 actual_metrics.json，不得编造实测指标）"


def read_job_sidecar(save_path: str, *rel_parts: str, limit: int = 8000) -> str:
    root = (save_path or "").strip()
    if not root or not rel_parts:
        return ""
    names = [p for p in rel_parts if p]
    candidates = [
        os.path.join(root, "workspace", *names),
        os.path.join(root, *names),
        os.path.join(root, "workspace", "data_analyze", *names),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                text = open(path, encoding="utf-8").read()
            except OSError:
                continue
            return (text or "")[:limit]
    return ""


def latest_openhands_log(save_path: str, limit: int = 2000) -> str:
    folder = os.path.join(save_path or "", "openhands_logs")
    if not os.path.isdir(folder):
        return ""
    files = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.endswith(".log") or name.endswith(".txt")
    ]
    if not files:
        return ""
    newest = max(files, key=lambda p: os.path.getmtime(p))
    try:
        text = open(newest, encoding="utf-8", errors="ignore").read()
    except OSError:
        return newest
    tail = (text or "")[-limit:]
    return f"{newest}\n{tail}"


def _auth_context_on() -> bool:
    return os.environ.get("OPENLENS_AUTHENTICATED_CONTEXT", "").strip() in {"1", "true", "TRUE", "yes"}


def _maybe_untrust_sidecar(text: str, save_path: str, *rel_parts: str, config=None) -> str:
    """Workspace-written sidecars are reachable from the sandbox; mark them."""
    from .authenticated_context import enabled_by_config, wrap_sidecar

    on = _auth_context_on() or (config is not None and enabled_by_config(config))
    if not on:
        return text
    root = (save_path or "").strip()
    workspace_hit = bool(rel_parts) and bool(root) and os.path.isfile(os.path.join(root, "workspace", *rel_parts))
    if not rel_parts:
        # OpenHands logs live outside workspace but are written by the sandbox.
        workspace_hit = True
    return wrap_sidecar(text, from_workspace=workspace_hit)


def inject_job_context(prompt: str, config, state: dict | None = None) -> str:
    this_prompt = prompt or ""
    save_path = getattr(config, "save_path", "") or ""
    if isinstance(state, dict) and state.get("save_path"):
        save_path = state.get("save_path") or save_path
    if "{lab_context}" in this_prompt:
        ctx = read_job_sidecar(save_path, "lab_context.json") or _EMPTY_LAB_CONTEXT
        ctx = _maybe_untrust_sidecar(ctx, save_path, "lab_context.json", config=config)
        this_prompt = this_prompt.replace("{lab_context}", ctx)
    if "{data_report}" in this_prompt:
        report = read_job_sidecar(save_path, "data_report.md") or _EMPTY_DATA_REPORT
        this_prompt = this_prompt.replace("{data_report}", report)
    if "{fail_log}" in this_prompt:
        log = latest_openhands_log(save_path) or _EMPTY_FAIL_LOG
        log = _maybe_untrust_sidecar(log, save_path, config=config)
        this_prompt = this_prompt.replace("{fail_log}", log)
    if "{literature_evidence}" in this_prompt:
        try:
            from .literature_bib import write_literature_evidence

            write_literature_evidence(save_path)
        except Exception:
            pass
        ev = read_job_sidecar(save_path, "literature_evidence.md") or "（尚无检索账本，不得编造书目）"
        ev = _maybe_untrust_sidecar(ev, save_path, "literature_evidence.md", config=config)
        this_prompt = this_prompt.replace("{literature_evidence}", ev)
    if "{actual_metrics}" in this_prompt:
        metrics = read_job_sidecar(save_path, "actual_metrics.json") or _EMPTY_ACTUAL_METRICS
        metrics = _maybe_untrust_sidecar(metrics, save_path, "actual_metrics.json", config=config)
        this_prompt = this_prompt.replace("{actual_metrics}", metrics)
    return this_prompt


def prepend_lab_context(prompt: str, save_path: str) -> str:
    ctx = read_job_sidecar(save_path, "lab_context.json", limit=4000)
    if not ctx:
        return prompt
    ctx = _maybe_untrust_sidecar(ctx, save_path, "lab_context.json")
    head = "Lab context (lab_context.json):\n" + ctx
    gaps = read_job_sidecar(save_path, "reproduce_gaps.md", limit=2000)
    if gaps:
        head += "\n\nGroup reproduce gaps (reproduce_gaps.md):\n" + gaps
    metrics = read_job_sidecar(save_path, "actual_metrics.json", limit=1500)
    if metrics:
        head += "\n\nMeasured metrics (actual_metrics.json). Do not invent numbers beyond this file:\n" + metrics
    return head + "\n\n" + prompt
