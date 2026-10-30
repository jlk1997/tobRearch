from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from fengsuan.app.core.concurrency import worker_enabled
from fengsuan.app.core.queue import enqueue_job
from fengsuan.app.core.deps import get_current_user, is_admin, require_membership
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.safepath import safe_filename
from fengsuan.app.db.session import SessionLocal, get_db
from fengsuan.app.services.activity import cockpit_feed_watermark, cockpit_lab_events, emit_audit, recent_events
from fengsuan.app.models.identity import User
from fengsuan.app.models.runs import ReproduceRun, ResearchRun
from fengsuan.app.schemas.common import ReproduceRunCreate, ResearchRunCreate, RunResumeBody
from fengsuan.app.services.reproduce import (
    QUOTA_NEXT,
    create_reproduce_run,
    diagnosis_is_live,
    execute_reproduce_run,
    ensure_reenter_heavy,
    job_action_denied,
    job_can_cancel,
    job_can_improve,
    job_can_pause,
    job_can_resume,
    job_feedback,
    job_next_step,
    reproduce_evidence,
)
from fengsuan.app.services.research_engine import (
    create_research_run,
    execute_research_run,
    is_resumable,
    list_artifacts,
    read_run_messages,
    resolve_artifact,
    run_unused_actionable,
    sse_interval,
    sse_ticks,
    subgraph_progress_meta,
    unused_query_on,
)

