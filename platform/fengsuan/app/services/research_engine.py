from __future__ import annotations

import json
import os
import re
from pathlib import Path

from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.concurrency import HEAVY_STATUSES, keep_user_signal, lab_quota_guard, try_begin_execute
from fengsuan.app.core.queue import enqueue_job
from fengsuan.app.core.safepath import resolve_under
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.activity import emit_activity

ALL_SUBGRAPHS = [
    "literature_reviewer",
    "data_analyzer",
    "supervisor",
    "coder",
    "latex_writer",
    "artifact_publisher",
]


def read_review_md(save_path: str, *, limit: int = 60000) -> str:
    """读作业目录里已写好的综述。没有文件就空，不假装写完。"""
    path = Path(save_path or "") / "workspace" / "literature_review.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n\n（综述较长，这里只显示前一部分。完整文件在工件里。）"
    return text


def read_run_messages(save_path: str) -> list[dict]:
    path = Path(save_path) / "streamlit" / "messages.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


_STEP_NAME = re.compile(r"^step_(\d+)_(.+)$")


def read_state_steps(save_path: str) -> list[tuple[int, str]]:
    """OpenLens 已有约定：states/step_NNNN_name.json，写出即该子图完成。不改图边。"""
    folder = Path(save_path or "") / "states"
    if not folder.is_dir():
        return []
    out: list[tuple[int, str]] = []
    for path in folder.glob("step_*.json"):
        hit = _STEP_NAME.match(path.stem)
        if not hit:
            continue
        try:
            idx = int(hit.group(1))
        except ValueError:
            continue
        out.append((idx, hit.group(2)))
    out.sort()
    return out


def subgraph_progress(save_path: str, msgs: list[dict] | None = None, *, steps: list[tuple[int, str]] | None = None) -> list[dict]:
    found = read_state_steps(save_path) if steps is None else steps
    if found:
        done = {name for _, name in found if name in ALL_SUBGRAPHS}
        current = ""
        for name in ALL_SUBGRAPHS:
            if name not in done:
                current = name
                break
        return [{"name": n, "done": n in done, "current": n == current and n not in done} for n in ALL_SUBGRAPHS]
    msgs = read_run_messages(save_path) if msgs is None else msgs
    done: set[str] = set()
    current = ""
    for m in msgs:
        name = m.get("node_name") or m.get("subgraph") or m.get("node") or ""
        content = str(m.get("content") or "")
        if name in ALL_SUBGRAPHS:
            if "Completed" in content or m.get("type") == "node_update" and "Completed" in content:
                done.add(name)
            elif "Starting" in content:
                current = name
    return [{"name": n, "done": n in done, "current": n == current and n not in done} for n in ALL_SUBGRAPHS]


def subgraph_progress_meta(save_path: str, msgs: list[dict] | None = None) -> dict:
    steps = read_state_steps(save_path)
    items = subgraph_progress(save_path, msgs, steps=steps)
    return {
        "subgraphs": items,
        "steps_done": sum(1 for s in items if s.get("done")),
        "steps_total": len(ALL_SUBGRAPHS),
        "progress_source": "states" if steps else "messages",
    }


def list_artifacts(save_path: str, limit: int = 200) -> list[dict]:
    root = Path(save_path or "")
    if not root.exists() or not root.is_dir():
        return []
    out: list[dict] = []
    skip = {".git", "__pycache__", "llm_calls", "openhands_logs"}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip for part in p.parts):
            continue
        if "tool_calls" in p.parts and p.name != "search_hits.jsonl":
            continue
        rel = p.relative_to(root).as_posix()
        out.append({"path": rel, "size": p.stat().st_size, "name": p.name})
        if len(out) >= limit:
            break
    return out


def resolve_artifact(save_path: str, rel: str) -> Path:
    return resolve_under(save_path, rel)


def is_resumable(save_path: str) -> bool:
    return (Path(save_path) / "latest_state.json").exists()


def sse_ticks() -> int:
    if os.environ.get("FENGSUN_LLM_STUB") == "1" or os.environ.get("FENGSUN_SKIP_HEAVY") == "1":
        return int(os.environ.get("FENGSUN_SSE_TICKS") or "1")
    return int(os.environ.get("FENGSUN_SSE_TICKS") or "900")


def sse_interval() -> float:
    if os.environ.get("FENGSUN_LLM_STUB") == "1" or os.environ.get("FENGSUN_SKIP_HEAVY") == "1":
        return float(os.environ.get("FENGSUN_SSE_INTERVAL") or "0")
    return float(os.environ.get("FENGSUN_SSE_INTERVAL") or "2")


def count_heavy(db: Session, user_id: str, lab_id: str) -> tuple[int, int]:
    from sqlalchemy import case, func, or_

    from fengsuan.app.models.runs import ReproduceRun

    def _pair(model):
        row = (
            db.query(
                func.coalesce(func.sum(case((model.requester_id == user_id, 1), else_=0)), 0),
                func.coalesce(func.sum(case((model.lab_id == lab_id, 1), else_=0)), 0),
            )
            .filter(model.status.in_(HEAVY_STATUSES), or_(model.requester_id == user_id, model.lab_id == lab_id))
            .one()
        )
        return int(row[0] or 0), int(row[1] or 0)

    user_r, lab_r = _pair(ResearchRun)
    user_p, lab_p = _pair(ReproduceRun)
    return user_r + user_p, lab_r + lab_p


def build_lab_injection(db: Session, literature_item_ids: list[str] | None, dataset_id: str | None) -> dict:
    from fengsuan.app.models.dataset import Dataset
    from fengsuan.app.models.literature import LiteratureItem

    lits: list[dict] = []
    for iid in literature_item_ids or []:
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None:
            continue
        from fengsuan.app.services.reproduce import evidence_brief, latest_reproduce_evidence

        ev = latest_reproduce_evidence(db, item.id)
        lits.append(
            {
                "id": item.id,
                "title": item.title,
                "reproduce": ev,
                "reproduce_brief": evidence_brief(ev),
            }
        )
    ds = None
    if dataset_id:
        row = db.get(Dataset, dataset_id)
        if row is not None and row.deleted_at is None:
            ds = {"id": row.id, "name": row.name}
    used = bool(lits or ds)
    return {
        "literature": lits,
        "dataset": ds,
        "note": (
            "已写入作业根目录与 workspace/ 的 lab_context.json，供文献调研引擎阅读。未改 OpenLens 图边。"
            if used
            else "未注入组内文献或数据集。图仍按单机问题运行。"
        ),
    }


