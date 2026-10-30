from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.concurrency import HEAVY_STATUSES, keep_user_signal, lab_quota_guard, try_begin_execute, worker_enabled
from fengsuan.app.core.queue import enqueue_job
from fengsuan.app.models.credential import GPUProfile
from fengsuan.app.models.literature import LiteratureFile, LiteratureItem
from fengsuan.app.models.runs import ReproduceAttempt, ReproduceRun, ResearchRun
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.research_engine import count_heavy


def create_reproduce_run(
    db: Session,
    *,
    lab_id: str,
    user_id: str,
    literature_item_id: str | None,
    dataset_path: str = "",
    claimed_metrics: dict | None = None,
    dataset_id: str | None = None,
) -> ReproduceRun:
    from fengsuan.app.services.llm import assert_lab_quota

    settings = get_settings()
    with lab_quota_guard(db, lab_id):
        assert_lab_quota(db, lab_id, "reproduce")
        user_n, lab_n = count_heavy(db, user_id, lab_id)
        if user_n >= settings.heavy_job_per_user:
            raise ValueError("quota")
        if lab_n >= settings.heavy_job_per_lab:
            raise ValueError("quota")
        claimed = dict(claimed_metrics or {})
        if not claimed and literature_item_id:
            from fengsuan.app.services.analysis import claimed_metrics_from_card, load_best_method_card

            _row, card = load_best_method_card(db, literature_item_id)
            filled = claimed_metrics_from_card(card)
            if filled:
                claimed = filled
        run = ReproduceRun(
            lab_id=lab_id,
            requester_id=user_id,
            literature_item_id=literature_item_id,
            dataset_path=dataset_path or "",
            dataset_id=dataset_id,
            claimed_metrics_json=json.dumps(claimed, ensure_ascii=False),
            status="pending",
            current_stage="ingest",
        )
        db.add(run)
        db.flush()
        user_n, lab_n = count_heavy(db, user_id, lab_id)
        if user_n > settings.heavy_job_per_user or lab_n > settings.heavy_job_per_lab:
            db.delete(run)
            db.flush()
            raise ValueError("quota")
        dest = Path(settings.file_root) / "repro" / lab_id / run.id
        dest.mkdir(parents=True, exist_ok=True)
        run.save_path = str(dest)
        enqueue_job(ReproduceRun, run.id)
        emit_activity(
            db,
            lab_id=lab_id,
            actor_id=user_id,
            verb="reproduce.stage",
            object_type="reproduce_run",
            object_id=run.id,
            summary="ingest",
        )
        return run


def _pdf_text(db: Session, item_id: str | None) -> tuple[str, str]:
    if not item_id:
        return "", ""
    item = db.get(LiteratureItem, item_id)
    title = item.title if item else ""
    files = db.query(LiteratureFile).filter(LiteratureFile.item_id == item_id).all() if item else []
    for f in files:
        path = Path(f.file_path or "")
        if path.exists():
            try:
                from fengsuan.app.services.pdfparse import extract_pdf_path

                pages, status, _err = extract_pdf_path(str(path))
                if status == "unusable" or not any((p or "").strip() for p in pages):
                    continue
                return title, "\n".join(pages)
            except Exception:
                continue
    return title, (item.abstract if item else "") or ""