router = APIRouter(tags=["runs"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(gen):
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)


def _attach_injected(data: dict, db: Session | None = None, user_id: str | None = None) -> dict:
    progress = data.get("progress") if isinstance(data.get("progress"), dict) else {}
    inj = progress.get("injected") if isinstance(progress, dict) else None
    lits = data.get("literature") or []
    ds = data.get("dataset")
    if isinstance(inj, dict) and (inj.get("literature") or inj.get("dataset") or inj.get("note")):
        if not inj.get("literature") and lits:
            inj = {**inj, "literature": lits}
        if not inj.get("dataset") and ds:
            inj = {**inj, "dataset": ds}
        data["injected"] = inj
    else:
        used = bool(lits or ds)
        data["injected"] = {
            "literature": lits,
            "dataset": ds,
            "note": (
                "已把组内文献/数据集写入工作目录 lab_context.json，供文献调研引擎阅读。未改 OpenLens 图边。"
                if used
                else "未注入组内文献或数据集。图仍按单机问题运行。"
            ),
        }
    inj_lits = (data.get("injected") or {}).get("literature") or []
    if db is not None and inj_lits:
        from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href

        stamp_items_open_href(db, user_id, inj_lits, id_key="id")
        lab_id = data.get("lab_id") or ""
        stamp_reproduce_href(lab_id, inj_lits, id_key="id")
        from fengsuan.app.services.reproduce import stamp_latest_reproduce

        stamp_latest_reproduce(db, inj_lits, id_key="id")
    return data


def _stamp_run_literature(db: Session, data: dict, user_id: str | None) -> dict:
    from fengsuan.app.services.analysis import first_card_page
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href

    lab_id = data.get("lab_id") or ""
    lits = data.get("literature") or []
    if lits:
        stamp_items_open_href(db, user_id, lits, id_key="id")
        stamp_reproduce_href(lab_id, lits, id_key="id")
        from fengsuan.app.services.reproduce import stamp_latest_reproduce

        stamp_latest_reproduce(db, lits, id_key="id")
    if data.get("literature_item_id"):
        card = data.get("method_card") if isinstance(data.get("method_card"), dict) else {}
        data["source_page"] = first_card_page(card) if card else None
        stamp_items_open_href(db, user_id, [data], id_key="literature_item_id")
        stamp_reproduce_href(lab_id, [data], id_key="literature_item_id")
        from fengsuan.app.services.reproduce import stamp_latest_reproduce

        stamp_latest_reproduce(db, [data], id_key="literature_item_id")
    return data


def _run_dict(r: ResearchRun, *, detail: bool = False) -> dict:
    progress = json.loads(r.progress_json or "{}")
    if not isinstance(progress, dict):
        progress = {}
    if detail:
        meta = subgraph_progress_meta(r.save_path)
        subgraphs = meta["subgraphs"]
        steps_done = meta["steps_done"]
        steps_total = meta["steps_total"]
        progress_source = meta["progress_source"]
    else:
        subgraphs = progress.get("subgraphs") or []
        steps_done = progress.get("steps_done")
        steps_total = progress.get("steps_total")
        progress_source = progress.get("progress_source")
    lit_ids = progress.get("literature_item_ids") or []
    from fengsuan.app.services.research_engine import research_diagnosis

    diagnosis = research_diagnosis(r)
    return {
        "id": r.id,
        "lab_id": r.lab_id,
        "question": r.question,
        "status": r.status,
        "error": r.error,
        "error_class": r.error_class,
        "diagnosis": diagnosis,
        "next_step": diagnosis.get("next_step") or "",
        "suggested_action": diagnosis.get("suggested_action") or "",
        "can_resume": job_can_resume(r.status, kind="research"),
        "can_pause": job_can_pause(r.status),
        "can_cancel": job_can_cancel(r.status),
        "feedback": job_feedback(r.status, r.error_class, kind="research", next_step=diagnosis.get("next_step") or job_next_step(r.status, r.error_class, kind="research")),
        "progress": progress,
        "requester_id": r.requester_id,
        "dataset_id": r.dataset_id,
        "literature_item_ids": lit_ids,
        "domain": r.domain,
        "subgraphs": subgraphs,
        "steps_done": steps_done,
        "steps_total": steps_total,
        "progress_source": progress_source,
        "resumable": is_resumable(r.save_path) if detail or r.status in {"paused", "failed"} else False,
    }


def _repro_dict(r: ReproduceRun) -> dict:
    from fengsuan.app.services.analysis import card_quality, claimed_metrics_from_card, readable_method_card

    card = json.loads(r.method_card_json or "{}")
    if not isinstance(card, dict):
        card = {}
    else:
        card = readable_method_card(card)
    try:
        claimed = json.loads(r.claimed_metrics_json or "{}")
    except json.JSONDecodeError:
        claimed = {}
    if not isinstance(claimed, dict):
        claimed = {}
    filled = claimed_metrics_from_card(card)
    source = ""
    if claimed:
        source = "method_card" if filled and claimed == filled else "user"
    ev = reproduce_evidence(r) or {}
    if card and r.literature_item_id:
        from types import SimpleNamespace

        from fengsuan.app.services.analysis import stamp_analysis_ai

        stamp_analysis_ai(
            card,
            SimpleNamespace(id=r.literature_item_id, lab_id=r.lab_id),
            empty=None,
            result=card,
        )
    return {
        "id": r.id,
        "lab_id": r.lab_id,
        "status": r.status,
        "current_stage": r.current_stage,
        "literature_item_id": r.literature_item_id,
        "gap_report_md": r.gap_report_md,
        "method_card": card,
        "card_quality": card_quality(card) if card else None,
        "actual_metrics": json.loads(r.actual_metrics_json or "{}"),
        "claimed_metrics": claimed,
        "claimed_metrics_source": source,
        "error": r.error,
        "error_class": r.error_class,
        "requester_id": r.requester_id,
        "dataset_id": r.dataset_id,
        "next_step": job_next_step(r.status, r.error_class, kind="reproduce"),
        "can_resume": job_can_resume(r.status, kind="reproduce"),
        "can_pause": job_can_pause(r.status),
        "can_cancel": job_can_cancel(r.status),
        "can_improve": job_can_improve(r.status),
        "feedback": job_feedback(r.status, r.error_class, kind="reproduce"),
        "honest": ev.get("honest"),
        "measured": bool(ev.get("measured")),
    }


def _prefer_pdf_diagnosis(diagnosis: dict, guide: dict) -> dict:
    kind = (guide or {}).get("kind")
    if kind not in {"abstract_only", "ungrounded", "unusable"}:
        return diagnosis if isinstance(diagnosis, dict) else {}
    from openlens_ai.agents.paper_reproducer.pipeline import enrich_diagnosis

    return enrich_diagnosis(
        {
            "kind": "parse",
            "summary": (guide.get("next_step") or guide.get("label") or "请先上传可复制文字版 PDF。")[:500],
        }
    )


def _repro_diagnosis(run: ReproduceRun) -> dict:
    dest = Path(run.save_path or "")
    blob = dest / "diagnosis.json"
    if blob.is_file():
        try:
            loaded = json.loads(blob.read_text(encoding="utf-8"))
            if diagnosis_is_live(loaded, run.status):
                return loaded
        except (OSError, json.JSONDecodeError):
            pass
    from openlens_ai.agents.paper_reproducer.pipeline import enrich_diagnosis

    if run.status == "awaiting_gpu":
        return enrich_diagnosis({"kind": "gpu", "summary": (run.error or "管理员未开启 GPU。")[:500]}, env={"status": "awaiting_gpu"})
    if run.status == "blocked":
        return enrich_diagnosis({"kind": "need_data", "summary": (run.error or "缺少数据")[:500]}, env={"status": "blocked"})
    if (run.error_class or "") == "parse":
        return enrich_diagnosis({"kind": "parse", "summary": (run.error or "还没写方法")[:500]})
    return {}


def _enrich_repro(db: Session, run: ReproduceRun, user_id: str | None = None) -> dict:
    data = _repro_dict(run)
    from fengsuan.app.models.runs import ReproduceAttempt

    attempts = db.query(ReproduceAttempt).filter(ReproduceAttempt.run_id == run.id).order_by(ReproduceAttempt.created_at).all()
    data["attempts"] = [
        {"id": a.id, "kind": a.kind, "status": a.status, "log_summary": a.log_summary} for a in attempts
    ]
    data["artifacts"] = list_artifacts(run.save_path)
    if run.literature_item_id:
        from fengsuan.app.models.literature import LiteratureItem

        lit = db.get(LiteratureItem, run.literature_item_id)
        if lit is not None:
            data["literature_title"] = lit.title
    if run.dataset_id:
        from fengsuan.app.models.dataset import Dataset

        ds = db.get(Dataset, run.dataset_id)
        if ds is not None and ds.deleted_at is None:
            data["dataset"] = {"id": ds.id, "name": ds.name}
    from fengsuan.app.services.analysis import card_empty_state
    from fengsuan.app.models.literature import LiteratureFile

    files = []
    if run.literature_item_id:
        files = db.query(LiteratureFile).filter(LiteratureFile.item_id == run.literature_item_id).all()
    empty = card_empty_state(files=files, card=data.get("method_card"))
    if empty.get("kind"):
        data["guide"] = {
            **empty,
            "href": f"/literature/{run.literature_item_id}?panel=card" if run.literature_item_id else "",
        }
    elif run.status == "blocked":
        data["guide"] = {
            "kind": "need_data",
            "label": "缺少数据",
            "next_step": "请先登记或上传数据集，再发起复现。",
            "href": f"/labs/{run.lab_id}/datasets" if run.lab_id else "",
        }
    elif run.status == "awaiting_gpu":
        data["guide"] = {
            "kind": "awaiting_gpu",
            "label": "等待 GPU",
            "next_step": "请联系管理员开启 GPU。不会伪造指标。",
            "href": "",
        }
    else:
        data["guide"] = {"kind": "", "label": "", "next_step": "", "href": ""}
    data["diagnosis"] = _prefer_pdf_diagnosis(_repro_diagnosis(run), data.get("guide") or {})
    if data.get("claimed_metrics") and data.get("claimed_metrics_source") != "method_card" and run.literature_item_id:
        from fengsuan.app.services.analysis import claimed_metrics_from_card, load_best_method_card

        _row, best = load_best_method_card(db, run.literature_item_id)
        filled = claimed_metrics_from_card(best)
        if filled and data["claimed_metrics"] == filled:
            data["claimed_metrics_source"] = "method_card"
    return _stamp_run_literature(db, data, user_id)


def _link_run(db: Session, data: dict, run: ResearchRun, user_id: str | None = None) -> dict:
    lit_ids = data.get("literature_item_ids") or []
    if lit_ids:
        from fengsuan.app.models.literature import LiteratureItem

        rows = db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        data["literature"] = [{"id": x.id, "title": x.title} for x in rows]
    if run.dataset_id:
        from fengsuan.app.models.dataset import Dataset

        ds = db.get(Dataset, run.dataset_id)
        if ds is not None and ds.deleted_at is None:
            data["dataset"] = {"id": ds.id, "name": ds.name}
    return _attach_injected(_stamp_run_literature(db, data, user_id), db, user_id)


def _enrich_run(db: Session, run: ResearchRun, *, detail: bool = False, user_id: str | None = None) -> dict:
    data = _run_dict(run, detail=detail)
    if detail:
        from fengsuan.app.services.research_engine import read_review_md

        data["messages"] = read_run_messages(run.save_path)
        data["artifacts"] = list_artifacts(run.save_path)
        data["review_md"] = read_review_md(run.save_path)
    return _link_run(db, data, run, user_id=user_id)


def _attach_run_links(db: Session, rows: list[ResearchRun], user_id: str | None = None) -> list[dict]:
    payloads = [_run_dict(r) for r in rows]
    lit_ids: list[str] = []
    for d in payloads:
        lit_ids.extend(d.get("literature_item_ids") or [])
    ds_ids = [r.dataset_id for r in rows if r.dataset_id]
    titles: dict[str, str] = {}
    datasets: dict[str, str] = {}
    if lit_ids:
        from fengsuan.app.models.literature import LiteratureItem

        titles = {
            x.id: x.title
            for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    if ds_ids:
        from fengsuan.app.models.dataset import Dataset

        datasets = {
            x.id: x.name
            for x in db.query(Dataset).filter(Dataset.id.in_(ds_ids), Dataset.deleted_at.is_(None)).all()
        }
    out = []
    for r, data in zip(rows, payloads):
        ids = data.get("literature_item_ids") or []
        data["literature"] = [{"id": i, "title": titles[i]} for i in ids if i in titles]
        if r.dataset_id and r.dataset_id in datasets:
            data["dataset"] = {"id": r.dataset_id, "name": datasets[r.dataset_id]}
        out.append(_attach_injected(_stamp_run_literature(db, data, user_id), db, user_id))
    return out


def _attach_repro_links(db: Session, rows: list[ReproduceRun], user_id: str | None = None) -> list[dict]:
    lit_ids = [r.literature_item_id for r in rows if r.literature_item_id]
    ds_ids = [r.dataset_id for r in rows if r.dataset_id]
    titles: dict[str, str] = {}
    datasets: dict[str, str] = {}
    if lit_ids:
        from fengsuan.app.models.literature import LiteratureItem

        titles = {
            x.id: x.title
            for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    if ds_ids:
        from fengsuan.app.models.dataset import Dataset

        datasets = {
            x.id: x.name
            for x in db.query(Dataset).filter(Dataset.id.in_(ds_ids), Dataset.deleted_at.is_(None)).all()
        }
    out = []
    for r in rows:
        data = _repro_dict(r)
        if r.literature_item_id and r.literature_item_id in titles:
            data["literature_title"] = titles[r.literature_item_id]
        if r.dataset_id and r.dataset_id in datasets:
            data["dataset"] = {"id": r.dataset_id, "name": datasets[r.dataset_id]}
        out.append(_stamp_run_literature(db, data, user_id))
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, out, id_key="literature_item_id")
    for r, data in zip(rows, out):
        empty = data.get("card_empty") if isinstance(data.get("card_empty"), dict) else {}
        if empty.get("kind"):
            data["guide"] = {**empty, "href": data.get("card_href") or ""}
        elif data.get("status") == "blocked":
            data["guide"] = {
                "kind": "need_data",
                "label": "缺少数据",
                "next_step": "请先登记或上传数据集，再发起复现。",
                "href": f"/labs/{data.get('lab_id')}/datasets" if data.get("lab_id") else "",
            }
        elif data.get("status") == "awaiting_gpu":
            data["guide"] = {
                "kind": "awaiting_gpu",
                "label": "等待 GPU",
                "next_step": "请联系管理员开启 GPU。不会伪造指标。",
                "href": "",
            }
        else:
            data["guide"] = {"kind": "", "label": "", "next_step": "", "href": ""}
        data["diagnosis"] = _prefer_pdf_diagnosis(_repro_diagnosis(r), data.get("guide") or {})
    return out


@router.get("/labs/{lab_id}/research-preflight")
def get_research_preflight(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    dataset_id: str | None = None,
    dataset_path: str | None = None,
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import research_preflight

    return research_preflight(db, lab_id, dataset_id=dataset_id, dataset_path=dataset_path, user_id=user.id)


@router.get("/labs/{lab_id}/research-runs")
def list_runs(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    dataset_id: str | None = None,
    status: str | None = None,
    unused: str | None = None,
    direction_id: str | None = None,
    requester_id: str | None = None,
) -> list[dict] | dict:
    membership = require_membership(db, user, lab_id)
    q = db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id)
    if dataset_id:
        q = q.filter(ResearchRun.dataset_id == dataset_id)
    unused_on = unused_query_on(unused)
    status = (status or "").strip() or None
    if unused_on and status:
        from fengsuan.app.services.insight import research_list_empty_next, research_preflight
        from fengsuan.app.services.research_engine import unused_scope_dto

        return {
            "items": [],
            "unused_scope": unused_scope_dto(db, lab_id, requester_id=str(requester_id or "").strip(), direction_id=direction_id or ""),
            "unused_titles": [],
            "unused_title_more": 0,
            "empty_kind": "filter",
            "empty_next": research_list_empty_next(
                lab_id,
                empty_kind="filter",
                status=status,
                dataset_id=dataset_id,
                unused=unused,
                direction_id=direction_id,
                requester_id=str(requester_id or "").strip(),
            ),
            "preflight": research_preflight(db, lab_id, dataset_id=dataset_id, user_id=user.id),
        }
    owner = str(requester_id or "").strip()
    if owner and getattr(membership, "role", "") != "teacher" and owner != user.id:
        owner = user.id
    if owner:
        q = q.filter(ResearchRun.requester_id == owner)
    if unused_on:
        q = q.filter(ResearchRun.status.in_(("failed", "paused")))
    elif status:
        q = q.filter(ResearchRun.status == status)
    scan = 400 if unused_on else 200
    rows = q.order_by(ResearchRun.created_at.desc()).limit(scan).all()
    if unused_on:
        rows = [row for row in rows if run_unused_actionable(row)]
        if direction_id:
            from fengsuan.app.services.directions import resolve_direction_scope
            from fengsuan.app.services.research_engine import requester_ids_for_direction, run_matches_direction

            owners = requester_ids_for_direction(db, lab_id, direction_id)
            scope = resolve_direction_scope(db, lab_id, direction_id)
            rows = [row for row in rows if run_matches_direction(row, direction_id, owners, scope_ids=scope)]
        rows = rows[:80]
    out = _attach_run_links(db, rows, user.id)
    if unused_on:
        from fengsuan.app.services.research_engine import leftover_titles_from_dtos, unused_scope_dto

        titles, more = leftover_titles_from_dtos(out)
        payload = {
            "items": out,
            "unused_scope": unused_scope_dto(db, lab_id, requester_id=owner, direction_id=direction_id or ""),
            "unused_titles": titles,
            "unused_title_more": more,
        }
        if out:
            return payload
        lab_n = db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id).count()
        kind = "library" if lab_n == 0 else "filter"
        from fengsuan.app.services.insight import research_list_empty_next, research_preflight

        payload.update(
            {
                "empty_kind": kind,
                "empty_next": research_list_empty_next(
                    lab_id,
                    empty_kind=kind,
                    status=status,
                    dataset_id=dataset_id,
                    unused=unused,
                    direction_id=direction_id,
                    requester_id=owner,
                ),
                "preflight": research_preflight(db, lab_id, dataset_id=dataset_id, user_id=user.id),
            }
        )
        return payload
    if out:
        return out
    lab_n = db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id).count()
    kind = "library" if lab_n == 0 else "filter"
    from fengsuan.app.services.insight import research_list_empty_next, research_preflight

    return {
        "items": [],
        "empty_kind": kind,
        "empty_next": research_list_empty_next(
            lab_id,
            empty_kind=kind,
            status=status,
            dataset_id=dataset_id,
            unused=unused,
            direction_id=direction_id,
            requester_id=owner,
        ),
        "preflight": research_preflight(db, lab_id, dataset_id=dataset_id, user_id=user.id),
    }


