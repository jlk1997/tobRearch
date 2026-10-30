"""真复现流水线（独立目录，禁止插入 build_graph.py 主线性图）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


STAGES = [
    "ingest",
    "methodCard",
    "envCheck",
    "implement",
    "runExp",
    "compare",
    "diagnose",
    "fix",
    "improve",
    "succeed",
    "blocked",
    "awaiting_gpu",
]


def extract_method_card(pdf_text: str, title: str = "") -> dict[str, Any]:
    """遗留启发式，不在成功主路径调用。无 LiteratureAnalysis 时应诚实失败。"""
    text = (pdf_text or "")[:8000]
    return {
        "title": title,
        "problem": "",
        "algorithm_steps": [],
        "hyperparams": {},
        "datasets": [],
        "metrics": [],
        "claimed_numbers": {},
        "needs_gpu": "gpu" in text.lower() or "cuda" in text.lower(),
        "source_excerpt": text[:500],
    }


def env_check(*, dataset_path: str, gpu_mode: str, needs_gpu: bool) -> dict[str, Any]:
    missing: list[str] = []
    if not (dataset_path or "").strip():
        missing.append("dataset_path")
    if dataset_path and not Path(dataset_path).exists():
        missing.append(f"dataset not found: {dataset_path}")
    status = "ok"
    error_class = None
    if missing:
        status = "blocked"
        error_class = "need_data"
    elif needs_gpu and gpu_mode == "off":
        status = "awaiting_gpu"
        error_class = "awaiting_gpu"
    return {"status": status, "missing": missing, "error_class": error_class, "gpu_mode": gpu_mode}


def compare_metrics(claimed: dict, actual: dict) -> dict[str, Any]:
    rows = []
    for key, val in (claimed or {}).items():
        act = (actual or {}).get(key)
        rel = None
        try:
            if act is not None and float(val) != 0:
                rel = abs(float(act) - float(val)) / abs(float(val))
        except (TypeError, ValueError):
            rel = None
        rows.append({"metric": key, "claimed": val, "actual": act, "rel_error": rel})
    return {"rows": rows}


def is_stub_env() -> bool:
    return os.environ.get("FENGSUN_SKIP_HEAVY") == "1" or os.environ.get("FENGSUN_LLM_STUB") == "1"


def _as_metric_number(val: Any) -> float | int | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if val != val or val in (float("inf"), float("-inf")):
            return None
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            if any(ch in s.lower() for ch in (".", "e")):
                f = float(s)
                if f != f or f in (float("inf"), float("-inf")):
                    return None
                return f
            return int(s)
        except ValueError:
            return None
    return None


def normalize_actual_metrics(raw: Any) -> dict[str, float | int]:
    """小模式：指标名 → 数字。列表/嵌套/非数字整份拒绝。"""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, list):
        out: dict[str, float | int] = {}
        for item in raw:
            if not isinstance(item, dict):
                return {}
            name = item.get("name") or item.get("metric") or item.get("key")
            val = item.get("value") if "value" in item else item.get("actual")
            num = _as_metric_number(val)
            if not str(name or "").strip() or num is None:
                return {}
            out[str(name).strip()] = num
        return out
    if not isinstance(raw, dict):
        return {}
    if len(raw) == 1:
        only_k, only_v = next(iter(raw.items()))
        if only_k in {"actual_metrics", "actual_metrics_json", "metrics"} and isinstance(only_v, (dict, list)):
            return normalize_actual_metrics(only_v)
    skip = {
        "logs",
        "error",
        "claimed",
        "claimed_metrics",
        "diagnosis",
        "method_card",
        "status",
    }
    out: dict[str, float | int] = {}
    for key, val in raw.items():
        if key in skip:
            continue
        if key in {"actual_metrics", "actual_metrics_json", "metrics"} and isinstance(val, (dict, list)):
            inner = normalize_actual_metrics(val)
            if val not in ({}, []) and not inner:
                return {}
            out.update(inner)
            continue
        num = _as_metric_number(val)
        if num is None:
            return {}
        name = str(key).strip()
        if not name:
            return {}
        out[name] = num
    return out


def actual_metrics_illegal(raw: Any) -> bool:
    if raw is None or raw == {} or raw == []:
        return False
    return not bool(normalize_actual_metrics(raw))


def _block_text(block: Any) -> str:
    if isinstance(block, dict):
        text = str(block.get("text") or block.get("summary") or "").strip()
        if text:
            return text
        name = block.get("name") or block.get("metric") or block.get("key")
        if name:
            val = block.get("value") if "value" in block else block.get("claimed")
            return f"{name}={val}" if val is not None and val != "" else str(name).strip()
        items = block.get("items") or block.get("metrics")
        if isinstance(items, list):
            return "；".join(_block_text(x) for x in items if _block_text(x))
        return ""
    if isinstance(block, list):
        return "；".join(_block_text(x) for x in block if _block_text(x))
    if block is None:
        return ""
    return str(block).strip()


def card_implement_brief(card: dict | None) -> str:
    """对齐分析卡 problem / methods / metrics，而不是整卡 JSON 堆砌。"""
    card = card if isinstance(card, dict) else {}
    parts: list[str] = []
    title = str(card.get("title") or "").strip()
    if title:
        parts.append(f"title: {title}")
    for key in ("problem", "methods", "metrics"):
        text = _block_text(card.get(key))
        if text:
            parts.append(f"{key}: {text}")
    if not parts:
        return json.dumps(card, ensure_ascii=False)[:4000]
    return "\n".join(parts)[:4000]


def _error_class_from_exc(exc: BaseException) -> str:
    text = str(exc).lower()
    if any(tok in text for tok in ("cuda", "nvidia", "gpu out of memory", "no gpu")):
        return "no_gpu"
    if "docker" in text:
        return "docker"
    if "json" in text or "parse" in text:
        return "parse"
    return "docker"


def _copied_claimed(claimed: dict | None, actual: dict | None, gap: dict | None = None) -> bool:
    claimed = claimed if isinstance(claimed, dict) else {}
    actual = actual if isinstance(actual, dict) else {}
    if claimed and actual and actual == claimed:
        return True
    rows = [r for r in ((gap or {}).get("rows") or []) if isinstance(r, dict)]
    if rows and all(r.get("actual") is not None and r.get("actual") == r.get("claimed") for r in rows):
        return True
    return False


def _has_measured(claimed: dict | None, actual: dict | None, gap: dict | None = None) -> bool:
    if _copied_claimed(claimed, actual, gap):
        return False
    if isinstance(actual, dict) and actual:
        return True
    return any(isinstance(r, dict) and r.get("actual") is not None for r in ((gap or {}).get("rows") or []))


def fallback_not_run_class(state: dict[str, Any] | None = None) -> str:
    existing = str((state or {}).get("error_class") or "").strip()
    if existing and existing != "docker":
        return existing
    if is_stub_env():
        return "stub"
    if existing:
        return existing
    if not shutil.which("docker"):
        return "no_docker"
    return "docker"


def improve_hint(state: dict[str, Any]) -> str:
    """上一轮诊断 + 日志，供 fix/improve 再实现。不得含「把声称当实测」。"""
    parts: list[str] = []
    diag = state.get("diagnosis") if isinstance(state.get("diagnosis"), dict) else {}
    kind = str(diag.get("kind") or "").strip()
    summary = str(diag.get("summary") or "").strip()
    if kind or summary:
        parts.append(f"{kind}: {summary}".strip(": "))
    nxt = str(diag.get("next_step") or "").strip()
    if nxt:
        parts.append("下一步：" + nxt)
    action = str(diag.get("suggested_action") or "").strip()
    if action:
        parts.append("建议动作：" + action)
    extra = str(state.get("lab_feedback") or "").strip()
    if extra:
        parts.append("组内反馈：" + extra[:800])
    logs = str(state.get("logs") or "")[:800]
    if logs:
        parts.append("上次日志：" + logs)
    gap = state.get("gap") if isinstance(state.get("gap"), dict) else {}
    for row in gap.get("rows") or []:
        if not isinstance(row, dict):
            continue
        parts.append(f"指标 {row.get('metric')}: claimed={row.get('claimed')} actual={row.get('actual')}")
    return "\n".join(parts)[:2000]


def enrich_diagnosis(diagnosis: dict | None, *, env: dict | None = None, gap: dict | None = None) -> dict[str, Any]:
    """给诊断补下一步。不改 kind，避免把等待写成失败。"""
    d = dict(diagnosis) if isinstance(diagnosis, dict) else {}
    kind = str(d.get("kind") or "")
    env = env if isinstance(env, dict) else {}
    gap = gap if isinstance(gap, dict) else {}
    if kind == "gpu" or env.get("status") == "awaiting_gpu":
        action, step = "ask_admin_gpu", "联系管理员开启 GPU，再在本任务点「恢复」。不会伪造指标。"
    elif kind == "parse":
        action, step = "upload_pdf", "回阅读器上传可复制 PDF，或先生成方法卡后再复现。"
    elif env.get("status") == "blocked" or kind == "need_data":
        action, step = "need_data", "先登记或绑定数据集，再恢复复现。"
    elif kind == "code_error":
        action, step = "improve", "根据日志改实现后再跑一轮。禁止把论文声称当作实测。"
    elif kind == "not_run":
        action, step = "improve", "环境就绪后点「改进一轮」补实测。禁止把论文声称填成成功结果。"
    elif kind == "hyperparams_or_data":
        action, step = "improve", "对照指标差距改超参或预处理，再改进一轮。禁止把声称写入实测。"
    elif kind == "improve":
        action, step = "improve", "根据上一轮差距再实现。禁止把论文声称写入实测。"
    else:
        action, step = "none", ""
    env_wins = env.get("status") in {"blocked", "awaiting_gpu"}
    if env_wins or not d.get("suggested_action"):
        d["suggested_action"] = action
    if env_wins or not d.get("next_step"):
        d["next_step"] = step
    if "metric_gaps" not in d:
        gaps = []
        for row in gap.get("rows") or []:
            if not isinstance(row, dict):
                continue
            if row.get("actual") is None or row.get("rel_error") not in (None, 0, 0.0):
                gaps.append(
                    {
                        "metric": row.get("metric"),
                        "claimed": row.get("claimed"),
                        "actual": row.get("actual"),
                        "rel_error": row.get("rel_error"),
                    }
                )
        d["metric_gaps"] = gaps
    return d


def diagnose(gap: dict, logs: str = "", *, claimed: dict | None = None, actual: dict | None = None) -> dict[str, Any]:
    gap = gap if isinstance(gap, dict) else {}
    claimed = claimed if isinstance(claimed, dict) else {}
    actual = actual if isinstance(actual, dict) else {}
    copied = _copied_claimed(claimed, actual, gap)
    measured = _has_measured(claimed, actual, gap)
    if copied or not measured:
        if copied:
            summary = "实测与论文声称完全相同，已拒绝写入。不得填写论文数字。"
        else:
            summary = "尚未得到实测指标，不得填写论文数字。"
        out = {"kind": "not_run", "summary": summary}
    elif logs and ("error" in logs.lower() or "traceback" in logs.lower()):
        out = {"kind": "code_error", "summary": logs[:500]}
    else:
        out = {"kind": "hyperparams_or_data", "summary": "指标存在差距，请检查超参与数据预处理。"}
    return enrich_diagnosis(out, gap=gap)


def write_gap_report(*, method_card: dict, env: dict, gap: dict, diagnosis: dict) -> str:
    lines = [
        "# 复现差距报告",
        "",
        "## 方法卡摘要",
        f"- 标题：{method_card.get('title') or ''}",
        f"- 需要 GPU：{method_card.get('needs_gpu')}",
        "",
        "## 环境检查",
        f"- 状态：{env.get('status')}",
        f"- 缺失：{', '.join(env.get('missing') or []) or '无'}",
        "",
        "## 指标",
    ]
    for row in gap.get("rows") or []:
        lines.append(f"- {row.get('metric')}: claimed={row.get('claimed')} actual={row.get('actual')} rel={row.get('rel_error')}")
    if not gap.get("rows"):
        lines.append("- （无实测，未填写任何声称数字作为 actual）")
    diagnosis = enrich_diagnosis(diagnosis, env=env, gap=gap)
    lines.extend(["", "## 诊断", f"- {diagnosis.get('kind')}: {diagnosis.get('summary')}"])
    if diagnosis.get("next_step"):
        lines.extend(["", "## 下一步", str(diagnosis.get("next_step"))])
    return "\n".join(lines)


def _reject_copied_or_illegal(actual: dict, claimed: dict | None, *, illegal: bool) -> tuple[dict, str | None]:
    claimed = claimed if isinstance(claimed, dict) else {}
    if claimed and actual and actual == claimed:
        return {}, "parse"
    if actual:
        return actual, None
    return {}, "parse" if illegal else None


def implement_prompt(state: dict[str, Any]) -> str:
    """OpenHands 实现提示：对齐方法卡字段，改进必须带上一轮诊断。"""
    brief = card_implement_brief(state.get("method_card_json") if isinstance(state.get("method_card_json"), dict) else {})
    hint = (state.get("improve_hint") or improve_hint(state) or "").strip()
    prompt = (
        "根据方法卡的 problem / methods / metrics 实现并运行实验，"
        "把实测指标写入 workspace/actual_metrics.json（小模式：指标名→数字）。"
        "禁止把论文声称数字当作实测。\n方法卡字段：\n" + brief
    )
    if hint:
        prompt += "\n上一轮诊断（必须针对诊断修改实现，禁止把论文声称数字当作实测）：\n" + hint
    return prompt


def try_implement(state: dict[str, Any]) -> dict[str, Any]:
    """Honest implement. Never copy claimed metrics into actual."""
    if is_stub_env():
        return {"actual_metrics_json": {}, "logs": "本环境未执行实测（stub/CI）。", "error_class": "stub"}
    gpu_mode = (state.get("gpu_mode") or "off").strip()
    external = (state.get("external_endpoint") or "").strip()
    needs_gpu = bool((state.get("method_card_json") or {}).get("needs_gpu"))
    if gpu_mode == "external" and external:
        return _dispatch_external(state, external)
    if gpu_mode == "external" and not external:
        return {"actual_metrics_json": {}, "logs": "外部 GPU 未配置投递地址，无法实测。", "error_class": "no_gpu"}
    if needs_gpu and gpu_mode == "off":
        return {"actual_metrics_json": {}, "logs": "需要 GPU 但当前模式为 off，无法实测。", "error_class": "no_gpu"}
    if not shutil.which("docker"):
        return {"actual_metrics_json": {}, "logs": "未检测到 Docker，无法实测。", "error_class": "no_docker"}
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=8)
        if r.returncode != 0:
            return {"actual_metrics_json": {}, "logs": "Docker 不可用，无法实测。", "error_class": "no_docker"}
    except Exception as exc:
        return {"actual_metrics_json": {}, "logs": f"Docker 检查失败：{exc}"[:300], "error_class": "no_docker"}
    save_path = state.get("save_path") or ""
    docker_gpus = (state.get("docker_gpus") or "").strip()
    if needs_gpu and gpu_mode == "internal" and not docker_gpus:
        return {"actual_metrics_json": {}, "logs": "内部 GPU 未分配设备，无法实测。", "error_class": "no_gpu"}
    prev_vis = os.environ.get("NVIDIA_VISIBLE_DEVICES")
    if gpu_mode == "internal" and docker_gpus:
        os.environ["NVIDIA_VISIBLE_DEVICES"] = docker_gpus
    try:
        from openlens_ai.tools.openhands_adaptor import run_openhands_prompt
        from openlens_ai.utils.config import Config

        cfg = Config(
            dataset_path=state.get("dataset_path") or "",
            save_path=save_path or "outputs/repro_tmp",
            question="reproduce implement",
            domain="general",
        )
        logs = run_openhands_prompt(implement_prompt(state), cfg)
        actual: dict = {}
        illegal = False
        p = Path(cfg.save_path) / "workspace" / "actual_metrics.json"
        if p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = None
                illegal = True
            if loaded is not None:
                illegal = actual_metrics_illegal(loaded)
                actual = normalize_actual_metrics(loaded)
        claimed = state.get("claimed_metrics") or {}
        actual, rejected = _reject_copied_or_illegal(actual, claimed, illegal=illegal)
        log_path = Path(cfg.save_path) / "implement.log"
        try:
            log_path.write_text(str(logs)[:20000], encoding="utf-8")
        except Exception:
            pass
        if actual:
            klass = None
        elif rejected:
            klass = rejected
        else:
            klass = "docker"
        return {
            "actual_metrics_json": actual,
            "logs": str(logs)[:2000],
            "error_class": klass,
        }
    except Exception as exc:
        return {"actual_metrics_json": {}, "logs": str(exc)[:500], "error_class": _error_class_from_exc(exc)}
    finally:
        if gpu_mode == "internal" and docker_gpus:
            if prev_vis is None:
                os.environ.pop("NVIDIA_VISIBLE_DEVICES", None)
            else:
                os.environ["NVIDIA_VISIBLE_DEVICES"] = prev_vis


def _dispatch_external(state: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """POST to admin-configured worker. Never fill actual with claimed."""
    import urllib.error
    import urllib.request

    hint = (state.get("improve_hint") or improve_hint(state) or "").strip()
    payload = json.dumps(
        {
            "dataset_path": state.get("dataset_path") or "",
            "method_card": state.get("method_card_json") or {},
            "save_path": state.get("save_path") or "",
            "improve_hint": hint,
            "claimed_metrics": state.get("claimed_metrics") or {},
            "diagnosis": state.get("diagnosis") if isinstance(state.get("diagnosis"), dict) else {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="ignore")[:8000]
    except Exception as exc:
        return {"actual_metrics_json": {}, "logs": f"external 投递失败：{exc}"[:500], "error_class": "no_gpu"}
    actual: dict = {}
    illegal = False
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            cand = parsed.get("actual_metrics") if "actual_metrics" in parsed else parsed.get("actual_metrics_json")
            if cand is None:
                cand = {k: v for k, v in parsed.items() if k not in {"logs", "error", "diagnosis", "claimed_metrics"}}
            illegal = actual_metrics_illegal(cand)
            actual = normalize_actual_metrics(cand)
            logs = str(parsed.get("logs") or body)[:2000]
        else:
            logs = body[:2000]
            illegal = True
    except json.JSONDecodeError:
        logs = body[:2000]
        illegal = True
    claimed = state.get("claimed_metrics") or {}
    actual, rejected = _reject_copied_or_illegal(actual, claimed, illegal=illegal)
    dest = Path(state.get("save_path") or "")
    if dest:
        try:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "external.log").write_text(logs, encoding="utf-8")
        except Exception:
            pass
    if actual:
        klass = None
    elif rejected:
        klass = rejected
    else:
        klass = "no_gpu"
    return {"actual_metrics_json": actual, "logs": logs, "error_class": klass}


def advance_once(state: dict[str, Any]) -> dict[str, Any]:
    """纯函数状态机一步。implement 可通过 implement_fn 接 OpenHands。"""
    stage = state.get("current_stage") or "ingest"
    if stage == "ingest":
        card = state.get("method_card_json") or {}
        if not card or not (card.get("problem") or card.get("methods") or card.get("title") or card.get("source")):
            state["status"] = "failed"
            state["error_class"] = "parse"
            state["error"] = "无方法卡：请先在阅读器生成分析，或上传可复制 PDF。"
            state["current_stage"] = "methodCard"
            diagnosis = enrich_diagnosis({"kind": "parse", "summary": "主路径不再使用启发式抽卡。"})
            state["diagnosis"] = diagnosis
            state["gap_report_md"] = write_gap_report(
                method_card=card,
                env={"status": "failed", "missing": ["method_card"]},
                gap={"rows": []},
                diagnosis=diagnosis,
            )
            return state
        state["current_stage"] = "methodCard"
        return state
    if stage == "methodCard":
        state["current_stage"] = "envCheck"
        return state
    if stage == "envCheck":
        card = state.get("method_card_json") or {}
        env = env_check(
            dataset_path=state.get("dataset_path") or "",
            gpu_mode=state.get("gpu_mode") or "off",
            needs_gpu=bool(card.get("needs_gpu")),
        )
        state["env"] = env
        if env["status"] == "blocked":
            state["current_stage"] = "blocked"
            state["status"] = "blocked"
            state["error_class"] = env["error_class"]
            diagnosis = enrich_diagnosis(diagnose({}, ""), env=env)
            state["diagnosis"] = diagnosis
            state["gap_report_md"] = write_gap_report(method_card=card, env=env, gap={"rows": []}, diagnosis=diagnosis)
            return state
        if env["status"] == "awaiting_gpu":
            state["current_stage"] = "awaiting_gpu"
            state["status"] = "awaiting_gpu"
            state["error_class"] = "awaiting_gpu"
            diagnosis = enrich_diagnosis(
                {"kind": "gpu", "summary": "管理员未开启 GPU，停止在 awaiting_gpu，不伪造指标。"},
                env=env,
            )
            state["diagnosis"] = diagnosis
            state["gap_report_md"] = write_gap_report(method_card=card, env=env, gap={"rows": []}, diagnosis=diagnosis)
            return state
        state["current_stage"] = "implement"
        return state
    if stage in {"fix", "improve"}:
        env = env_check(
            dataset_path=state.get("dataset_path") or "",
            gpu_mode=state.get("gpu_mode") or "off",
            needs_gpu=bool((state.get("method_card_json") or {}).get("needs_gpu")),
        )
        state["env"] = env
        if env["status"] == "blocked":
            state["current_stage"] = "blocked"
            state["status"] = "blocked"
            state["error_class"] = env["error_class"]
            diagnosis = enrich_diagnosis(diagnose(state.get("gap") or {}, state.get("logs") or ""), env=env, gap=state.get("gap") or {})
            state["diagnosis"] = diagnosis
            state["gap_report_md"] = write_gap_report(
                method_card=state.get("method_card_json") or {},
                env=env,
                gap=state.get("gap") or {},
                diagnosis=diagnosis,
            )
            return state
        if env["status"] == "awaiting_gpu":
            state["current_stage"] = "awaiting_gpu"
            state["status"] = "awaiting_gpu"
            state["error_class"] = "awaiting_gpu"
            diagnosis = enrich_diagnosis(
                {"kind": "gpu", "summary": "管理员未开启 GPU，停止在 awaiting_gpu，不伪造指标。"},
                env=env,
                gap=state.get("gap") or {},
            )
            state["diagnosis"] = diagnosis
            state["gap_report_md"] = write_gap_report(
                method_card=state.get("method_card_json") or {},
                env=env,
                gap=state.get("gap") or {},
                diagnosis=diagnosis,
            )
            return state
        state["improve_hint"] = improve_hint(state)
        state["current_stage"] = "implement"
        return state
    if stage in {"implement", "runExp"}:
        impl: Callable | None = state.get("implement_fn")
        if callable(impl):
            result = impl(state)
        elif state.get("use_openhands"):
            result = try_implement(state)
        else:
            result = {"actual_metrics_json": {}, "logs": "未执行实测（未启用 OpenHands）。", "error_class": "stub"}
        raw_actual = result.get("actual_metrics_json")
        illegal = actual_metrics_illegal(raw_actual)
        actual = normalize_actual_metrics(raw_actual)
        claimed = state.get("claimed_metrics") or {}
        actual, rejected = _reject_copied_or_illegal(actual, claimed, illegal=illegal)
        state["actual_metrics_json"] = actual
        state["logs"] = result.get("logs") or state.get("logs") or ""
        if actual:
            state.pop("error_class", None)
        elif result.get("error_class"):
            state["error_class"] = result.get("error_class")
        elif rejected:
            state["error_class"] = rejected
        state["current_stage"] = "compare"
        return state
    if stage == "compare":
        gap = compare_metrics(state.get("claimed_metrics") or {}, state.get("actual_metrics_json") or {})
        state["gap"] = gap
        state["current_stage"] = "diagnose"
        return state
    if stage == "diagnose":
        claimed = state.get("claimed_metrics") or {}
        actual = state.get("actual_metrics_json") or {}
        diagnosis = diagnose(state.get("gap") or {}, state.get("logs") or "", claimed=claimed, actual=actual)
        state["diagnosis"] = diagnosis
        report = write_gap_report(
            method_card=state.get("method_card_json") or {},
            env=state.get("env") or {},
            gap=state.get("gap") or {},
            diagnosis=diagnosis,
        )
        if "成功完成实验" in report:
            report = report.replace("成功完成实验", "尚未得到实测，不得标成成功")
        state["gap_report_md"] = report
        honest_fail = diagnosis.get("kind") == "not_run" or not actual or _copied_claimed(claimed, actual, state.get("gap") or {})
        if honest_fail:
            state["status"] = "failed"
            state["error_class"] = fallback_not_run_class(state)
            state["error"] = diagnosis.get("summary")
            return state
        state["status"] = "succeeded"
        state["current_stage"] = "succeed"
        return state
    return state


def run_until_terminal(state: dict[str, Any], max_steps: int = 12) -> dict[str, Any]:
    for _ in range(max_steps):
        if state.get("status") in {"blocked", "awaiting_gpu", "failed", "succeeded", "cancelled"}:
            return state
        if state.get("current_stage") in {"blocked", "awaiting_gpu", "succeed"}:
            return state
        prev = json.dumps({k: v for k, v in state.items() if k != "implement_fn"}, sort_keys=True, default=str)
        state = advance_once(state)
        nxt = json.dumps({k: v for k, v in state.items() if k != "implement_fn"}, sort_keys=True, default=str)
        if nxt == prev:
            return state
    return state