def execute_reproduce_run(db: Session, run: ReproduceRun, *, improve: bool = False, already_claimed: bool = False) -> None:
    from openlens_ai.agents.paper_reproducer.pipeline import compare_metrics, run_until_terminal, try_implement

    if keep_user_signal(db, run):
        return
    if not try_begin_execute(db, run, already_claimed=already_claimed):
        return

    gpu = db.query(GPUProfile).filter(GPUProfile.is_default.is_(True)).first()
    gpu_mode = (gpu.mode if gpu else get_settings().gpu_mode) or "off"
    docker_gpus = (gpu.docker_gpus if gpu else "") or ""
    external_endpoint = (gpu.external_endpoint if gpu else "") or ""
    title, pdf_text = _pdf_text(db, run.literature_item_id)
    method_card = None
    if run.literature_item_id:
        item = db.get(LiteratureItem, run.literature_item_id)
        if item:
            from fengsuan.app.services.analysis import analyze_item, load_best_method_card

            last, parsed = load_best_method_card(db, item.id)
            if last:
                method_card = parsed
            if method_card is None:
                files = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).all()
                if any(f.parse_status != "unusable" for f in files) or item.abstract:
                    try:
                        row = analyze_item(db, item, run.requester_id, "method_card")
                        method_card = json.loads(row.result_json or "{}")
                    except Exception:
                        method_card = None
    try:
        claimed = json.loads(run.claimed_metrics_json or "{}")
    except json.JSONDecodeError:
        claimed = {}
    if not isinstance(claimed, dict):
        claimed = {}
    if not claimed and method_card:
        from fengsuan.app.services.analysis import claimed_metrics_from_card

        filled = claimed_metrics_from_card(method_card)
        if filled:
            claimed = filled
            run.claimed_metrics_json = json.dumps(claimed, ensure_ascii=False)
    if method_card is None and run.method_card_json:
        try:
            parsed = json.loads(run.method_card_json)
            if isinstance(parsed, dict) and (parsed.get("problem") or parsed.get("methods") or parsed.get("title") or parsed.get("source")):
                method_card = parsed
        except json.JSONDecodeError:
            method_card = method_card
    if method_card:
        method_card = dict(method_card)
        method_card.setdefault("title", title)
        method_card["needs_gpu"] = bool(method_card.get("needs_gpu")) or "gpu" in pdf_text.lower() or "cuda" in pdf_text.lower()
    prev_actual: dict = {}
    if improve:
        try:
            loaded = json.loads(run.actual_metrics_json or "{}")
            if isinstance(loaded, dict):
                prev_actual = loaded
        except json.JSONDecodeError:
            prev_actual = {}
    improve_diag = load_improve_diagnosis(run) if improve else {}
    lab_feedback = load_improve_lab_feedback(db, run) if improve else ""
    state = {
        "current_stage": "improve" if improve else "ingest",
        "title": title,
        "pdf_text": pdf_text,
        "dataset_path": run.dataset_path,
        "gpu_mode": gpu_mode,
        "docker_gpus": docker_gpus,
        "external_endpoint": external_endpoint,
        "claimed_metrics": claimed,
        "status": "running",
        "save_path": run.save_path,
        "method_card_json": method_card or {},
        "actual_metrics_json": prev_actual,
        "logs": (run.error or run.gap_report_md or "") if improve else "",
        "lab_feedback": lab_feedback,
        "diagnosis": improve_diag if improve else {},
        "gap": compare_metrics(claimed, prev_actual) if improve else {},
        "implement_fn": try_implement,
    }
    state = run_until_terminal(state)
    method_card = json.dumps(state.get("method_card_json") or {}, ensure_ascii=False)
    actual_metrics = json.dumps(state.get("actual_metrics_json") or {}, ensure_ascii=False)
    gap_report = state.get("gap_report_md") or ""
    dest = Path(run.save_path or "")
    if dest:
        dest.mkdir(parents=True, exist_ok=True)
        if gap_report:
            (dest / "gap_report.md").write_text(gap_report, encoding="utf-8")
        (dest / "method_card.json").write_text(method_card, encoding="utf-8")
        diag = state.get("diagnosis") if isinstance(state.get("diagnosis"), dict) else {}
        if not diag and (state.get("status") or "") == "awaiting_gpu":
            from openlens_ai.agents.paper_reproducer.pipeline import enrich_diagnosis

            diag = enrich_diagnosis(
                {"kind": "gpu", "summary": "管理员未开启 GPU，停止在 awaiting_gpu，不伪造指标。"},
                env=state.get("env") if isinstance(state.get("env"), dict) else {},
            )
        if diag:
            (dest / "diagnosis.json").write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
        (dest / "status.json").write_text(
            json.dumps(
                {
                    "status": state.get("status") or run.status,
                    "error_class": state.get("error_class"),
                    "stage": state.get("current_stage") or run.current_stage,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if keep_user_signal(db, run):
        return
    run.method_card_json = method_card
    run.actual_metrics_json = actual_metrics
    run.gap_report_md = gap_report
    run.current_stage = state.get("current_stage") or run.current_stage
    run.status = state.get("status") or run.status
    run.error_class = state.get("error_class")
    run.error = state.get("error")
    db.add(
        ReproduceAttempt(
            run_id=run.id,
            kind="improve" if improve else "implement",
            status=run.status,
            log_summary=(run.gap_report_md or "")[:500],
            metrics_json=run.actual_metrics_json,
        )
    )
    if run.status == "succeeded":
        verb = "reproduce.succeeded"
    elif run.status == "failed":
        verb = "reproduce.failed"
    elif run.status == "awaiting_gpu":
        verb = "reproduce.awaiting_gpu"
    else:
        verb = "reproduce.stage"
    emit_activity(
        db,
        lab_id=run.lab_id,
        actor_id=run.requester_id,
        verb=verb,
        object_type="reproduce_run",
        object_id=run.id,
        summary=run.current_stage,
    )
    db.commit()


def load_improve_diagnosis(run: ReproduceRun) -> dict:
    """改进一轮必须吃上一轮 diagnosis.json，而不是只带一句笼统 improve。"""
    dest = Path(run.save_path or "")
    blob = dest / "diagnosis.json"
    if blob.is_file():
        try:
            loaded = json.loads(blob.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and (loaded.get("kind") or loaded.get("summary") or loaded.get("next_step")):
                out = dict(loaded)
                if not out.get("summary"):
                    out["summary"] = (run.error or out.get("next_step") or "根据上一轮诊断再实现。")[:500]
                return out
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    return {
        "kind": "improve",
        "summary": (getattr(run, "error", None) or "根据上一轮差距再实现，禁止把声称写入实测。")[:500],
    }


AWAIT_GPU_NEXT = "联系管理员开启 GPU，再点恢复。不会伪造指标。"
NEED_DATA_NEXT = "先登记或绑定数据集，再恢复。不会伪造指标。"
RELEASE_GPU_NEXT = "管理员已开启 GPU，任务已重新入队。不会伪造指标。"
PENDING_NO_WORKER_NEXT = "工人未开，可点恢复当场再跑。不会伪造指标。"
PENDING_QUEUED_NEXT = "已在队列中，等待工人领取。不会伪造指标。"
GPU_RELEASE_NO_WORKER_NEXT = "工人未开，学生需在任务上点恢复当场再跑。不会伪造指标。"
GPU_RELEASE_QUEUED_NEXT = "工人将领取已入队任务。不会伪造指标。"
QUOTA_NEXT = "先结束、取消或恢复当前重型任务后再开新的。不会伪造指标。"


def job_next_step(status: str, error_class: str | None = None, *, kind: str = "research") -> str:
    if status == "awaiting_gpu" or error_class == "awaiting_gpu":
        return AWAIT_GPU_NEXT
    if status == "blocked" or error_class == "need_data":
        return NEED_DATA_NEXT
    if status == "paused":
        return "可在本任务点「恢复」继续。"
    if status in {"pending", "queued"}:
        return PENDING_NO_WORKER_NEXT if not worker_enabled() else PENDING_QUEUED_NEXT
    if status == "failed":
        if kind == "reproduce":
            if error_class == "stub":
                return "本环境未执行实测（测试桩）。禁止把论文声称填成成功结果。有 Docker/GPU 后再改进一轮。"
            if error_class == "no_docker":
                return "本机未检测到可用 Docker。安装并启动后点「改进一轮」。不会伪造指标。"
            if error_class == "no_gpu":
                return "GPU 未就绪或外部投递失败。联系管理员后点「改进一轮」。不会伪造指标。"
            if error_class == "parse":
                return "还没写方法，或实测文件不合规。查看失败原因后点「改进一轮」。不会伪造指标。"
            return "查看失败原因后点「改进一轮」重试。不会伪造指标。"
        return "查看失败原因后点「恢复」重试。不会伪造指标。"
    if status == "cancelled":
        return "任务已取消。"
    return ""


def job_can_pause(status: str) -> bool:
    return status in {"pending", "queued", "running"}


def job_can_cancel(status: str) -> bool:
    return status in {"pending", "queued", "running", "paused", "awaiting_gpu", "blocked"}


def job_can_improve(status: str) -> bool:
    return status not in {"running", "cancelled"}


def ensure_reenter_heavy(db: Session, *, user_id: str, lab_id: str, current_status: str) -> None:
    """已结束任务再入队时占重型槽。已在槽内的等待/运行不重复计。"""
    if current_status in HEAVY_STATUSES:
        return
    from fengsuan.app.core.errors import ClassifiedHTTPException

    settings = get_settings()
    with lab_quota_guard(db, lab_id):
        user_n, lab_n = count_heavy(db, user_id, lab_id)
        if user_n >= settings.heavy_job_per_user or lab_n >= settings.heavy_job_per_lab:
            raise ClassifiedHTTPException(429, "同时运行的重型任务已达上限", "quota", next_step=QUOTA_NEXT)


def job_can_resume(status: str, *, kind: str = "research") -> bool:
    if status == "awaiting_gpu":
        return True
    if status == "blocked":
        return True
    if kind == "research" and status in {"paused", "failed"}:
        return True
    if status in {"pending", "queued"} and not worker_enabled():
        return True
    return False


def job_action_denied(action: str, status: str, *, kind: str = "research") -> str:
    if action == "pause":
        if status == "awaiting_gpu":
            return "当前在等 GPU，无需暂停。开启 GPU 后点恢复。不会伪造指标。"
        if status == "blocked":
            return "当前缺数据，无需暂停。先登记数据集再恢复。不会伪造指标。"
        if status == "paused":
            return "任务已暂停，可点恢复继续。"
        if status == "succeeded":
            return "任务已跑完，无法暂停。"
        if status == "failed":
            return "任务已失败，无法暂停。" + ("可点恢复重试。" if kind == "research" else "可点改进一轮。")
        if status == "cancelled":
            return "任务已取消，无法暂停。"
        return "当前状态无法暂停。"
    if action == "cancel":
        if status in {"succeeded", "failed", "cancelled"}:
            return "任务已结束，无法取消。"
        return "当前状态无法取消。"
    if action == "improve":
        if status == "running":
            return "任务正在运行，请等待完成或取消后再改进。不会伪造指标。"
        if status == "cancelled":
            return "任务已取消，无法改进。"
        return "当前状态无法改进。"
    if status == "running":
        return "任务正在运行，无需恢复。可暂停或取消。"
    if status == "succeeded":
        return "任务已跑完，无法恢复。不会伪造指标。"
    if status == "cancelled":
        return "任务已取消，无法恢复。"
    if status in {"pending", "queued"} and worker_enabled():
        return PENDING_QUEUED_NEXT
    return "当前状态无法恢复。"


def diagnosis_is_live(loaded: dict | None, status: str) -> bool:
    if not isinstance(loaded, dict) or not loaded:
        return False
    kind = str(loaded.get("kind") or loaded.get("error_class") or "")
    if kind in {"gpu", "awaiting_gpu"} and status != "awaiting_gpu":
        return False
    if kind in {"need_data", "blocked"} and status != "blocked":
        return False
    if kind == "paused" and status != "paused":
        return False
    return True


def job_feedback(status: str, error_class: str | None = None, *, kind: str = "research", next_step: str | None = None) -> dict:
    step = next_step if next_step is not None else job_next_step(status, error_class, kind=kind)
    if status == "awaiting_gpu" or error_class == "awaiting_gpu":
        title, tone = "仍在等 GPU", "warning"
    elif status == "blocked" or error_class == "need_data":
        title, tone = "仍缺数据", "warning"
    elif status in {"pending", "queued"}:
        title, tone = ("已在队列", "info") if worker_enabled() else ("可当场再跑", "warning")
    elif status == "running":
        title, tone = "正在运行", "info"
    elif status == "paused":
        title, tone = "已暂停", "info"
    elif status == "succeeded":
        title, tone = "已跑完", "success"
        step = step or "查看结果。不会伪造指标。"
    elif status == "failed":
        title, tone = "仍失败", "error"
    elif status == "cancelled":
        title, tone = "已取消", "info"
    else:
        title, tone = "", "info"
    return {
        "tone": tone,
        "title": title,
        "next_step": step,
        "can_resume": job_can_resume(status, kind=kind),
        "can_pause": job_can_pause(status),
        "can_cancel": job_can_cancel(status),
        "can_improve": kind == "reproduce" and job_can_improve(status),
    }


def gpu_profile_feedback(
    *,
    released_research: int = 0,
    released_reproduce: int = 0,
    pending_research: int = 0,
    pending_reproduce: int = 0,
    awaiting_research: int = 0,
    awaiting_reproduce: int = 0,
) -> dict:
    released_n = int(released_research or 0) + int(released_reproduce or 0)
    pending_n = int(pending_research or 0) + int(pending_reproduce or 0)
    awaiting_n = int(awaiting_research or 0) + int(awaiting_reproduce or 0)
    if released_n:
        if not worker_enabled():
            return {
                "tone": "warning",
                "title": "已重新入队",
                "next_step": GPU_RELEASE_NO_WORKER_NEXT,
                "can_resume": True,
            }
        return {
            "tone": "success",
            "title": "已重新入队",
            "next_step": GPU_RELEASE_QUEUED_NEXT,
            "can_resume": False,
        }
    if awaiting_n:
        return {"tone": "warning", "title": "仍在等 GPU", "next_step": AWAIT_GPU_NEXT, "can_resume": True}
    if pending_n and not worker_enabled():
        return {"tone": "warning", "title": "可当场再跑", "next_step": PENDING_NO_WORKER_NEXT, "can_resume": True}
    return {"tone": "info", "title": "GPU 就绪", "next_step": "", "can_resume": False}


def _waiting_row(*, kind: str, run, title: str) -> dict:
    href = f"/runs/{run.id}" if kind == "research" else f"/reproduces/{run.id}"
    return {
        "kind": kind,
        "id": run.id,
        "lab_id": run.lab_id,
        "href": href,
        "title": (title or "")[:80],
        "status": run.status,
        "error_class": run.error_class,
        "requester_id": getattr(run, "requester_id", None),
        "next_step": job_next_step(run.status, run.error_class, kind=kind),
        "can_resume": job_can_resume(run.status, kind=kind),
        "feedback": job_feedback(run.status, run.error_class, kind=kind),
    }


def awaiting_gpu_items(
    db: Session,
    *,
    lab_id: str | None = None,
    include_blocked: bool = False,
    limit: int = 20,
) -> list[dict]:
    """等待中的具体任务。不是新队列产品。"""
    rq = db.query(ResearchRun).filter(ResearchRun.status == "awaiting_gpu")
    statuses = ["awaiting_gpu"]
    if include_blocked:
        statuses.append("blocked")
    pq = db.query(ReproduceRun).filter(ReproduceRun.status.in_(statuses))
    if lab_id:
        rq = rq.filter(ResearchRun.lab_id == lab_id)
        pq = pq.filter(ReproduceRun.lab_id == lab_id)
    research = rq.order_by(ResearchRun.updated_at.desc()).limit(limit).all()
    reproduce = pq.order_by(ReproduceRun.updated_at.desc()).limit(limit).all()
    lit_ids = [r.literature_item_id for r in reproduce if r.literature_item_id]
    titles: dict[str, str] = {}
    if lit_ids:
        titles = {
            x.id: x.title
            for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    items = [_waiting_row(kind="research", run=r, title=r.question) for r in research]
    items.extend(
        _waiting_row(
            kind="reproduce",
            run=r,
            title=titles.get(r.literature_item_id) or r.current_stage or "复现任务",
        )
        for r in reproduce
    )
    return items[:limit]


def pending_without_worker_counts(db: Session, lab_id: str | None = None, user_id: str | None = None) -> dict[str, int]:
    """工人未开时的已入队计数。工人开启则为 0，不改 awaiting_* 口径。"""
    if worker_enabled():
        return {"pending_research": 0, "pending_reproduce": 0}
    rq = db.query(ResearchRun).filter(ResearchRun.status.in_(("pending", "queued")))
    pq = db.query(ReproduceRun).filter(ReproduceRun.status.in_(("pending", "queued")))
    if lab_id:
        rq = rq.filter(ResearchRun.lab_id == lab_id)
        pq = pq.filter(ReproduceRun.lab_id == lab_id)
    if user_id:
        rq = rq.filter(ResearchRun.requester_id == user_id)
        pq = pq.filter(ReproduceRun.requester_id == user_id)
    return {"pending_research": int(rq.count()), "pending_reproduce": int(pq.count())}


def reproduce_preflight(
    db: Session,
    lab_id: str,
    *,
    literature_item_id: str | None = None,
    dataset_id: str | None = None,
    dataset_path: str | None = None,
) -> dict:
    """创建前诚实诊断。不伪造实测，也不拦创建。"""
    import shutil

    from fengsuan.app.services.analysis import card_quality, load_best_method_card
    from fengsuan.app.services.research_engine import current_gpu_mode

    has_card = False
    quality: dict = {}
    card_href = ""
    if literature_item_id:
        _row, card = load_best_method_card(db, literature_item_id)
        has_card = bool(card)
        quality = card_quality(card) if card else {}
        card_href = f"/literature/{literature_item_id}?panel=card"
    has_dataset = bool(dataset_id or (dataset_path or "").strip())
    gpu_mode = current_gpu_mode(db)
    worker_on = worker_enabled()
    docker_ok = bool(shutil.which("docker"))
    missing: list[dict] = []
    if literature_item_id and not has_card:
        missing.append(
            {
                "key": "method_card",
                "label": "还没写方法",
                "text": "创建仍允许，会从空卡走声称，不标实测。",
                "href": card_href,
            }
        )
    if not has_dataset:
        missing.append(
            {
                "key": "dataset",
                "label": "未选数据集",
                "text": "缺数据会停在 blocked。",
                "href": f"/labs/{lab_id}/datasets",
            }
        )
    if gpu_mode == "off":
        missing.append(
            {
                "key": "gpu",
                "label": "实验算力关闭",
                "text": "创建后会停在等待实验算力，不会伪造指标。",
                "href": f"/labs/{lab_id}/reproduces?status=awaiting_gpu",
                "admin_href": "/admin",
            }
        )
    if not worker_on:
        missing.append(
            {
                "key": "worker",
                "label": "工人未开",
                "text": "已入队不是失败。",
                "href": f"/labs/{lab_id}/reproduces?status=pending",
            }
        )
    if not docker_ok:
        missing.append(
            {
                "key": "docker",
                "label": "本机未见 Docker",
                "text": "无容器时诚实停在 no_docker，不会标成功。",
                "href": f"/labs/{lab_id}/reproduces",
            }
        )
    return {
        "has_method_card": has_card,
        "card_quality": quality,
        "has_dataset": has_dataset,
        "gpu_mode": gpu_mode,
        "worker_on": worker_on,
        "docker_hint": "docker" if docker_ok else "no_docker",
        "missing": missing,
        "awaiting_gpu_href": f"/labs/{lab_id}/reproduces?status=awaiting_gpu",
        "admin_href": "/admin" if gpu_mode == "off" else None,
        "card_href": card_href,
    }


def pending_without_worker_items(db: Session, lab_id: str | None = None, *, limit: int = 12, exclude_ids: set[str] | None = None) -> list[dict]:
    """工人未开时，已入队任务也要能点恢复。不计入 awaiting_gpu 计数。"""
    if worker_enabled():
        return []
    skip = exclude_ids or set()
    rq = db.query(ResearchRun).filter(ResearchRun.status.in_(("pending", "queued")))
    pq = db.query(ReproduceRun).filter(ReproduceRun.status.in_(("pending", "queued")))
    if lab_id:
        rq = rq.filter(ResearchRun.lab_id == lab_id)
        pq = pq.filter(ReproduceRun.lab_id == lab_id)
    research = rq.order_by(ResearchRun.updated_at.desc()).limit(limit).all()
    reproduce = pq.order_by(ReproduceRun.updated_at.desc()).limit(limit).all()
    lit_ids = [r.literature_item_id for r in reproduce if r.literature_item_id]
    titles: dict[str, str] = {}
    if lit_ids:
        titles = {
            x.id: x.title
            for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    items = []
    for r in research:
        if r.id in skip:
            continue
        items.append(_waiting_row(kind="research", run=r, title=r.question))
    for r in reproduce:
        if r.id in skip:
            continue
        items.append(
            _waiting_row(
                kind="reproduce",
                run=r,
                title=titles.get(r.literature_item_id) or r.current_stage or "复现任务",
            )
        )
    return items[:limit]


def _job_kind(type_name: str) -> str | None:
    t = (type_name or "").strip().lower()
    if t in {"research_run", "research"}:
        return "research"
    if t in {"reproduce_run", "reproduce"}:
        return "reproduce"
    return None


def stamp_job_live(db: Session, items: list[dict], *, id_key: str = "id", type_key: str = "type") -> None:
    """用当前作业状态覆盖历史文案。通知/动态仍保留原 verb。"""
    research_ids: list[str] = []
    reproduce_ids: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = _job_kind(str(it.get(type_key) or it.get("object_type") or it.get("kind") or ""))
        oid = it.get(id_key) or it.get("object_id")
        if not oid or not kind:
            continue
        if kind == "research":
            research_ids.append(str(oid))
        else:
            reproduce_ids.append(str(oid))
    research = (
        {r.id: r for r in db.query(ResearchRun).filter(ResearchRun.id.in_(research_ids)).all()} if research_ids else {}
    )
    reproduce = (
        {r.id: r for r in db.query(ReproduceRun).filter(ReproduceRun.id.in_(reproduce_ids)).all()} if reproduce_ids else {}
    )
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = _job_kind(str(it.get(type_key) or it.get("object_type") or it.get("kind") or ""))
        oid = it.get(id_key) or it.get("object_id")
        run = research.get(oid) if kind == "research" else reproduce.get(oid) if kind == "reproduce" else None
        if run is None:
            continue
        it["live_status"] = run.status
        it["can_resume"] = job_can_resume(run.status, kind=kind)
        live_next = job_next_step(run.status, run.error_class, kind=kind)
        verb = it.get("verb") or it.get("content") or ""
        stale_await = verb in {"run.awaiting_gpu", "reproduce.awaiting_gpu"} and run.status != "awaiting_gpu"
        if not it.get("next_step") or stale_await:
            it["next_step"] = live_next
        if not it.get("href"):
            it["href"] = f"/runs/{run.id}" if kind == "research" else f"/reproduces/{run.id}"
        it["feedback"] = job_feedback(run.status, run.error_class, kind=kind, next_step=it.get("next_step") or live_next)


def _json_obj(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def reproduce_evidence(run: ReproduceRun | None) -> dict | None:
    """组内复现证据脊柱。实测为空时不得把声称写成成功。"""
    if run is None:
        return None
    from openlens_ai.agents.paper_reproducer.pipeline import normalize_actual_metrics

    claimed = _json_obj(run.claimed_metrics_json)
    actual = normalize_actual_metrics(_json_obj(run.actual_metrics_json))
    if claimed and actual == claimed:
        actual = {}
    measured = bool(actual)
    diag_kind = ""
    diag_summary = ""
    dest = Path(run.save_path or "")
    blob = dest / "diagnosis.json"
    if blob.is_file():
        try:
            loaded = json.loads(blob.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                diag_kind = str(loaded.get("kind") or "")
                diag_summary = str(loaded.get("summary") or loaded.get("next_step") or "")[:400]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    if not diag_kind:
        if run.status == "awaiting_gpu":
            diag_kind = "gpu"
        elif run.status == "blocked":
            diag_kind = "need_data"
        elif not measured:
            diag_kind = "not_run"
    if run.status == "awaiting_gpu":
        honest = "awaiting"
    elif run.status == "blocked":
        honest = "blocked"
    elif measured:
        honest = "measured"
    else:
        honest = "not_run"
    return {
        "id": run.id,
        "kind": "reproduce",
        "status": run.status,
        "error_class": run.error_class,
        "href": f"/reproduces/{run.id}",
        "next_step": job_next_step(run.status, run.error_class, kind="reproduce"),
        "can_resume": job_can_resume(run.status, kind="reproduce"),
        "can_improve": job_can_improve(run.status),
        "feedback": job_feedback(run.status, run.error_class, kind="reproduce"),
        "claimed_metrics": claimed,
        "actual_metrics": actual,
        "measured": measured,
        "honest": honest,
        "diagnosis_kind": diag_kind,
        "diagnosis_summary": diag_summary,
        "gap_excerpt": (run.gap_report_md or "")[:600],
    }


REPRO_SUCCESS_CLAIMS = (
    "成功完成实验",
    "组内已复现成功",
    "组内已有实测表明",
    "we successfully reproduced",
    "reproduction confirms the claimed",
)


def text_claims_reproduce_success(text: str) -> str:
    blob = text or ""
    lower = blob.lower()
    for phrase in REPRO_SUCCESS_CLAIMS:
        if phrase.lower() in lower:
            return phrase
    return ""


def evidence_is_unmeasured(ev: dict | None) -> bool:
    """尚无实测：有任务、未测、不是等 GPU。缺数据与失败重试都算。"""
    if not isinstance(ev, dict) or not ev:
        return False
    if ev.get("measured"):
        return False
    return ev.get("honest") != "awaiting"


def filter_rows_by_reproduce(rows: list[dict], want: str) -> list[dict]:
    """文献行按诚实状态过滤。不是排名。not_run=尚无实测，含 blocked。"""
    key = (want or "").strip()
    if key == "unmeasured":
        key = "not_run"
    if key not in {"measured", "not_run", "none", "awaiting", "blocked"}:
        return rows
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ev = row.get("latest_reproduce") if isinstance(row.get("latest_reproduce"), dict) else None
        if key == "none":
            if not ev:
                out.append(row)
            continue
        if not ev:
            continue
        if key == "measured" and ev.get("measured"):
            out.append(row)
        elif key == "not_run" and evidence_is_unmeasured(ev):
            out.append(row)
        elif key == "awaiting" and ev.get("honest") == "awaiting":
            out.append(row)
        elif key == "blocked" and ev.get("honest") == "blocked":
            out.append(row)
    return out


def title_mentioned_in(title: str, blob: str) -> bool:
    name = (title or "").strip()
    if len(name) < 6:
        return False
    return name in (blob or "")


def lab_mentioned_literature(db: Session, lab_id: str | None, text: str) -> list[dict]:
    """正文点名的组内题名，附诚实 DTO。短题名不扫，避免误伤。"""
    blob = text or ""
    if not lab_id or not blob:
        return []
    from fengsuan.app.models.literature import LiteratureItem

    rows = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .limit(400)
        .all()
    )
    latest = latest_reproduce_runs(db, [x.id for x in rows])
    names: list[dict] = []
    for it in rows:
        if not title_mentioned_in(it.title or "", blob):
            continue
        ev = reproduce_evidence(latest.get(it.id))
        names.append(
            {
                "key": "",
                "title": it.title,
                "item_id": it.id,
                "measured": bool(ev and ev.get("measured")),
                "honest": (ev or {}).get("honest") or "none",
                "href": (ev or {}).get("href")
                or f"/labs/{lab_id}/reproduces?literature_item_id={it.id}",
            }
        )
    return names


def lab_unmeasured_mentioned(db: Session, lab_id: str | None, text: str) -> list[dict]:
    """正文点名的组内题名，且尚无实测（含尚未开跑）。"""
    return [x for x in lab_mentioned_literature(db, lab_id, text) if not x.get("measured")]


def merge_reproduce_hints(db: Session, manuscript, hints: list[dict] | None, text: str | None = None) -> list[dict]:
    """引用 hints + 正文点名的未测题名。模拟评审与保存警告同一口径。"""
    blob = text if text is not None else (getattr(manuscript, "content_md", None) or "")
    out = [h for h in (hints or []) if isinstance(h, dict)]
    seen = {h.get("item_id") for h in out if h.get("item_id")} | {h.get("title") for h in out if h.get("title")}
    for extra in lab_unmeasured_mentioned(db, getattr(manuscript, "lab_id", None), blob):
        if extra.get("item_id") in seen or extra.get("title") in seen:
            continue
        out.append(extra)
    return out


def manuscript_success_warning(db: Session, manuscript, text: str | None = None) -> str:
    """手稿正文把未测写成成功时的警告，不拦保存。题名点名也算，不只看引用。"""
    blob = text if text is not None else (getattr(manuscript, "content_md", None) or "")
    phrase = text_claims_reproduce_success(blob)
    if not phrase:
        return ""
    from fengsuan.app.services.insight import manuscript_reproduce_hints

    hints = manuscript_reproduce_hints(db, manuscript)
    unmeasured = [h for h in hints if not h.get("measured")]
    mentioned = lab_mentioned_literature(db, getattr(manuscript, "lab_id", None), blob)
    extra = [
        h
        for h in mentioned
        if not h.get("measured") and h.get("title") not in {str(x.get("title") or "") for x in hints}
    ]
    if hints and not unmeasured and not extra:
        return ""
    who = ""
    for hint in unmeasured:
        title, key = hint.get("title") or "", hint.get("key") or ""
        if title_mentioned_in(title, blob) or (key and key in blob):
            who = title or key
            break
    if not who and extra:
        who = extra[0].get("title") or ""
    if not who and unmeasured:
        who = unmeasured[0].get("title") or unmeasured[0].get("key") or ""
    if who:
        return f"正文含「{phrase}」，且「{who}」组内尚无实测。禁止把声称当成功。"
    if unmeasured:
        return f"正文含「{phrase}」，但引用文献组内尚无实测。禁止把声称当成功。"
    if mentioned and all(x.get("measured") for x in mentioned):
        return ""
    return f"正文含「{phrase}」。禁止把论文声称写成组内成功。"


def language_output_warning(
    db: Session, text: str, manuscript_id: str | None = None, lab_id: str | None = None
) -> str:
    """润色/翻译/拓展输出把未测写成成功时的警告，不拦生成。无手稿时仍点名组内题名。"""
    if manuscript_id:
        from fengsuan.app.models.manuscript import Manuscript

        m = db.get(Manuscript, manuscript_id)
        if m:
            return manuscript_success_warning(db, m, text)
    phrase = text_claims_reproduce_success(text)
    if not phrase:
        return ""
    extra = lab_unmeasured_mentioned(db, lab_id, text) if lab_id else []
    if extra:
        return f"输出含「{phrase}」，且「{extra[0].get('title')}」组内尚无实测。禁止把声称当成功。"
    mentioned = lab_mentioned_literature(db, lab_id, text) if lab_id else []
    if mentioned and all(x.get("measured") for x in mentioned):
        return ""
    return f"输出含「{phrase}」。禁止把论文声称写成组内成功。"


def ungrounded_manuscript_ids(db: Session, lab_id: str, manuscript_ids: list[str] | None = None) -> set[str]:
    """现有手稿里正文还没复现就写成成功的 id。不是新对象库。"""
    from fengsuan.app.models.manuscript import Manuscript

    q = db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
    if manuscript_ids is not None:
        keep = [mid for mid in manuscript_ids if mid]
        if not keep:
            return set()
        q = q.filter(Manuscript.id.in_(keep))
    return {m.id for m in q.all() if manuscript_success_warning(db, m)}


def stamp_manuscript_honesty(db: Session, rows: list[dict], *, id_key: str = "id") -> None:
    """现有手稿行打 ungrounded_reproduce 与 unused_research。按这篇正文，不是新对象库。"""
    ids = [r.get(id_key) for r in rows if isinstance(r, dict) and r.get(id_key)]
    if not ids:
        return
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.research_engine import research_unused_brief, scoped_unused_pack
    from fengsuan.app.services.writing import unused_note_from_pack

    found = {
        x.id: x
        for x in db.query(Manuscript).filter(Manuscript.id.in_(ids), Manuscript.deleted_at.is_(None)).all()
    }
    lab_packs: dict[str, dict] = {}
    owner_packs: dict[tuple[str, str], dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        m = found.get(row.get(id_key))
        if not m:
            continue
        row["ungrounded_reproduce"] = manuscript_success_warning(db, m)
        owner = str(m.owner_id or "")
        lab = str(m.lab_id or "")
        key = (lab, owner)
        if key not in owner_packs:
            if lab and lab not in lab_packs:
                lab_packs[lab] = research_unused_brief(db, lab) if lab else {}
            owner_packs[key] = scoped_unused_pack(lab_packs.get(lab) or {}, owner, lab_id=lab) if lab and owner else {}
        row["unused_research"] = unused_note_from_pack(lab, owner_packs[key], m.content_md or "")


def manuscript_reproduce_gaps_sidecar(db: Session, manuscript) -> str:
    """Overleaf zip/sync 的差距 sidecar，禁止写进 main.tex。"""
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.models.manuscript import ManuscriptCitation

    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == manuscript.id).all()
    ids = [c.literature_item_id for c in cites if c.literature_item_id]
    titles: dict[str, str] = {}
    if ids:
        titles = {x.id: x.title for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(ids)).all()}
    for extra in lab_unmeasured_mentioned(db, getattr(manuscript, "lab_id", None), getattr(manuscript, "content_md", None) or ""):
        iid = extra.get("item_id")
        if iid and iid not in titles:
            ids.append(iid)
            titles[iid] = extra.get("title") or iid
    body = compose_reproduce_gap_md(db, ids, titles)
    if not body:
        body = "本组引用文献尚无复现任务。禁止把论文声称当成组内实测。"
    return "# 组内复现差距\n\n" + body


def stamp_notification_reproduce(db: Session, rows: list[dict]) -> None:
    lit = [r for r in rows if isinstance(r, dict) and r.get("object_type") in {"literature", "literature_item"}]
    stamp_latest_reproduce(db, lit, id_key="object_id")
    for row in lit:
        ev = row.get("latest_reproduce") if isinstance(row.get("latest_reproduce"), dict) else None
        if ev:
            row["honest"] = ev.get("honest")
    repro = [
        r
        for r in rows
        if isinstance(r, dict) and r.get("object_type") in {"reproduce", "reproduce_run"}
    ]
    ids = [r.get("object_id") for r in repro if r.get("object_id")]
    if ids:
        found = {x.id: x for x in db.query(ReproduceRun).filter(ReproduceRun.id.in_(ids)).all()}
        for row in repro:
            ev = reproduce_evidence(found.get(row.get("object_id")))
            row["latest_reproduce"] = ev
            if ev:
                row["honest"] = ev.get("honest")
    mss = [r for r in rows if isinstance(r, dict) and r.get("object_type") == "manuscript"]
    if mss:
        stamp_manuscript_honesty(db, mss, id_key="object_id")


def evidence_brief(ev: dict | None) -> str:
    if not isinstance(ev, dict) or not ev:
        return "尚无组内复现任务。禁止把论文声称当成组内实测。"
    if ev.get("measured"):
        return f"已有实测 {json.dumps(ev.get('actual_metrics') or {}, ensure_ascii=False)}；声称 {json.dumps(ev.get('claimed_metrics') or {}, ensure_ascii=False)}。"
    kind = ev.get("diagnosis_kind") or ev.get("error_class") or ev.get("status") or "not_run"
    return f"尚无实测（{kind}）。禁止把论文声称写成成功结果。"


def pick_item_reproduce_run(runs: list) -> object | None:
    """同一文献：有实测取最近实测，否则取最近一次。后一次失败不得盖掉已有实测。"""
    if not runs:
        return None
    for run in runs:
        ev = reproduce_evidence(run) or {}
        if ev.get("measured"):
            return run
    return runs[0]


def latest_reproduce_runs(db: Session, item_ids: list[str]) -> dict[str, ReproduceRun]:
    ids = [iid for iid in item_ids if iid]
    if not ids:
        return {}
    # 每篇最多看 12 次最近尝试就足够选出「最近实测 / 最近一次」，不必把全部历史拉回来。
    found = (
        db.query(ReproduceRun)
        .filter(ReproduceRun.literature_item_id.in_(ids))
        .order_by(ReproduceRun.updated_at.desc())
        .limit(max(60, len(ids) * 12))
        .all()
    )
    grouped: dict[str, list[ReproduceRun]] = {}
    for run in found:
        if run.literature_item_id:
            grouped.setdefault(run.literature_item_id, []).append(run)
    picked: dict[str, ReproduceRun] = {}
    for iid, rs in grouped.items():
        run = pick_item_reproduce_run(rs)
        if run is not None:
            picked[iid] = run
    return picked


def latest_reproduce_evidence(db: Session, item_id: str | None) -> dict | None:
    if not item_id:
        return None
    return reproduce_evidence(latest_reproduce_runs(db, [item_id]).get(item_id))


def stamp_latest_reproduce(db: Session, rows: list[dict], *, id_key: str = "id") -> None:
    ids = [r.get(id_key) for r in rows if isinstance(r, dict) and r.get(id_key)]
    latest = latest_reproduce_runs(db, ids)
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["latest_reproduce"] = reproduce_evidence(latest.get(row.get(id_key)))


def empty_reproduce_spine(lab_id: str | None = None, *, direction_id: str | None = None) -> dict:
    href = f"/labs/{lab_id}/reproduces" if lab_id else ""
    extra = f"direction_id={direction_id}" if (href and direction_id) else ""
    if extra:
        href = f"{href}?{extra}"

    def with_status(st: str) -> str:
        if not lab_id:
            return ""
        base = f"/labs/{lab_id}/reproduces"
        return f"{base}?{extra}&status={st}" if extra else f"{base}?status={st}"

    return {
        "measured": 0,
        "not_run": 0,
        "stub": 0,
        "awaiting": 0,
        "href": href,
        "measured_href": with_status("succeeded"),
        "not_run_href": with_status("failed"),
        "stub_href": with_status("failed"),
        "not_run_titles": [],
        "not_run_title_more": 0,
    }


def not_run_title_chips(
    db: Session | None,
    lab_id: str,
    item_ids: list[str],
    *,
    direction_id: str | None = None,
    cap: int = 8,
) -> tuple[list[dict], int]:
    """未实测文献题名芯片。不是文献调研 unused，也不是排名。"""
    ids: list[str] = []
    seen: set[str] = set()
    for iid in item_ids:
        key = str(iid or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ids.append(key)
    if not ids or db is None or not lab_id:
        return [], 0
    titles = {
        x.id: x.title
        for x in db.query(LiteratureItem)
        .filter(LiteratureItem.id.in_(ids), LiteratureItem.deleted_at.is_(None))
        .all()
    }
    rows: list[dict] = []
    for iid in ids:
        name = str(titles.get(iid) or "").strip()
        if not name:
            continue
        href = f"/labs/{lab_id}/reproduces?literature_item_id={iid}"
        if direction_id:
            href += f"&direction_id={direction_id}"
        rows.append({"title": name, "href": href})
    return rows[:cap], max(0, len(rows) - cap)


def compose_reproduce_gap_md(db: Session, item_ids: list[str], titles: dict[str, str] | None = None) -> str:
    """组内复现差距短文。单独字段，禁止并进 LLM gap_md。"""
    ids: list[str] = []
    seen: set[str] = set()
    for iid in item_ids:
        if iid and iid not in seen:
            seen.add(iid)
            ids.append(iid)
    if not ids:
        return ""
    latest = latest_reproduce_runs(db, ids)
    lines = []
    for iid in ids:
        title = (titles or {}).get(iid) or iid
        lines.append(f"- {title} ({iid}): {evidence_brief(reproduce_evidence(latest.get(iid)))}")
    return "\n".join(lines)


REPRODUCE_NOTE_HEAD = "## 组内复现差距"
COMPARE_NOTE_HEAD = "## 文献对比与差距"


def merge_direction_note(current: str, note: str) -> str:
    """方向说明幂等追加。同标题章节替换，相同全文不重复。"""
    note = (note or "").strip()
    cur = current or ""
    if not note:
        return cur[:20000]
    if note in cur:
        return cur[:20000]
    head = ""
    for line in note.splitlines():
        if line.startswith("## "):
            head = line.strip()
            break
    if head and head in cur:
        pre, _, rest = cur.partition(head)
        nxt = rest.find("\n## ")
        tail = rest[nxt:] if nxt >= 0 else ""
        merged = pre.rstrip()
        if tail:
            merged = (merged + "\n" + tail.lstrip("\n")).strip()
        return (merged + "\n\n" + note).strip()[:20000]
    return (cur.rstrip() + "\n\n" + note).strip()[:20000]


def lab_reproduce_spine(
    db: Session, lab_id: str, item_ids: list[str] | None = None, *, direction_id: str | None = None
) -> dict:
    """进度/统计/方向用的复现诚实计数。不是排名榜。"""
    scoped = item_ids is not None
    keep = [iid for iid in (item_ids or []) if iid] if scoped else None
    if scoped and not keep:
        return empty_reproduce_spine(lab_id, direction_id=direction_id)
    q = db.query(ReproduceRun).filter(ReproduceRun.lab_id == lab_id)
    if keep:
        q = q.filter(ReproduceRun.literature_item_id.in_(keep))
    rows = q.order_by(ReproduceRun.updated_at.desc()).all()
    grouped: dict[str, list[ReproduceRun]] = {}
    orphans: list[ReproduceRun] = []
    for run in rows:
        if run.literature_item_id:
            grouped.setdefault(run.literature_item_id, []).append(run)
        elif not scoped:
            orphans.append(run)
    chosen = [pick_item_reproduce_run(rs) for rs in grouped.values()]
    chosen = [x for x in chosen if x is not None] + orphans
    measured = not_run = stub = awaiting = 0
    leftover_ids: list[str] = []
    for run in chosen:
        ev = reproduce_evidence(run) or {}
        if ev.get("honest") == "awaiting":
            awaiting += 1
        elif ev.get("measured"):
            measured += 1
        else:
            not_run += 1
            if run.literature_item_id:
                leftover_ids.append(run.literature_item_id)
        if ev.get("error_class") == "stub":
            stub += 1
    out = empty_reproduce_spine(lab_id, direction_id=direction_id)
    titles, more = not_run_title_chips(db, lab_id, leftover_ids, direction_id=direction_id)
    out.update(
        {
            "measured": measured,
            "not_run": not_run,
            "stub": stub,
            "awaiting": awaiting,
            "not_run_titles": titles,
            "not_run_title_more": more,
        }
    )
    return out


def reproduce_status_by_item(db: Session, lab_id: str, item_ids: list[str] | None = None) -> dict[str, dict]:
    """一次查出每篇文献的复现诚实状态，供方向分桶，避免每方向再扫一遍。"""
    q = db.query(ReproduceRun).filter(ReproduceRun.lab_id == lab_id)
    if item_ids is not None:
        keep = [iid for iid in item_ids if iid]
        if not keep:
            return {}
        q = q.filter(ReproduceRun.literature_item_id.in_(keep))
    rows = q.order_by(ReproduceRun.updated_at.desc()).all()
    grouped: dict[str, list[ReproduceRun]] = {}
    for run in rows:
        if run.literature_item_id:
            grouped.setdefault(run.literature_item_id, []).append(run)
    out: dict[str, dict] = {}
    for iid, rs in grouped.items():
        run = pick_item_reproduce_run(rs)
        ev = reproduce_evidence(run) or {}
        out[iid] = ev
    return out


def spine_from_item_status(
    lab_id: str,
    item_ids: list[str],
    status_by: dict[str, dict],
    *,
    direction_id: str | None = None,
    db: Session | None = None,
) -> dict:
    measured = not_run = stub = awaiting = 0
    leftover_ids: list[str] = []
    for iid in item_ids:
        ev = status_by.get(iid)
        if not ev:
            continue
        if ev.get("honest") == "awaiting":
            awaiting += 1
        elif ev.get("measured"):
            measured += 1
        else:
            not_run += 1
            leftover_ids.append(iid)
        if ev.get("error_class") == "stub":
            stub += 1
    out = empty_reproduce_spine(lab_id, direction_id=direction_id)
    titles, more = not_run_title_chips(db, lab_id, leftover_ids, direction_id=direction_id)
    out.update(
        {
            "measured": measured,
            "not_run": not_run,
            "stub": stub,
            "awaiting": awaiting,
            "not_run_titles": titles,
            "not_run_title_more": more,
        }
    )
    return out


def load_improve_lab_feedback(db: Session, run: ReproduceRun) -> str:
    """改进一轮可吃组内精读追问与手稿批注，仍禁止把声称当实测。"""
    iid = run.literature_item_id
    if not iid:
        return ""
    parts: list[str] = []
    from fengsuan.app.models.literature import LiteratureAnalysis

    row = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id == iid, LiteratureAnalysis.analysis_type == "tutor")
        .order_by(LiteratureAnalysis.created_at.desc())
        .first()
    )
    if row:
        try:
            payload = json.loads(row.result_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        question = str((payload or {}).get("question") or "").strip()
        if question:
            parts.append("精读追问：" + question[:240])
    from fengsuan.app.models.manuscript import ManuscriptCitation, ManuscriptComment

    ms_ids = [
        mid
        for (mid,) in db.query(ManuscriptCitation.manuscript_id).filter(ManuscriptCitation.literature_item_id == iid).all()
        if mid
    ]
    if ms_ids:
        comment = (
            db.query(ManuscriptComment)
            .filter(ManuscriptComment.manuscript_id.in_(ms_ids))
            .order_by(ManuscriptComment.created_at.desc())
            .first()
        )
        body = str(getattr(comment, "body", "") or "").strip() if comment else ""
        if body:
            parts.append("手稿批注：" + body[:240])
    return "\n".join(parts)[:800]


def lab_queue(db: Session, lab_id: str) -> dict:
    awaiting = (
        db.query(ReproduceRun)
        .filter(ReproduceRun.lab_id == lab_id, ReproduceRun.status == "awaiting_gpu")
        .count()
    )
    blocked = db.query(ReproduceRun).filter(ReproduceRun.lab_id == lab_id, ReproduceRun.status == "blocked").count()
    research_awaiting = (
        db.query(ResearchRun)
        .filter(ResearchRun.lab_id == lab_id, ResearchRun.status == "awaiting_gpu")
        .count()
    )
    return {
        "awaiting_gpu": int(awaiting),
        "blocked": int(blocked),
        "awaiting_gpu_href": f"/labs/{lab_id}/reproduces?status=awaiting_gpu",
        "blocked_href": f"/labs/{lab_id}/reproduces?status=blocked",
        "research_awaiting_gpu": int(research_awaiting),
        "research_awaiting_href": f"/labs/{lab_id}/runs?status=awaiting_gpu",
        "items": _queue_items(db, lab_id),
    }


def _queue_items(db: Session, lab_id: str) -> list[dict]:
    items = awaiting_gpu_items(db, lab_id=lab_id, include_blocked=True, limit=12)
    extra = pending_without_worker_items(db, lab_id, limit=12, exclude_ids={x["id"] for x in items})
    return (items + extra)[:12]


def awaiting_gpu_counts(db: Session) -> dict[str, int]:
    return {
        "awaiting_research": int(db.query(ResearchRun).filter(ResearchRun.status == "awaiting_gpu").count()),
        "awaiting_reproduce": int(db.query(ReproduceRun).filter(ReproduceRun.status == "awaiting_gpu").count()),
    }


def release_awaiting_gpu(db: Session) -> dict[str, int]:
    """管理员开启 GPU 后：等待中的任务回到 pending 并入队。不执行、不伪造指标。"""
    research = db.query(ResearchRun).filter(ResearchRun.status == "awaiting_gpu").all()
    reproduce = db.query(ReproduceRun).filter(ReproduceRun.status == "awaiting_gpu").all()
    for run in research:
        run.status = "pending"
        run.error = None
        run.error_class = None
        enqueue_job(ResearchRun, run.id)
        emit_activity(
            db,
            lab_id=run.lab_id,
            actor_id=run.requester_id,
            verb="run.released",
            object_type="research_run",
            object_id=run.id,
            summary=RELEASE_GPU_NEXT,
        )
    for run in reproduce:
        run.status = "pending"
        run.error = None
        run.error_class = None
        enqueue_job(ReproduceRun, run.id)
        emit_activity(
            db,
            lab_id=run.lab_id,
            actor_id=run.requester_id,
            verb="reproduce.released",
            object_type="reproduce_run",
            object_id=run.id,
            summary=RELEASE_GPU_NEXT,
        )
    db.commit()
    return {"released_research": len(research), "released_reproduce": len(reproduce)}
