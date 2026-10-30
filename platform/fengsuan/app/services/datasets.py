from __future__ import annotations

from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.safepath import ensure_under, safe_filename, write_bytes_under
from fengsuan.app.models.dataset import Dataset, DatasetLiteratureLink
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.models.runs import ReproduceRun, ResearchRun


def _lab_dataset_root(lab_id: str) -> Path:
    return Path(get_settings().file_root) / "files" / lab_id / "datasets"


def resolve_registered_path(lab_id: str, local_path: str) -> str:
    raw = (local_path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    root = _lab_dataset_root(lab_id)
    root.mkdir(parents=True, exist_ok=True)
    rel = Path(raw)
    if rel.is_absolute() or getattr(rel, "anchor", None) or ".." in rel.parts:
        raise ValueError("path")
    if raw.startswith("/") or raw.startswith("\\") or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError("path")
    target = (root / rel).resolve()
    ensure_under(root, target)
    return str(target)


def runtime_path(ds: Dataset) -> str:
    if ds.file_path and Path(ds.file_path).exists():
        return ds.file_path
    if ds.local_path:
        try:
            p = resolve_registered_path(ds.lab_id, ds.local_path)
            if Path(p).exists():
                return p
        except ValueError:
            return ""
    return ""


def usage_breakdown(db: Session, dataset_ids: list[str]) -> dict[str, dict[str, int]]:
    if not dataset_ids:
        return {}
    rcount = dict(
        db.query(ResearchRun.dataset_id, func.count())
        .filter(ResearchRun.dataset_id.in_(dataset_ids))
        .group_by(ResearchRun.dataset_id)
        .all()
    )
    pcount = dict(
        db.query(ReproduceRun.dataset_id, func.count())
        .filter(ReproduceRun.dataset_id.in_(dataset_ids))
        .group_by(ReproduceRun.dataset_id)
        .all()
    )
    return {
        i: {"research": int(rcount.get(i, 0)), "reproduce": int(pcount.get(i, 0))}
        for i in dataset_ids
    }


def usage_counts(db: Session, dataset_ids: list[str]) -> dict[str, int]:
    return {i: int(v["research"] + v["reproduce"]) for i, v in usage_breakdown(db, dataset_ids).items()}


def dataset_dto(
    ds: Dataset,
    *,
    teacher: bool,
    literature_ids: list[str] | None = None,
    usage_count: int | None = None,
) -> dict:
    out = {
        "id": ds.id,
        "lab_id": ds.lab_id,
        "name": ds.name,
        "description": ds.description,
        "source_note": ds.source_note,
        "license": ds.license,
        "created_by": ds.created_by,
        "has_file": bool(ds.file_path or ds.local_path),
        "literature_item_ids": literature_ids or [],
        "usage_count": int(usage_count or 0),
    }
    if teacher:
        out["local_path"] = ds.local_path
        out["file_path"] = ds.file_path
    return out


def inferred_direction_ids(db: Session, literature_ids: list[str] | None) -> list[str]:
    ids = [str(i).strip() for i in (literature_ids or []) if str(i or "").strip()]
    if not ids:
        return []
    from fengsuan.app.models.literature import LiteratureDirectionLink

    seen: list[str] = []
    seen_set: set[str] = set()
    for row in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(ids)).all():
        if row.direction_id in seen_set:
            continue
        seen_set.add(row.direction_id)
        seen.append(row.direction_id)
    return seen


def list_links(db: Session, dataset_id: str) -> list[str]:
    return [
        r.literature_item_id
        for r in db.query(DatasetLiteratureLink).filter(DatasetLiteratureLink.dataset_id == dataset_id).all()
    ]


def literature_rows(db: Session, ids: list[str], user_id: str | None = None) -> list[dict]:
    if not ids:
        return []
    rows = db.query(LiteratureItem).filter(LiteratureItem.id.in_(ids), LiteratureItem.deleted_at.is_(None)).all()
    titles = {x.id: x.title for x in rows}
    by_id = {x.id: x for x in rows}
    out = [{"id": i, "title": titles[i]} for i in ids if i in titles]
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href

    stamp_items_open_href(db, user_id, out, id_key="id")
    lab_id = next((by_id[p["id"]].lab_id for p in out if p["id"] in by_id), "")
    stamp_reproduce_href(lab_id, out, id_key="id")
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, out, id_key="id")
    return out


def save_dataset_file(lab_id: str, filename: str, content: bytes) -> Path:
    return write_bytes_under(_lab_dataset_root(lab_id), safe_filename(filename, "dataset.bin"), content, default="dataset.bin")


def get_lab_dataset(db: Session, lab_id: str, dataset_id: str) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.deleted_at is not None or ds.lab_id != lab_id:
        raise LookupError("dataset")
    return ds


def usages(db: Session, dataset_id: str) -> dict:
    from fengsuan.app.services.reproduce import job_can_resume, job_feedback, job_next_step

    runs = (
        db.query(ResearchRun.id, ResearchRun.question, ResearchRun.status, ResearchRun.error_class)
        .filter(ResearchRun.dataset_id == dataset_id)
        .all()
    )
    repro = (
        db.query(ReproduceRun.id, ReproduceRun.status, ReproduceRun.error_class)
        .filter(ReproduceRun.dataset_id == dataset_id)
        .all()
    )
    return {
        "research_runs": [
            {
                "id": r.id,
                "question": r.question,
                "status": r.status,
                "href": f"/runs/{r.id}",
                "next_step": job_next_step(r.status, r.error_class),
                "can_resume": job_can_resume(r.status, kind="research"),
                "feedback": job_feedback(r.status, r.error_class, kind="research"),
            }
            for r in runs
        ],
        "reproduce_runs": [
            {
                "id": r.id,
                "status": r.status,
                "href": f"/reproduces/{r.id}",
                "next_step": job_next_step(r.status, r.error_class, kind="reproduce"),
                "can_resume": job_can_resume(r.status, kind="reproduce"),
                "feedback": job_feedback(r.status, r.error_class, kind="reproduce"),
            }
            for r in repro
        ],
    }


def assert_literature_in_lab(db: Session, lab_id: str, item_ids: list[str]) -> None:
    for iid in item_ids:
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None or item.lab_id != lab_id:
            raise LookupError("literature")