def compose_injected_question(question: str, injected: dict | None) -> str:
    """问题旁注要求引用已关联标题；未用须标明。不改图边。"""
    payload = injected if isinstance(injected, dict) else {}
    titles = [str(lit.get("title") or "").strip() for lit in payload.get("literature") or [] if isinstance(lit, dict)]
    titles = [t for t in titles if t]
    ds = payload.get("dataset") if isinstance(payload.get("dataset"), dict) else None
    ds_name = str((ds or {}).get("name") or "").strip()
    lines = [(question or "").strip(), ""]
    if titles or ds_name:
        lines.append("组内已关联对象见工作目录 lab_context.json、linked_objects.md、reproduce_gaps.md，以及 workspace/ 下同名文件。")
        if titles:
            lines.append("必须在文献综述与结论中引用下列已关联文献标题：" + "；".join(titles))
            lines.append("若某一篇未使用，必须在结论中写明「未使用：标题」。不要假装子图已改。")
        if any(isinstance(lit, dict) and lit.get("reproduce_brief") for lit in payload.get("literature") or []):
            lines.append("组内复现差距见 lab_context.json 与 reproduce_gaps.md。仅当 measured=true 才能引用实测；禁止把声称当组内成功。")
        if ds_name:
            lines.append(f"实验须优先使用已关联数据集：{ds_name}。")
    return "\n".join(lines).strip()


_GPU_HINTS = ("gpu", "cuda", "nvidia", "cudnn")


def text_needs_gpu(*parts: str) -> bool:
    blob = " ".join(str(p or "") for p in parts).lower()
    return any(key in blob for key in _GPU_HINTS)


def current_gpu_mode(db: Session) -> str:
    from fengsuan.app.models.credential import GPUProfile

    gpu = db.query(GPUProfile).filter(GPUProfile.is_default.is_(True)).first()
    mode = (gpu.mode if gpu else get_settings().gpu_mode) or "off"
    return str(mode).strip() or "off"


def research_needs_gpu(db: Session, run: ResearchRun, injected: dict | None) -> bool:
    if text_needs_gpu(run.question, run.dataset_path):
        return True
    payload = injected if isinstance(injected, dict) else {}
    for lit in payload.get("literature") or []:
        if not isinstance(lit, dict):
            continue
        if text_needs_gpu(lit.get("title") or ""):
            return True
        iid = lit.get("id")
        if not iid:
            continue
        from fengsuan.app.services.analysis import load_best_method_card

        _row, card = load_best_method_card(db, iid)
        if card.get("needs_gpu") or text_needs_gpu(json.dumps(card, ensure_ascii=False)):
            return True
    return False


def classify_research_failure(exc, *, gpu_mode: str, needs_gpu: bool) -> str:
    klass = getattr(exc, "error_class", None)
    if klass:
        return str(klass)
    msg = str(exc).lower()
    if gpu_mode == "off" and (needs_gpu or text_needs_gpu(msg)):
        return "awaiting_gpu"
    if "docker" in msg:
        return "docker"
    if "quota" in msg:
        return "quota"
    if isinstance(exc, ModuleNotFoundError) or "no module named" in msg:
        return "parse"
    return "llm"


def literature_sidecar(dest: Path) -> dict:
    """作业目录里的检索命中 / 未点名题名。给现有 diagnosis 加字段，不新开接口。"""
    try:
        from openlens_ai.utils.literature_bib import load_search_hits, unused_bib_titles, valid_bib_titles
    except ImportError:
        return {}
    root = Path(dest or "")
    hits = load_search_hits(root)
    titles = []
    unused = []
    bib = root / "workspace" / "literature_review.bib"
    md = root / "workspace" / "literature_review.md"
    if bib.is_file():
        titles = valid_bib_titles(bib.read_text(encoding="utf-8", errors="ignore"))
    blob = md.read_text(encoding="utf-8", errors="ignore") if md.is_file() else ""
    if blob and titles:
        unused = unused_bib_titles(blob, titles)
    hit_titles = [str(h.get("title") or "").strip() for h in hits if str(h.get("title") or "").strip()]
    review_ready = bib.is_file() or md.is_file()
    injected_unused = unused_injected_titles(root, None) if review_ready else []
    return {
        "search_hit_count": len(hits),
        "search_hit_titles": hit_titles[:12],
        "unused_bib_titles": unused[:12],
        "unused_injected_titles": injected_unused[:12],
        "search_hits_path": "tool_calls/search_hits.jsonl" if hits else "",
        "bib_title_count": len(titles),
        "review_ready": review_ready,
    }


def unused_query_on(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "unused"}