@router.post("/labs/{lab_id}/research-runs")
def create_run(
    lab_id: str,
    body: ResearchRunCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    dataset_path = body.dataset_path
    if body.dataset_id:
        from fengsuan.app.services.datasets import get_lab_dataset, runtime_path

        try:
            ds = get_lab_dataset(db, lab_id, body.dataset_id)
        except LookupError:
            raise HTTPException(404, detail="数据集不存在")
        dataset_path = runtime_path(ds) or dataset_path
    if body.literature_item_ids:
        from fengsuan.app.models.literature import LiteratureItem

        for iid in body.literature_item_ids:
            item = db.get(LiteratureItem, iid)
            if item is None or item.deleted_at is not None or item.lab_id != lab_id:
                raise HTTPException(404, detail="文献不属于当前实验室")
    try:
        run = create_research_run(
            db,
            lab_id=lab_id,
            user_id=user.id,
            question=body.question,
            dataset_path=dataset_path,
            domain=body.domain,
            language=body.language,
            interrupt_after=body.interrupt_after,
            dataset_id=body.dataset_id,
            literature_item_ids=body.literature_item_ids,
        )
    except ValueError:
        raise ClassifiedHTTPException(429, "同时运行的重型任务已达上限", "quota", next_step=QUOTA_NEXT)
    emit_audit(db, user_id=user.id, action="run.start", resource="research_run", resource_id=run.id)
    db.commit()
    db.refresh(run)
    data = _enrich_run(db, run, user_id=user.id)
    from fengsuan.app.services.insight import research_preflight

    data["preflight"] = research_preflight(
        db, lab_id, dataset_id=body.dataset_id, dataset_path=dataset_path, user_id=user.id
    )
    return data


@router.get("/research-runs/{run_id}")
def get_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    require_membership(db, user, run.lab_id)
    return _enrich_run(db, run, detail=True, user_id=user.id)


@router.get("/research-runs/{run_id}/artifacts")
def run_artifacts(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    require_membership(db, user, run.lab_id)
    return {"items": list_artifacts(run.save_path)}


@router.get("/research-runs/{run_id}/artifacts/file")
def run_artifact_file(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    require_membership(db, user, run.lab_id)
    try:
        target = resolve_artifact(run.save_path, path)
    except (FileNotFoundError, ValueError, OSError):
        raise HTTPException(404, detail="工件不存在")
    return FileResponse(target, filename=safe_filename(Path(path).name, "artifact.bin"))


@router.post("/research-runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    membership = require_membership(db, user, run.lab_id)
    if run.requester_id != user.id and membership.role != "teacher":
        raise HTTPException(403, detail="仅发起者或导师可取消")
    n = (
        db.query(ResearchRun)
        .filter(ResearchRun.id == run.id, ResearchRun.status.in_(("pending", "queued", "running", "paused", "awaiting_gpu", "blocked")))
        .update({ResearchRun.status: "cancelled"}, synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    if n != 1 and run.status != "cancelled":
        raise HTTPException(409, detail=job_action_denied("cancel", run.status, kind="research"))
    return _enrich_run(db, run, user_id=user.id)


@router.post("/research-runs/{run_id}/pause")
def pause_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    membership = require_membership(db, user, run.lab_id)
    if run.requester_id != user.id and membership.role != "teacher":
        raise HTTPException(403, detail="仅发起者或导师可暂停")
    n = (
        db.query(ResearchRun)
        .filter(ResearchRun.id == run.id, ResearchRun.status.in_(("pending", "queued", "running")))
        .update({ResearchRun.status: "paused"}, synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    if n != 1 and run.status != "paused":
        raise HTTPException(409, detail=job_action_denied("pause", run.status, kind="research"))
    return _enrich_run(db, run, user_id=user.id)


@router.post("/research-runs/{run_id}/resume")
def resume_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: RunResumeBody | None = None,
) -> dict:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    require_membership(db, user, run.lab_id)
    if run.requester_id != user.id:
        raise HTTPException(403, detail="仅发起者可恢复")
    prev = run.status
    if not job_can_resume(prev, kind="research"):
        raise HTTPException(409, detail=job_action_denied("resume", prev, kind="research"))
    ensure_reenter_heavy(db, user_id=user.id, lab_id=run.lab_id, current_status=prev)
    if body and body.from_subgraph:
        run.resume_from_subgraph = body.from_subgraph
    run.status = "pending"
    run.error = None
    if prev == "awaiting_gpu":
        run.error_class = None
    enqueue_job(ResearchRun, run.id)
    db.commit()
    if not worker_enabled() and prev in {"paused", "awaiting_gpu", "pending", "queued", "failed", "blocked"}:
        execute_research_run(db, run)
        db.refresh(run)
    return _enrich_run(db, run, user_id=user.id)


@router.get("/labs/{lab_id}/reproduce-preflight")
def get_repro_preflight(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    literature_item_id: str | None = None,
    dataset_id: str | None = None,
    dataset_path: str | None = None,
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.services.reproduce import reproduce_preflight

    return reproduce_preflight(
        db,
        lab_id,
        literature_item_id=literature_item_id,
        dataset_id=dataset_id,
        dataset_path=dataset_path,
    )


@router.get("/labs/{lab_id}/reproduce-runs")
def list_repro(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    literature_item_id: str | None = None,
    dataset_id: str | None = None,
    honest: str | None = None,
    direction_id: str | None = None,
) -> list[dict] | dict:
    require_membership(db, user, lab_id)
    q = db.query(ReproduceRun).filter(ReproduceRun.lab_id == lab_id)
    if status:
        q = q.filter(ReproduceRun.status == status)
    if literature_item_id:
        q = q.filter(ReproduceRun.literature_item_id == literature_item_id)
    if dataset_id:
        q = q.filter(ReproduceRun.dataset_id == dataset_id)
    includes_children = False
    if direction_id:
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        includes_children = len(scope) > 1
        if not scope:
            q = q.filter(ReproduceRun.id.in_([]))
        else:
            scoped_items = [
                iid
                for (iid,) in db.query(LiteratureDirectionLink.item_id)
                .filter(LiteratureDirectionLink.direction_id.in_(scope))
                .all()
            ]
            q = q.filter(ReproduceRun.literature_item_id.in_(scoped_items or ["__none__"]))
    rows = q.order_by(ReproduceRun.created_at.desc()).limit(200).all()
    out = _attach_repro_links(db, rows, user.id)
    want = (honest or "").strip()
    if want == "unmeasured":
        want = "not_run"
    if want in {"measured", "not_run", "awaiting", "blocked", "stub"}:
        if want == "stub":
            out = [r for r in out if r.get("error_class") == "stub"]
        elif want == "measured":
            out = [r for r in out if r.get("measured")]
        elif want == "not_run":
            out = [r for r in out if (not r.get("measured")) and r.get("honest") != "awaiting"]
        else:
            out = [r for r in out if r.get("honest") == want]
    from fengsuan.app.services.insight import reproduce_list_empty_next
    from fengsuan.app.services.reproduce import reproduce_preflight

    wrap = bool(literature_item_id) or not out
    if not wrap:
        return out
    lab_n = db.query(ReproduceRun).filter(ReproduceRun.lab_id == lab_id).count()
    kind = "library" if lab_n == 0 else "filter"
    pack: dict = {"items": out}
    if literature_item_id or dataset_id:
        pack["preflight"] = reproduce_preflight(
            db, lab_id, literature_item_id=literature_item_id, dataset_id=dataset_id
        )
    if not out:
        pack["empty_kind"] = kind
        pack["empty_next"] = reproduce_list_empty_next(
            lab_id,
            empty_kind=kind,
            status=status,
            honest=honest,
            literature_item_id=literature_item_id,
            dataset_id=dataset_id,
            direction_id=direction_id,
            includes_children=includes_children,
        )
        pack["includes_children"] = includes_children
    return pack


@router.post("/labs/{lab_id}/reproduce-runs")
def create_repro(
    lab_id: str,
    body: ReproduceRunCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    dataset_path = body.dataset_path
    if body.dataset_id:
        from fengsuan.app.services.datasets import get_lab_dataset, runtime_path

        try:
            ds = get_lab_dataset(db, lab_id, body.dataset_id)
        except LookupError:
            raise HTTPException(404, detail="数据集不存在")
        dataset_path = runtime_path(ds) or dataset_path
    try:
        run = create_reproduce_run(
            db,
            lab_id=lab_id,
            user_id=user.id,
            literature_item_id=body.literature_item_id,
            dataset_path=dataset_path,
            claimed_metrics=body.claimed_metrics,
            dataset_id=body.dataset_id,
        )
    except ValueError:
        raise ClassifiedHTTPException(429, "同时运行的重型任务已达上限", "quota", next_step=QUOTA_NEXT)
    emit_audit(db, user_id=user.id, action="reproduce.start", resource="reproduce_run", resource_id=run.id)
    db.commit()
    db.refresh(run)
    if not worker_enabled():
        execute_reproduce_run(db, run)
        db.refresh(run)
    data = _enrich_repro(db, run, user.id)
    from fengsuan.app.services.reproduce import reproduce_preflight

    data["preflight"] = reproduce_preflight(
        db,
        lab_id,
        literature_item_id=body.literature_item_id,
        dataset_id=body.dataset_id,
        dataset_path=dataset_path,
    )
    return data


@router.get("/reproduce-runs/{run_id}")
def get_repro(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    require_membership(db, user, run.lab_id)
    return _enrich_repro(db, run, user.id)


@router.get("/reproduce-runs/{run_id}/artifacts")
def repro_artifacts(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    require_membership(db, user, run.lab_id)
    return {"items": list_artifacts(run.save_path)}


@router.get("/reproduce-runs/{run_id}/artifacts/file")
def repro_artifact_file(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    require_membership(db, user, run.lab_id)
    try:
        target = resolve_artifact(run.save_path, path)
    except (FileNotFoundError, ValueError, OSError):
        raise HTTPException(404, detail="工件不存在")
    return FileResponse(target, filename=safe_filename(Path(path).name, "artifact.bin"))


@router.post("/reproduce-runs/{run_id}/resume")
def resume_repro(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    require_membership(db, user, run.lab_id)
    if run.requester_id != user.id:
        raise HTTPException(403, detail="仅发起者可恢复")
    if not job_can_resume(run.status, kind="reproduce"):
        raise HTTPException(409, detail=job_action_denied("resume", run.status, kind="reproduce"))
    ensure_reenter_heavy(db, user_id=user.id, lab_id=run.lab_id, current_status=run.status)
    if run.status in {"pending", "queued"} and worker_enabled():
        return _enrich_repro(db, run, user.id)
    run.status = "pending"
    run.error = None
    run.error_class = None
    enqueue_job(ReproduceRun, run.id)
    db.commit()
    if not worker_enabled():
        execute_reproduce_run(db, run)
        db.refresh(run)
    return _enrich_repro(db, run, user.id)


@router.post("/reproduce-runs/{run_id}/improve")
def improve_repro(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    require_membership(db, user, run.lab_id)
    if run.requester_id != user.id:
        raise HTTPException(403, detail="仅发起者可改进")
    if not job_can_improve(run.status):
        raise HTTPException(409, detail=job_action_denied("improve", run.status, kind="reproduce"))
    ensure_reenter_heavy(db, user_id=user.id, lab_id=run.lab_id, current_status=run.status)
    run.status = "pending"
    run.current_stage = "improve"
    db.commit()
    if not worker_enabled():
        execute_reproduce_run(db, run, improve=True)
        db.refresh(run)
    return _enrich_repro(db, run, user.id)


@router.post("/reproduce-runs/{run_id}/cancel")
def cancel_repro(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    membership = require_membership(db, user, run.lab_id)
    if run.requester_id != user.id and membership.role != "teacher":
        raise HTTPException(403, detail="仅发起者或导师可取消")
    n = (
        db.query(ReproduceRun)
        .filter(ReproduceRun.id == run.id, ReproduceRun.status.in_(("pending", "queued", "running", "paused", "awaiting_gpu", "blocked")))
        .update({ReproduceRun.status: "cancelled"}, synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    if n != 1 and run.status != "cancelled":
        raise HTTPException(409, detail=job_action_denied("cancel", run.status, kind="reproduce"))
    return _enrich_repro(db, run, user.id)


_SSE_IDLE = frozenset({"pending", "queued", "paused", "awaiting_gpu", "blocked"})


def _sse_once(kind: str, run_id: str, last: str, last_sig):
    """One database read for a job stream. The wait between reads stays off the worker thread."""
    db = SessionLocal()
    try:
        if kind == "research":
            run = db.get(ResearchRun, run_id)
            if run is None:
                return {"missing": True}
            sig = (run.status, run.updated_at.isoformat() if run.updated_at else "", run.error_class or "")
            terminal = run.status not in {"pending", "queued", "running"}
            if last and run.status in _SSE_IDLE and sig == last_sig:
                payload = last
            else:
                msgs = read_run_messages(run.save_path)
                meta = subgraph_progress_meta(run.save_path, msgs)
                payload = json.dumps(
                    {
                        "status": run.status,
                        "messages": msgs[-30:],
                        "subgraphs": meta["subgraphs"],
                        "steps_done": meta["steps_done"],
                        "steps_total": meta["steps_total"],
                        "progress_source": meta["progress_source"],
                        "error_class": run.error_class,
                        "next_step": job_next_step(run.status, run.error_class, kind="research"),
                        "can_resume": job_can_resume(run.status, kind="research"),
                        "can_pause": job_can_pause(run.status),
                        "can_cancel": job_can_cancel(run.status),
                        "feedback": job_feedback(run.status, run.error_class, kind="research"),
                    },
                    ensure_ascii=False,
                )
            return {"payload": payload, "terminal": terminal, "sig": sig}
        run = db.get(ReproduceRun, run_id)
        if run is None:
            return {"missing": True}
        return {
            "payload": json.dumps(_repro_dict(run), ensure_ascii=False),
            "terminal": run.status not in {"pending", "queued", "running"},
            "sig": last_sig,
        }
    finally:
        db.close()


async def _sse_loop(kind: str, run_id: str, request: Request):
    ticks = sse_ticks()
    interval = sse_interval()
    last = ""
    last_sig = None
    for _ in range(max(1, ticks)):
        if await request.is_disconnected():
            return
        row = await asyncio.to_thread(_sse_once, kind, run_id, last, last_sig)
        if row.get("missing"):
            yield "event: error\ndata: {\"detail\": \"missing\"}\n\n"
            return
        payload = row["payload"]
        last_sig = row.get("sig")
        if payload != last:
            yield f"event: snapshot\ndata: {payload}\n\n"
            last = payload
        if row.get("terminal"):
            yield f"event: done\ndata: {payload}\n\n"
            return
        if interval:
            await asyncio.sleep(interval)
        else:
            yield ": ping\n\n"
    yield f"event: snapshot\ndata: {last or '{}'}\n\n"


def _lab_events_once(lab_id: str, user_id: str, teacher: bool, last_wm: str):
    session = SessionLocal()
    try:
        wm = cockpit_feed_watermark(session, lab_id)
        if last_wm and wm == last_wm:
            return None, wm
        payload = json.dumps(
            cockpit_lab_events(session, lab_id, user_id, limit=12, teacher=teacher),
            ensure_ascii=False,
        )
        return payload, wm
    finally:
        session.close()


async def _lab_event_stream(lab_id: str, user_id: str, teacher: bool, request: Request):
    last = ""
    last_wm = ""
    ticks = sse_ticks()
    interval = sse_interval()
    for _ in range(max(1, ticks)):
        if await request.is_disconnected():
            return
        payload, last_wm = await asyncio.to_thread(_lab_events_once, lab_id, user_id, teacher, last_wm)
        if payload is None:
            yield ": ping\n\n"
        elif payload != last:
            yield f"event: snapshot\ndata: {payload}\n\n"
            last = payload
        else:
            yield ": ping\n\n"
        if interval:
            await asyncio.sleep(interval)


@router.get("/labs/{lab_id}/events/stream")
def lab_events(
    lab_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
):
    mem = require_membership(db, user, lab_id)
    teacher = mem.role == "teacher" or is_admin(db, user)
    user_id = user.id
    _ = direction_id
    db.close()
    return _sse(_lab_event_stream(lab_id, user_id, teacher, request))


@router.get("/research-runs/{run_id}/stream")
def run_stream(
    run_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(404, detail="任务不存在")
    require_membership(db, user, run.lab_id)
    db.close()
    return _sse(_sse_loop("research", run_id, request))


@router.get("/reproduce-runs/{run_id}/stream")
def repro_stream(
    run_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(ReproduceRun, run_id)
    if run is None:
        raise HTTPException(404, detail="复现任务不存在")
    require_membership(db, user, run.lab_id)
    db.close()
    return _sse(_sse_loop("reproduce", run_id, request))