def _as_title_list(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        title = str(item or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(title)
    return out


def unused_list_href(lab_id: str, *, requester_id: str = "", direction_id: str = "") -> str:
    href = f"/labs/{lab_id}/runs?unused=1"
    if requester_id:
        href += f"&requester_id={requester_id}"
    if direction_id:
        href += f"&direction_id={direction_id}"
    return href


def unused_scope_dto(db: Session, lab_id: str, *, requester_id: str = "", direction_id: str = "") -> dict:
    """给未点名列表页头写人名 / 方向名，不新开筛选产品。"""
    owner = str(requester_id or "").strip()
    did = str(direction_id or "").strip()
    out = {
        "requester_id": owner,
        "requester_name": "",
        "direction_id": did,
        "direction_name": "",
    }
    if owner:
        from fengsuan.app.models.identity import User

        user = db.get(User, owner)
        out["requester_name"] = str(getattr(user, "display_name", "") or "").strip()
    if did:
        from fengsuan.app.models.lab import ResearchDirection

        row = db.get(ResearchDirection, did)
        if row is not None and row.lab_id == lab_id and row.deleted_at is None:
            out["direction_name"] = str(row.name or "").strip()
    return out


def leftover_titles_from_dtos(rows: list[dict], *, cap: int = 8) -> tuple[list[str], int]:
    titles: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        diag = row.get("diagnosis") if isinstance(row.get("diagnosis"), dict) else {}
        blob = list(diag.get("unused_bib_titles") or []) + list(diag.get("unused_injected_titles") or [])
        blob += list(row.get("unused_bib_titles") or []) + list(row.get("unused_injected_titles") or [])
        for title in blob:
            name = str(title or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            titles.append(name)
    keep = max(1, min(int(cap or 8), 12))
    return titles[:keep], max(0, len(titles) - keep)


def membership_direction_id(db: Session, lab_id: str, user_id: str) -> str:
    """创建时快照用。已归档方向当作未选。"""
    if not lab_id or not user_id:
        return ""
    from fengsuan.app.models.lab import LabMembership, ResearchDirection

    mem = (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
        .first()
    )
    did = str(getattr(mem, "direction_id", None) or "").strip()
    if not did:
        return ""
    row = db.get(ResearchDirection, did)
    if row is None or row.lab_id != lab_id or row.deleted_at is not None:
        return ""
    return did


def hung_scope_literature_ids(db: Session, lab_id: str, user_id: str, *, cap: int = 12) -> tuple[list[str], bool]:
    """文献调研空选时，注入已挂方向（含小课题）文献。核心钉子优先。不改主方向快照。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.services.directions import expand_direction_ids, member_direction_maps

    hung = [row["id"] for row in (member_direction_maps(db, lab_id, [user_id]).get(user_id) or [])]
    scope = expand_direction_ids(db, lab_id, hung)
    includes_children = len(scope) > len({x for x in hung if x})
    if not scope:
        return [], False
    cores: list[str] = []
    rest: list[str] = []
    seen: set[str] = set()
    for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(scope)).all():
        if link.item_id in seen:
            continue
        seen.add(link.item_id)
        if getattr(link, "is_core", False):
            cores.append(link.item_id)
        else:
            rest.append(link.item_id)
    ids = cores + rest
    if not ids:
        return [], includes_children
    live = {
        iid
        for (iid,) in db.query(LiteratureItem.id)
        .filter(LiteratureItem.id.in_(ids), LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .all()
    }
    return [iid for iid in ids if iid in live][: max(1, min(int(cap or 12), 24))], includes_children


def _progress_blob(run) -> dict:
    try:
        prev = json.loads(getattr(run, "progress_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        prev = {}
    return prev if isinstance(prev, dict) else {}


def _diagnosis_blob(progress: dict) -> dict:
    stored = progress.get("diagnosis") if isinstance(progress, dict) else None
    return stored if isinstance(stored, dict) else {}


def run_direction_id(run, progress: dict | None = None) -> str:
    blob = progress if progress is not None else _progress_blob(run)
    did = str(blob.get("direction_id") or "").strip()
    if did:
        return did
    return str(_diagnosis_blob(blob).get("direction_id") or "").strip()


def run_matches_direction(
    run, direction_id: str, owners: set[str] | None = None, scope_ids: list[str] | None = None
) -> bool:
    """只认创建时快照。没有记下当时方向的旧任务，不跟现选定走。父方向可含小课题快照。"""
    allowed = {str(x).strip() for x in (scope_ids or []) if str(x or "").strip()}
    did = str(direction_id or "").strip()
    if did:
        allowed.add(did)
    if not allowed:
        return True
    snap = run_direction_id(run)
    return bool(snap and snap in allowed)


def _disk_review_ready(dest: Path) -> bool:
    return (dest / "workspace" / "literature_review.md").is_file() or (
        dest / "workspace" / "literature_review.bib"
    ).is_file()


def run_review_ready(run_or_dest, *, progress: dict | None = None) -> bool:
    """综述 md/bib 已落盘才谈未点名。已落盘 review_ready 则不再扫作业目录。"""
    if not isinstance(run_or_dest, (str, Path)) and getattr(run_or_dest, "progress_json", None) is not None:
        blob = progress if progress is not None else _progress_blob(run_or_dest)
        diag = _diagnosis_blob(blob)
        if "review_ready" in diag:
            return bool(diag.get("review_ready"))
        dest = Path(getattr(run_or_dest, "save_path", None) or "")
        return _disk_review_ready(dest) if dest else False
    dest = Path(getattr(run_or_dest, "save_path", None) or run_or_dest or "")
    return _disk_review_ready(dest)


def _empty_unused_sidecar() -> dict:
    return {
        "search_hit_count": 0,
        "search_hit_titles": [],
        "unused_bib_titles": [],
        "unused_injected_titles": [],
        "search_hits_path": "",
        "bib_title_count": 0,
        "review_ready": False,
    }


def _write_unused_diagnosis_keys(run, *, ready: bool | None = None) -> None:
    dest = Path(getattr(run, "save_path", None) or "")
    if ready is None:
        ready = run_review_ready(dest)
    extra = literature_sidecar(dest) if ready else _empty_unused_sidecar()
    extra["review_ready"] = bool(ready)
    try:
        prev = json.loads(getattr(run, "progress_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        prev = {}
    if not isinstance(prev, dict):
        prev = {}
    diag = prev.get("diagnosis") if isinstance(prev.get("diagnosis"), dict) else {}
    prev["diagnosis"] = {**diag, **extra}
    run.progress_json = json.dumps(prev, ensure_ascii=False)


def persist_unused_diagnosis(run, db=None, *, ready: bool | None = None) -> None:
    """Write unused keys into progress_json.diagnosis so home/stats never rescan disk."""
    if db is not None and getattr(run, "id", None):
        from fengsuan.app.core.concurrency import row_write_guard

        with row_write_guard(db, type(run), run.id) as locked:
            if locked is None:
                return
            _write_unused_diagnosis_keys(locked, ready=ready)
            run.progress_json = locked.progress_json
            db.flush()
            return
    _write_unused_diagnosis_keys(run, ready=ready)


def unused_view(run) -> dict:
    """Parse progress_json once. Prefer persisted unused / review_ready keys."""
    key = getattr(run, "progress_json", None)
    cached = getattr(run, "_fs_unused_view", None)
    if isinstance(cached, tuple) and cached[0] == key:
        return cached[1]
    progress = _progress_blob(run)
    ready = run_review_ready(run, progress=progress)
    direction_id = run_direction_id(run, progress)
    bib: list[str] = []
    inj: list[str] = []
    if ready:
        stored = _diagnosis_blob(progress)
        has_keys = "unused_bib_titles" in stored or "unused_injected_titles" in stored
        if has_keys:
            bib = _as_title_list(stored.get("unused_bib_titles"))
            inj = _as_title_list(stored.get("unused_injected_titles"))
        else:
            dest = Path(getattr(run, "save_path", None) or "")
            extra = literature_sidecar(dest) if dest else _empty_unused_sidecar()
            bib = _as_title_list(extra.get("unused_bib_titles"))
            inj = _as_title_list(extra.get("unused_injected_titles"))
    view = {
        "ready": ready,
        "unused_bib_titles": bib,
        "unused_injected_titles": inj,
        "direction_id": direction_id,
    }
    try:
        run._fs_unused_view = (key, view)
    except Exception:
        pass
    return view


def run_unused_titles(run) -> dict:
    """Prefer persisted diagnosis keys (even empty). Sidecar only when keys are absent."""
    view = unused_view(run)
    return {
        "unused_bib_titles": view["unused_bib_titles"],
        "unused_injected_titles": view["unused_injected_titles"],
    }


def run_has_unused(run) -> bool:
    pack = run_unused_titles(run)
    return bool(pack["unused_bib_titles"] or pack["unused_injected_titles"])


def stamp_notification_unused(db: Session, rows: list[dict]) -> None:
    """通知条带上该任务未点名题名；跳转仍是任务页，芯片进带快照方向的未点名列表。"""
    ids = [
        row.get("object_id")
        for row in rows or []
        if isinstance(row, dict) and row.get("object_type") in {"research_run", "research"} and row.get("object_id")
    ]
    if not ids:
        return
    found = {row.id: row for row in db.query(ResearchRun).filter(ResearchRun.id.in_(ids)).all()}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        run = found.get(row.get("object_id"))
        if run is None or not run_unused_actionable(run):
            continue
        pack = run_unused_titles(run)
        titles = [str(t).strip() for t in list(pack.get("unused_bib_titles") or []) + list(pack.get("unused_injected_titles") or []) if str(t or "").strip()]
        if not titles:
            continue
        did = run_direction_id(run)
        row["unused_titles"] = titles[:8]
        row["unused_title_more"] = max(0, len(titles) - 8)
        row["unused_research_href"] = unused_list_href(run.lab_id, direction_id=did)


def run_unused_actionable(run) -> bool:
    """Only failed/paused jobs that already wrote a review and still have unused titles."""
    status = str(getattr(run, "status", "") or "")
    if status not in {"failed", "paused"}:
        return False
    view = unused_view(run)
    return bool(view["ready"] and (view["unused_bib_titles"] or view["unused_injected_titles"]))


def research_unused_brief(
    db: Session,
    lab_id: str,
    *,
    requester_id: str | None = None,
    direction_id: str | None = None,
    limit: int = 40,
) -> dict:
    """Failed/paused-after-review jobs that still have unused titles. Count jobs, not people."""
    from fengsuan.app.services.directions import resolve_direction_scope

    did = str(direction_id or "").strip()
    scope = resolve_direction_scope(db, lab_id, did) if did else []
    if did and not scope:
        empty = unused_list_href(lab_id, requester_id=requester_id or "", direction_id=did)
        return {
            "count": 0,
            "unused_research": 0,
            "href": empty,
            "unused_research_href": empty,
            "run_id": "",
            "titles": [],
            "title_more": 0,
            "by_requester": {},
            "items": [],
            "scanned_items": [],
            "empty": True,
            "direction_id": did,
        }
    q = db.query(ResearchRun).filter(
        ResearchRun.lab_id == lab_id,
        ResearchRun.status.in_(("failed", "paused")),
    )
    if requester_id:
        q = q.filter(ResearchRun.requester_id == requester_id)
    cap = max(1, min(int(limit or 40), 80))
    scan = min(400, max(80, cap * 8))
    rows = q.order_by(ResearchRun.created_at.desc()).limit(scan).all()
    items: list[dict] = []
    by_requester: dict[str, dict] = {}
    titles: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not run_unused_actionable(row):
            continue
        if scope and (run_direction_id(row) or "") not in scope:
            continue
        view = unused_view(row)
        href = f"/runs/{row.id}"
        items.append(
            {
                "id": row.id,
                "requester_id": row.requester_id,
                "href": href,
                "direction_id": view.get("direction_id") or "",
                "unused_bib_titles": view["unused_bib_titles"][:8],
                "unused_injected_titles": view["unused_injected_titles"][:8],
            }
        )
        for title in list(view["unused_bib_titles"] or []) + list(view["unused_injected_titles"] or []):
            name = str(title or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            titles.append(name)
        bucket = by_requester.setdefault(
            row.requester_id,
            {
                "count": 0,
                "href": unused_list_href(lab_id, requester_id=row.requester_id),
                "run_id": row.id,
            },
        )
        bucket["count"] += 1
    shown = items[:cap]
    first = shown[0] if shown else None
    list_href = unused_list_href(lab_id, requester_id=requester_id or "", direction_id=did)
    href = ((first or {}).get("href") or list_href) if requester_id else unused_list_href(lab_id, direction_id=did)
    return {
        "count": len(items),
        "unused_research": len(items),
        "href": href,
        "unused_research_href": list_href,
        "run_id": (first or {}).get("id") or "",
        "titles": titles[:8],
        "title_more": max(0, len(titles) - 8),
        "by_requester": by_requester,
        "items": shown,
        "scanned_items": items,
        "empty": not items,
        "direction_id": did,
    }


def scoped_unused_pack(pack: dict | None, requester_id: str, *, lab_id: str = "") -> dict:
    """从一次实验室扫描切出某人的未点名，避免手稿列表每人再查一遍。"""
    pack = pack if isinstance(pack, dict) else {}
    rid = str(requester_id or "").strip()
    source = pack.get("scanned_items") or pack.get("items") or []
    items = [item for item in source if isinstance(item, dict) and str(item.get("requester_id") or "") == rid]
    first = items[0] if items else None
    titles: list[str] = []
    seen: set[str] = set()
    for item in items:
        for title in list(item.get("unused_bib_titles") or []) + list(item.get("unused_injected_titles") or []):
            name = str(title or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            titles.append(name)
    href = unused_list_href(lab_id, requester_id=rid) if lab_id and rid else ((first or {}).get("href") or pack.get("href") or "")
    return {
        "count": len(items),
        "unused_research": len(items),
        "href": href,
        "unused_research_href": href,
        "run_id": (first or {}).get("id") or "",
        "titles": titles[:8],
        "title_more": max(0, len(titles) - 8),
        "items": items[:40],
        "scanned_items": items,
        "by_requester": {rid: (pack.get("by_requester") or {}).get(rid) or {"count": len(items), "href": href}},
        "empty": not items,
    }


def requester_ids_by_direction(db: Session, lab_id: str, direction_ids: list[str]) -> dict[str, set[str]]:
    """一次查出各方向成员：选定该方向，或有手稿挂在该方向。"""
    ids = [str(did).strip() for did in direction_ids if str(did or "").strip()]
    out: dict[str, set[str]] = {did: set() for did in ids}
    if not lab_id or not ids:
        return out
    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.services.directions import direction_member_ids

    linked = direction_member_ids(db, lab_id, ids)
    for did, uids in linked.items():
        out.setdefault(did, set()).update(uids)
    for row in (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.direction_id.in_(ids))
        .all()
    ):
        if row.user_id and row.direction_id in out:
            out[row.direction_id].add(row.user_id)
    links = (
        db.query(ManuscriptDirectionLink)
        .filter(ManuscriptDirectionLink.direction_id.in_(ids))
        .all()
    )
    ms_ids = [row.manuscript_id for row in links if row.manuscript_id]
    owners_by_ms: dict[str, str] = {}
    if ms_ids:
        for row in (
            db.query(Manuscript)
            .filter(Manuscript.id.in_(ms_ids), Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
            .all()
        ):
            if row.owner_id:
                owners_by_ms[row.id] = row.owner_id
    for link in links:
        oid = owners_by_ms.get(link.manuscript_id)
        if oid and link.direction_id in out:
            out[link.direction_id].add(oid)
    return out


def requester_ids_for_direction(db: Session, lab_id: str, direction_id: str) -> set[str]:
    """选定该方向，或有手稿挂在该方向的人。大方向含下面的小课题成员。"""
    did = str(direction_id or "").strip()
    if not did:
        return set()
    from fengsuan.app.services.directions import resolve_direction_scope

    scope = resolve_direction_scope(db, lab_id, did) or [did]
    mapped = requester_ids_by_direction(db, lab_id, scope)
    uids: set[str] = set()
    for bucket in mapped.values():
        uids |= bucket
    return uids


def direction_unused_counts(db: Session, lab_id: str, direction_ids: list[str]) -> dict[str, dict]:
    """综述没写到的任务只记到创建时快照方向。没快照的旧任务不进方向数字。"""
    ids = [str(did) for did in direction_ids if str(did or "").strip()]
    out = {
        did: {
            "unused_research": 0,
            "unused_research_href": f"/labs/{lab_id}/runs?unused=1&direction_id={did}",
            "unused_titles": [],
            "unused_title_more": 0,
        }
        for did in ids
    }
    if not ids:
        return out
    pack = research_unused_brief(db, lab_id, limit=80)
    source = pack.get("scanned_items") or pack.get("items") or []
    titles_by: dict[str, list[str]] = {did: [] for did in ids}
    seen_by: dict[str, set[str]] = {did: set() for did in ids}
    for item in source:
        if not isinstance(item, dict):
            continue
        snap = str(item.get("direction_id") or "").strip()
        if snap not in out:
            continue
        out[snap]["unused_research"] = int(out[snap]["unused_research"]) + 1
        for title in list(item.get("unused_bib_titles") or []) + list(item.get("unused_injected_titles") or []):
            name = str(title or "").strip()
            if not name or name in seen_by[snap]:
                continue
            seen_by[snap].add(name)
            titles_by[snap].append(name)
    for did in ids:
        keep = titles_by[did]
        out[did]["unused_titles"] = keep[:8]
        out[did]["unused_title_more"] = max(0, len(keep) - 8)
    return out


def literature_sidecar_for_run(run) -> dict:
    """Trust persisted diagnosis keys. Sidecar only when unused/hit keys are absent."""
    try:
        progress = json.loads(getattr(run, "progress_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        progress = {}
    stored = progress.get("diagnosis") if isinstance(progress, dict) else None
    has_keys = isinstance(stored, dict) and (
        "unused_bib_titles" in stored
        or "unused_injected_titles" in stored
        or "search_hit_count" in stored
    )
    if has_keys:
        titles = run_unused_titles(run)
        return {
            "search_hit_count": int(stored.get("search_hit_count") or 0),
            "search_hit_titles": _as_title_list(stored.get("search_hit_titles")),
            "unused_bib_titles": titles["unused_bib_titles"][:12],
            "unused_injected_titles": titles["unused_injected_titles"][:12],
            "search_hits_path": str(stored.get("search_hits_path") or ""),
            "bib_title_count": int(stored.get("bib_title_count") or 0),
        }
    dest = Path(getattr(run, "save_path", None) or "")
    return literature_sidecar(dest) if dest else _empty_unused_sidecar()


def research_diagnosis(run: ResearchRun) -> dict:
    dest = Path(run.save_path or "")
    sidecar = literature_sidecar_for_run(run)
    blob = dest / "diagnosis.json"
    from fengsuan.app.services.reproduce import diagnosis_is_live, job_next_step

    status = run.status or ""
    error_class = run.error_class
    base: dict = {}
    if blob.is_file():
        try:
            loaded = json.loads(blob.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and diagnosis_is_live(loaded, status) and loaded.get("next_step"):
                base = loaded
        except (OSError, json.JSONDecodeError):
            pass
    if not base:
        try:
            progress = json.loads(run.progress_json or "{}")
        except json.JSONDecodeError:
            progress = {}
        stored = progress.get("diagnosis") if isinstance(progress, dict) else None
        if isinstance(stored, dict) and stored.get("next_step") and status == "failed":
            base = {
                "kind": stored.get("kind") or error_class or "parse",
                "suggested_action": stored.get("suggested_action") or "resume",
                "next_step": stored.get("next_step"),
                "summary": (run.error or stored.get("next_step") or "")[:500],
                "can_resume": True,
            }
        elif status == "awaiting_gpu" or error_class == "awaiting_gpu":
            next_step = job_next_step(status, error_class, kind="research")
            base = {"kind": "gpu", "suggested_action": "ask_admin_gpu", "next_step": next_step, "summary": (run.error or next_step)[:500]}
        elif status == "blocked" or error_class == "need_data":
            next_step = job_next_step(status, error_class, kind="research")
            base = {"kind": "need_data", "suggested_action": "need_data", "next_step": next_step, "summary": (run.error or next_step)[:500]}
        elif status == "paused":
            next_step = job_next_step(status, error_class, kind="research")
            base = {"kind": "paused", "suggested_action": "resume", "next_step": next_step, "summary": (run.error or next_step)[:500]}
        elif status in {"pending", "queued"}:
            next_step = job_next_step(status, error_class, kind="research")
            base = {"kind": "pending", "suggested_action": "resume", "next_step": next_step, "summary": (run.error or next_step)[:500]}
        elif status == "failed":
            next_step = job_next_step(status, error_class, kind="research")
            base = {"kind": error_class or "failed", "suggested_action": "resume", "next_step": next_step, "summary": (run.error or next_step)[:500]}
    out = dict(base or {})
    out.update(sidecar)
    return out if out else {}


def mark_research_awaiting_gpu(db: Session, run: ResearchRun) -> None:
    if keep_user_signal(db, run):
        return
    already = run.status == "awaiting_gpu"
    run.status = "awaiting_gpu"
    run.error_class = "awaiting_gpu"
    run.error = "管理员未开启 GPU。已停在 awaiting_gpu，未填写任何伪造指标。"
    if not already:
        emit_activity(
            db,
            lab_id=run.lab_id,
            actor_id=run.requester_id,
            verb="run.awaiting_gpu",
            object_type="research_run",
            object_id=run.id,
            summary="管理员未开启 GPU。已停在 awaiting_gpu，未填写任何伪造指标。",
        )
    db.commit()


def classify_engine_exit(code, interrupt_after: str = "") -> str:
    """兼容旧 sys.exit 与新 return 状态。0/空/completed 为成功；paused 或中断点为暂停。"""
    if isinstance(code, dict):
        status = (code.get("status") or "").strip()
        if status == "failed":
            return "failed"
        if status == "paused":
            return "paused"
        code = 0
    elif isinstance(code, str):
        if code == "failed":
            return "failed"
        if code == "paused":
            return "paused"
        if code in {"completed", "succeeded"}:
            code = 0
    if code not in (0, None):
        return "failed"
    if (interrupt_after or "").strip():
        return "paused"
    return "succeeded"


def write_lab_context(dest: Path, injected: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    workspace = dest / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(injected, ensure_ascii=False, indent=2)
    lines = ["# 关联对象", ""]
    ds = injected.get("dataset") if isinstance(injected.get("dataset"), dict) else None
    if ds:
        lines.append(f"- 数据集：{ds.get('name') or ''} ({ds.get('id') or ''})")
    for lit in injected.get("literature") or []:
        if not isinstance(lit, dict):
            continue
        brief = lit.get("reproduce_brief") or ""
        lines.append(f"- 文献：{lit.get('title') or ''} ({lit.get('id') or ''})")
        if brief:
            lines.append(f"  - 复现：{brief}")
    if len(lines) == 2:
        lines.append("- （无组内对象）")
    lines.extend(["", injected.get("note") or "", ""])
    md = "\n".join(lines)
    gap_lines = ["# 组内复现差距", ""]
    for lit in injected.get("literature") or []:
        if not isinstance(lit, dict):
            continue
        brief = lit.get("reproduce_brief") or ""
        gap_lines.append(f"- {lit.get('title') or ''} ({lit.get('id') or ''}): {brief or '尚无组内复现任务。禁止把论文声称当成组内实测。'}")
    if len(gap_lines) == 2:
        gap_lines.append("- （无组内文献）")
    gap_md = "\n".join(gap_lines)
    for folder in (dest, workspace):
        (folder / "lab_context.json").write_text(payload, encoding="utf-8")
        (folder / "linked_objects.md").write_text(md, encoding="utf-8")
        (folder / "reproduce_gaps.md").write_text(gap_md, encoding="utf-8")


def assert_lab_context(dest: Path) -> None:
    root = dest / "lab_context.json"
    ws = dest / "workspace" / "lab_context.json"
    if not root.is_file() or not ws.is_file():
        raise FileNotFoundError("组内注入文件缺失：lab_context.json")


def _subgraph_should_have_finished(name: str, interrupt_after: str, enabled: bool) -> bool:
    if not enabled:
        return False
    after = (interrupt_after or "").strip()
    if not after:
        return True
    if after not in ALL_SUBGRAPHS:
        return True
    return ALL_SUBGRAPHS.index(name) <= ALL_SUBGRAPHS.index(after)


def required_research_files(cfg, run: ResearchRun) -> list[tuple[str, Path]]:
    """对应子图已启用且应跑完时，收尾必须有的作业文件。无 workflow 的 stub 配置不闸。"""
    dest = Path(run.save_path or "")
    wf = getattr(cfg, "workflow", None)
    if wf is None:
        return []
    after = run.interrupt_after or ""
    need: list[tuple[str, Path]] = []
    if _subgraph_should_have_finished("literature_reviewer", after, bool(getattr(wf, "enable_literature_review", True))):
        need.append(("literature_review.md", dest / "workspace" / "literature_review.md"))
        need.append(("literature_review.bib", dest / "workspace" / "literature_review.bib"))
    if _subgraph_should_have_finished("supervisor", after, True):
        need.append(("plan.md", dest / "plan.md"))
    return need


def unused_injected_titles(dest: Path, injected: dict | None) -> list[str]:
    """综述/计划未点名且未写「未使用：标题」的组内文献。不扫 sidecar 自身。"""
    titles: list[str] = []
    seen: set[str] = set()
    for lit in _literature_rows_for_gate(dest, injected):
        title = str(lit.get("title") or "").strip()
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    if not titles:
        return []
    skip = {"lab_context.json", "linked_objects.md", "reproduce_gaps.md"}
    parts: list[str] = []
    for folder in (dest, dest / "workspace"):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.name in skip or path.suffix.lower() not in {".md", ".txt", ".tex"}:
                continue
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    blob = "\n".join(parts)
    unused: list[str] = []
    for title in titles:
        if title in blob or f"未使用：{title}" in blob or f"未使用: {title}" in blob:
            continue
        unused.append(title)
    return unused




def _literature_rows_for_gate(dest: Path, injected: dict | None) -> list[dict]:
    """注入内存 + 作业目录 lab_context.json。sidecar 只当证据源，不进综述扫描。"""
    rows: list[dict] = []
    payload = injected if isinstance(injected, dict) else {}
    for lit in payload.get("literature") or []:
        if isinstance(lit, dict):
            rows.append(lit)
    for folder in (dest, dest / "workspace"):
        path = folder / "lab_context.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for lit in data.get("literature") or []:
            if isinstance(lit, dict):
                rows.append(lit)
    return rows


def dishonest_reproduce_claims(dest: Path, injected: dict | None) -> str:
    """综述/计划把未测文献写成成功时拦截。不扫 sidecar 自身。"""
    unmeasured: list[str] = []
    seen: set[str] = set()
    for lit in _literature_rows_for_gate(dest, injected):
        ev = lit.get("reproduce") if isinstance(lit.get("reproduce"), dict) else {}
        if ev.get("measured"):
            continue
        title = str(lit.get("title") or "").strip()
        if title and title not in seen:
            seen.add(title)
            unmeasured.append(title)
    if not unmeasured:
        return ""
    skip = {"lab_context.json", "linked_objects.md", "reproduce_gaps.md"}
    parts: list[str] = []
    for folder in (dest, dest / "workspace"):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.name in skip or path.suffix.lower() not in {".md", ".txt", ".tex"}:
                continue
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    blob = "\n".join(parts)
    from fengsuan.app.services.reproduce import text_claims_reproduce_success

    hit = text_claims_reproduce_success(blob)
    if not hit:
        return ""
    return "综述/计划把尚未实测的组内文献写成成功（「" + hit + "」）。禁止把声称当组内成功。未测标题：" + "；".join(unmeasured[:8])


def research_output_gate(cfg, run: ResearchRun, injected: dict | None) -> str:
    """缺必填文件或未交代组内文献则返回错误文案，否则空串。"""
    dest = Path(run.save_path or "")
    missing = []
    for name, path in required_research_files(cfg, run):
        if not path.is_file() or not path.read_text(encoding="utf-8", errors="ignore").strip():
            missing.append(name)
    if missing:
        return f"文献调研收尾缺少必填文件：{'、'.join(missing)}。不得标完成。"
    leftover = unused_injected_titles(dest, injected)
    if leftover:
        return "组内已关联文献未在综述/结论中点名，也未写「未使用：标题」：" + "；".join(leftover)
    bib_err = literature_bib_gate(dest, cfg, run)
    if bib_err:
        return bib_err
    return dishonest_reproduce_claims(dest, injected)


def literature_bib_gate(dest: Path, cfg, run: ResearchRun) -> str:
    wf = getattr(cfg, "workflow", None)
    if wf is None:
        return ""
    if not _subgraph_should_have_finished("literature_reviewer", run.interrupt_after or "", bool(getattr(wf, "enable_literature_review", True))):
        return ""
    bib = dest / "workspace" / "literature_review.bib"
    md = dest / "workspace" / "literature_review.md"
    from openlens_ai.utils.literature_bib import load_search_hits, unused_bib_titles, valid_bib_titles

    hit_n = len(load_search_hits(dest))
    hit_note = f"作业目录已有 {hit_n} 条去重检索命中。" if hit_n else ""
    if not bib.is_file() or not bib.read_text(encoding="utf-8", errors="ignore").strip():
        extra = (" " + hit_note + "收尾应能写出书目。请恢复任务。") if hit_n else ""
        return "文献调研收尾缺少必填文件：literature_review.bib。" + extra + "不得标完成。"
    titles = valid_bib_titles(bib.read_text(encoding="utf-8", errors="ignore"))
    if not titles:
        extra = (" " + hit_note) if hit_note else ""
        return "literature_review.bib 没有有效条目（untitled 或空题名不计入）。" + extra + "不得标完成。"
    blob = md.read_text(encoding="utf-8", errors="ignore") if md.is_file() else ""
    missing = unused_bib_titles(blob, titles) if blob else []
    if blob and missing:
        shown = "；".join(missing[:8])
        extra = (" " + hit_note) if hit_note else ""
        return f"综述没写到 literature_review.bib 中的题名：{shown}。{extra}请在综述中点名或写「未使用：标题」。不得标完成。"
    return ""


def create_research_run(
    db: Session,
    *,
    lab_id: str,
    user_id: str,
    question: str,
    dataset_path: str = "",
    domain: str = "general",
    language: str = "chs",
    interrupt_after: str = "",
    dataset_id: str | None = None,
    literature_item_ids: list[str] | None = None,
) -> ResearchRun:
    from fengsuan.app.services.llm import assert_lab_quota

    settings = get_settings()
    with lab_quota_guard(db, lab_id):
        assert_lab_quota(db, lab_id, "research")
        user_n, lab_n = count_heavy(db, user_id, lab_id)
        if user_n >= settings.heavy_job_per_user:
            raise ValueError("quota")
        if lab_n >= settings.heavy_job_per_lab:
            raise ValueError("quota")
        run = ResearchRun(
            lab_id=lab_id,
            requester_id=user_id,
            question=question,
            dataset_path=dataset_path,
            dataset_id=dataset_id,
            domain=domain or "general",
            language=language or "chs",
            status="pending",
            interrupt_after=interrupt_after or "",
            progress_json="{}",
        )
        db.add(run)
        db.flush()
        user_n, lab_n = count_heavy(db, user_id, lab_id)
        if user_n > settings.heavy_job_per_user or lab_n > settings.heavy_job_per_lab:
            db.delete(run)
            db.flush()
            raise ValueError("quota")
        dest = Path(settings.file_root) / "runs" / lab_id / run.id
        dest.mkdir(parents=True, exist_ok=True)
        run.save_path = str(dest)
        picked = list(literature_item_ids or [])
        injected_from_hung = False
        includes_children = False
        if not picked:
            picked, includes_children = hung_scope_literature_ids(db, lab_id, user_id)
            injected_from_hung = bool(picked)
        injected = build_lab_injection(db, picked, dataset_id)
        run.progress_json = json.dumps(
            {
                "literature_item_ids": picked,
                "dataset_id": dataset_id,
                "injected": injected,
                "direction_id": membership_direction_id(db, lab_id, user_id),
                "injected_from_hung": injected_from_hung,
                "includes_children": includes_children,
            },
            ensure_ascii=False,
        )
        if injected.get("literature") or injected.get("dataset"):
            write_lab_context(dest, injected)
        enqueue_job(ResearchRun, run.id)
        emit_activity(
            db,
            lab_id=lab_id,
            actor_id=user_id,
            verb="run.started",
            object_type="research_run",
            object_id=run.id,
            summary=question[:80],
        )
        return run


def engine_runtime_missing() -> str:
    """镜像未装齐文献调研依赖时给出可读下一步，不把 ModuleNotFound 写成模型故障。"""
    try:
        from openlens_ai.utils.config import Config  # noqa: F401
    except Exception as exc:
        return f"文献调研引擎配置依赖未就绪（{exc}）。文献与写作仍可用，不会伪造指标。"
    try:
        from openlens_ai.main import main  # noqa: F401
    except Exception as exc:
        return f"文献调研引擎未装入本镜像（{exc}）。文献与写作仍可用；配好平台模型 Key 后仍需含引擎依赖的镜像。"
    return ""


def load_engine_config():
    try:
        from openlens_ai.utils.config import Config
    except Exception:
        return None
    for name in ("config.toml", "config.minimal.toml"):
        cfg_path = Path(name)
        if cfg_path.exists():
            return Config.from_toml(str(cfg_path))
    return Config()


def auto_resume_gate_allowed(progress: dict | None, sidecar: dict | None) -> bool:
    """命中已在盘上且本次尚未自动恢复，才允许再走一次 literature_reviewer。"""
    prev = progress if isinstance(progress, dict) else {}
    extra = sidecar if isinstance(sidecar, dict) else {}
    if prev.get("auto_resumed_gate"):
        return False
    return int(extra.get("search_hit_count") or 0) > 0


def prepare_engine_cfg_context(cfg):
    """平台作业默认打开鉴权上下文，防止压缩后丢掉出处。"""
    if getattr(cfg, "context", None) is not None:
        cfg.context.authenticated_context = True
    return cfg


def run_openlens_graph(cfg, run: ResearchRun, *, refine_suggestion: str = ""):
    """调用现有 OpenLens 入口。暂停恢复走 thread_id / 返回值，不再依赖 sys.exit。"""
    from openlens_ai.main import main, main_resume

    if is_resumable(run.save_path) or run.resume_from_subgraph:
        return main_resume(
            run.save_path,
            refine_suggestion=refine_suggestion,
            start_from_subgraph=run.resume_from_subgraph or "",
            interrupt_after_subgraph=run.interrupt_after or "",
        )
    if refine_suggestion:
        cfg.refine_suggestion = refine_suggestion
    return main(cfg, interrupt_after_subgraph=run.interrupt_after or "")


def execute_research_run(db: Session, run: ResearchRun, *, already_claimed: bool = False) -> None:
    """适配现有 OpenLens 图：不改子图语义。失败时记录可读 error_class。"""
    if keep_user_signal(db, run):
        return
    if not try_begin_execute(db, run, already_claimed=already_claimed):
        return
    try:
        missing = engine_runtime_missing()
        if missing:
            run.status = "failed"
            run.error_class = "parse"
            run.error = missing
            persist_unused_diagnosis(run, db)
            emit_activity(
                db,
                lab_id=run.lab_id,
                actor_id=run.requester_id,
                verb="run.failed",
                object_type="research_run",
                object_id=run.id,
                summary=run.error[:200],
            )
            db.commit()
            return
        cfg = load_engine_config()
        if cfg is None:
            run.status = "failed"
            run.error_class = "parse"
            run.error = "文献调研引擎配置无法加载。文献与写作仍可用，不会伪造指标。"
            persist_unused_diagnosis(run, db)
            emit_activity(
                db,
                lab_id=run.lab_id,
                actor_id=run.requester_id,
                verb="run.failed",
                object_type="research_run",
                object_id=run.id,
                summary=run.error,
            )
            db.commit()
            return
        prev: dict = {}
        try:
            prev = json.loads(run.progress_json or "{}")
        except json.JSONDecodeError:
            prev = {}
        if not isinstance(prev, dict):
            prev = {}
        injected = prev.get("injected") if isinstance(prev.get("injected"), dict) else {}
        dest = Path(run.save_path)
        dest.mkdir(parents=True, exist_ok=True)
        if injected.get("literature") or injected.get("dataset"):
            write_lab_context(dest, injected)
            assert_lab_context(dest)
        gpu_mode = current_gpu_mode(db)
        needs_gpu = research_needs_gpu(db, run, injected)
        if gpu_mode == "off" and needs_gpu:
            mark_research_awaiting_gpu(db, run)
            return
        cfg.question = compose_injected_question(run.question, injected) if (injected.get("literature") or injected.get("dataset")) else run.question
        cfg.dataset_path = run.dataset_path
        cfg.domain = run.domain or "general"
        cfg.language = run.language or "chs"
        cfg.save_root = str(Path(run.save_path).parent)
        cfg.thread_id = run.id
        cfg.save_path = run.save_path
        prepare_engine_cfg_context(cfg)
        try:
            from openlens_ai.utils.job_context import bind_job_save_path

            bind_job_save_path(run.save_path)
        except Exception:
            pass
        try:
            from fengsuan.app.services.llm import resolve_llm

            llm = resolve_llm(db, run.requester_id, run.lab_id, purpose="chat")
            cfg.llm.chat.api_key = llm.api_key
            cfg.llm.chat.base_url = llm.base_url
            cfg.llm.chat.model = llm.model
            try:
                vision = resolve_llm(db, run.requester_id, run.lab_id, purpose="vision")
            except Exception:
                vision = llm
            cfg.llm.vision.api_key = vision.api_key
            cfg.llm.vision.base_url = vision.base_url
            cfg.llm.vision.model = vision.model
        except Exception as exc:
            from fengsuan.app.core.errors import ClassifiedHTTPException, public_exc_text

            run.status = "failed"
            run.error_class = getattr(exc, "error_class", None) or "llm"
            if isinstance(exc, ClassifiedHTTPException):
                run.error = exc.detail
            else:
                run.error = public_exc_text(exc, "未能解析云端密钥")
            persist_unused_diagnosis(run, db)
            db.commit()
            return
        cfg.save_toml(str(dest / "config.toml"))
        outcome = None
        exit_code = 0
        try:
            result = run_openlens_graph(cfg, run)
            outcome = classify_engine_exit(result if result is not None else 0, run.interrupt_after or "")
            if isinstance(result, dict) and result.get("error"):
                exit_code = result.get("error")
        except SystemExit as exc:
            exit_code = exc.code
            outcome = classify_engine_exit(exc.code, run.interrupt_after or "")
        if outcome is None:
            outcome = classify_engine_exit(0, run.interrupt_after or "")
        if outcome == "failed":
            if keep_user_signal(db, run):
                return
            if gpu_mode == "off" and (needs_gpu or text_needs_gpu(str(exit_code or ""))):
                mark_research_awaiting_gpu(db, run)
                return
            run.status = "failed"
            run.error_class = "llm"
            run.error = f"文献调研引擎异常退出（{exit_code}）"
            persist_unused_diagnosis(run, db)
            emit_activity(
                db,
                lab_id=run.lab_id,
                actor_id=run.requester_id,
                verb="run.failed",
                object_type="research_run",
                object_id=run.id,
                summary=run.error[:200],
            )
            db.commit()
            return
        if outcome == "paused":
            if keep_user_signal(db, run):
                return
            run.status = "paused"
            persist_unused_diagnosis(run, db)
            db.commit()
            return
        msgs = read_run_messages(run.save_path)
        if keep_user_signal(db, run):
            return
        gate = research_output_gate(cfg, run, injected)
        if gate:
            try:
                prev_gate = json.loads(run.progress_json or "{}")
            except json.JSONDecodeError:
                prev_gate = {}
            if not isinstance(prev_gate, dict):
                prev_gate = {}
            extra = literature_sidecar(Path(run.save_path or ""))
            if auto_resume_gate_allowed(prev_gate, extra):
                prev_gate["auto_resumed_gate"] = True
                run.progress_json = json.dumps(prev_gate, ensure_ascii=False)
                if not run.resume_from_subgraph:
                    run.resume_from_subgraph = "literature_reviewer"
                db.commit()
                try:
                    from openlens_ai.utils.literature_bib import write_literature_evidence

                    write_literature_evidence(run.save_path)
                except Exception:
                    pass
                try:
                    run_openlens_graph(cfg, run, refine_suggestion=gate)
                except SystemExit:
                    pass
                if keep_user_signal(db, run):
                    return
                gate = research_output_gate(cfg, run, injected)
                extra = literature_sidecar(Path(run.save_path or ""))
            if gate:
                run.status = "failed"
                run.error_class = "parse"
                run.error = gate
                prev_gate["diagnosis"] = {
                    "kind": "missing_artifact",
                    "next_step": gate,
                    "can_resume": True,
                    "suggested_action": "resume",
                    **extra,
                    "review_ready": bool(extra.get("review_ready")),
                }
                run.progress_json = json.dumps(prev_gate, ensure_ascii=False)
                emit_activity(
                    db,
                    lab_id=run.lab_id,
                    actor_id=run.requester_id,
                    verb="run.failed",
                    object_type="research_run",
                    object_id=run.id,
                    summary=gate[:200],
                )
                persist_unused_diagnosis(run, db)
                db.commit()
                return
        run.status = "succeeded"
        prev: dict = {}
        try:
            prev = json.loads(run.progress_json or "{}")
        except json.JSONDecodeError:
            prev = {}
        if not isinstance(prev, dict):
            prev = {}
        meta = subgraph_progress_meta(run.save_path)
        run.progress_json = json.dumps(
            {
                "literature_item_ids": prev.get("literature_item_ids") or [],
                "dataset_id": prev.get("dataset_id") or run.dataset_id,
                "injected": prev.get("injected") or {},
                "direction_id": prev.get("direction_id") or "",
                "done": True,
                "messages": msgs[-20:],
                "subgraphs": meta["subgraphs"],
                "steps_done": meta["steps_done"],
                "steps_total": meta["steps_total"],
                "progress_source": meta["progress_source"],
            },
            ensure_ascii=False,
        )
        seen_nodes = set()
        for m in msgs:
            node = m.get("subgraph") or m.get("node") or m.get("agent")
            if node and node not in seen_nodes:
                seen_nodes.add(node)
                emit_activity(
                    db,
                    lab_id=run.lab_id,
                    actor_id=run.requester_id,
                    verb="run.subgraph_complete",
                    object_type="research_run",
                    object_id=run.id,
                    summary=str(node),
                )
        emit_activity(
            db,
            lab_id=run.lab_id,
            actor_id=run.requester_id,
            verb="run.succeeded",
            object_type="research_run",
            object_id=run.id,
            summary="文献调研任务完成",
        )
    except Exception as exc:
        from fengsuan.app.core.errors import public_exc_text

        if keep_user_signal(db, run):
            return
        klass = classify_research_failure(exc, gpu_mode=current_gpu_mode(db), needs_gpu=research_needs_gpu(db, run, {}))
        if klass == "awaiting_gpu":
            mark_research_awaiting_gpu(db, run)
            return
        run.status = "failed"
        run.error_class = klass
        run.error = public_exc_text(exc, "文献调研任务失败")
        persist_unused_diagnosis(run, db)
        emit_activity(
            db,
            lab_id=run.lab_id,
            actor_id=run.requester_id,
            verb="run.failed",
            object_type="research_run",
            object_id=run.id,
            summary=run.error[:200],
        )
    db.commit()
