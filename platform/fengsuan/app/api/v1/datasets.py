from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, require_membership
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.dataset import Dataset, DatasetLiteratureLink
from fengsuan.app.models.identity import User
from fengsuan.app.schemas.common import DatasetCreate, DatasetPatch
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.datasets import (
    assert_literature_in_lab,
    dataset_dto,
    inferred_direction_ids,
    usage_breakdown,
    usage_counts,
    get_lab_dataset,
    list_links,
    literature_rows,
    resolve_registered_path,
    save_dataset_file,
    usages,
)

router = APIRouter(tags=["datasets"])


def _teacher(db: Session, user: User, lab_id: str) -> bool:
    m = require_membership(db, user, lab_id)
    return m.role == "teacher" or bool(getattr(user, "is_admin", False))


def _dto(db: Session, ds: Dataset, *, teacher: bool, literature_ids: list[str] | None = None) -> dict:
    ids = literature_ids if literature_ids is not None else list_links(db, ds.id)
    return dataset_dto(ds, teacher=teacher, literature_ids=ids, usage_count=usage_counts(db, [ds.id]).get(ds.id, 0))


@router.get("/labs/{lab_id}/datasets")
def list_datasets(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> list[dict] | dict:
    teacher = _teacher(db, user, lab_id)
    rows = db.query(Dataset).filter(Dataset.lab_id == lab_id, Dataset.deleted_at.is_(None)).order_by(Dataset.created_at.desc()).all()
    ids = [d.id for d in rows]
    breakdown = usage_breakdown(db, ids)
    link_map: dict[str, list[str]] = {i: [] for i in ids}
    if ids:
        for row in db.query(DatasetLiteratureLink).filter(DatasetLiteratureLink.dataset_id.in_(ids)).all():
            link_map.setdefault(row.dataset_id, []).append(row.literature_item_id)
    lit_ids = [iid for lids in link_map.values() for iid in lids]
    by_lit = {x["id"]: x for x in literature_rows(db, lit_ids, user.id)}
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureDirectionLink

    dir_by_lit: dict[str, list[str]] = defaultdict(list)
    if lit_ids:
        for row in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(lit_ids)).all():
            dir_by_lit[row.item_id].append(row.direction_id)
    includes_children = False
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        includes_children = len(scope) > 1
        scoped_lits = {
            iid
            for (iid,) in db.query(LiteratureDirectionLink.item_id)
            .filter(LiteratureDirectionLink.direction_id.in_(scope or ["__none__"]))
            .all()
        }
        keep = {dsid for dsid, lids in link_map.items() if any(iid in scoped_lits for iid in lids)}
        rows = [d for d in rows if d.id in keep] if scope else []
    out = []
    for d in rows:
        lids = link_map.get(d.id) or []
        br = breakdown.get(d.id) or {"research": 0, "reproduce": 0}
        dto = dataset_dto(
            d,
            teacher=teacher,
            literature_ids=lids,
            usage_count=int(br["research"] + br["reproduce"]),
        )
        dto["research_count"] = int(br["research"])
        dto["reproduce_count"] = int(br["reproduce"])
        dto["research_href"] = f"/labs/{lab_id}/runs?dataset_id={d.id}"
        dto["reproduce_href"] = f"/labs/{lab_id}/reproduces?dataset_id={d.id}"
        dto["literature"] = [by_lit[i] for i in lids if i in by_lit][:3]
        seen_d: list[str] = []
        seen_set: set[str] = set()
        for iid in lids:
            for did in dir_by_lit.get(iid) or []:
                if did in seen_set:
                    continue
                seen_set.add(did)
                seen_d.append(did)
        dto["direction_ids"] = seen_d
        out.append(dto)
    if out:
        return out
    from fengsuan.app.services.insight import dataset_list_empty_next

    kind = "filter" if direction_id else "library"
    return {
        "items": [],
        "empty_kind": kind,
        "includes_children": includes_children,
        "empty_next": dataset_list_empty_next(
            lab_id, direction_id=direction_id, includes_children=includes_children, empty_kind=kind
        ),
    }


@router.post("/labs/{lab_id}/datasets")
def create_dataset(
    lab_id: str,
    body: DatasetCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    teacher = _teacher(db, user, lab_id)
    local_path = ""
    if body.local_path:
        try:
            resolve_registered_path(lab_id, body.local_path)
            local_path = body.local_path.replace("\\", "/").lstrip("/")
        except ValueError:
            raise HTTPException(400, detail="路径必须相对实验室资料目录，禁止扫描全盘")
    try:
        assert_literature_in_lab(db, lab_id, body.literature_item_ids or [])
    except LookupError:
        raise HTTPException(404, detail="文献不属于当前实验室")
    ds = Dataset(
        lab_id=lab_id,
        name=body.name,
        description=body.description,
        source_note=body.source_note,
        license=body.license,
        local_path=local_path,
        created_by=user.id,
    )
    db.add(ds)
    db.flush()
    for iid in body.literature_item_ids or []:
        db.add(DatasetLiteratureLink(dataset_id=ds.id, literature_item_id=iid))
    emit_activity(db, lab_id=lab_id, actor_id=user.id, verb="dataset.created", object_type="dataset", object_id=ds.id, summary=ds.name)
    db.commit()
    db.refresh(ds)
    ids = list_links(db, ds.id)
    data = _dto(db, ds, teacher=teacher, literature_ids=ids)
    data["literature"] = literature_rows(db, ids, user_id=user.id)
    return data


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(404, detail="数据集不存在")
    teacher = _teacher(db, user, ds.lab_id)
    ids = list_links(db, ds.id)
    used = usages(db, ds.id)
    data = dataset_dto(
        ds,
        teacher=teacher,
        literature_ids=ids,
        usage_count=len(used.get("research_runs") or []) + len(used.get("reproduce_runs") or []),
    )
    data["literature"] = literature_rows(db, ids, user_id=user.id)
    data["usages"] = used
    data["direction_ids"] = inferred_direction_ids(db, ids)
    return data


@router.patch("/datasets/{dataset_id}")
def patch_dataset(
    dataset_id: str,
    body: DatasetPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(404, detail="数据集不存在")
    teacher = _teacher(db, user, ds.lab_id)
    if body.name is not None:
        ds.name = body.name
    if body.description is not None:
        ds.description = body.description
    if body.source_note is not None:
        ds.source_note = body.source_note
    if body.license is not None:
        ds.license = body.license
    if body.local_path is not None:
        try:
            resolve_registered_path(ds.lab_id, body.local_path)
            ds.local_path = body.local_path.replace("\\", "/").lstrip("/")
        except ValueError:
            raise HTTPException(400, detail="路径必须相对实验室资料目录，禁止扫描全盘")
    if body.literature_item_ids is not None:
        try:
            assert_literature_in_lab(db, ds.lab_id, body.literature_item_ids)
        except LookupError:
            raise HTTPException(404, detail="文献不属于当前实验室")
        db.query(DatasetLiteratureLink).filter(DatasetLiteratureLink.dataset_id == ds.id).delete()
        for iid in body.literature_item_ids:
            db.add(DatasetLiteratureLink(dataset_id=ds.id, literature_item_id=iid))
    db.commit()
    db.refresh(ds)
    ids = list_links(db, ds.id)
    data = _dto(db, ds, teacher=teacher, literature_ids=ids)
    data["literature"] = literature_rows(db, ids, user_id=user.id)
    return data


@router.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(404, detail="数据集不存在")
    membership = require_membership(db, user, ds.lab_id)
    if ds.created_by != user.id and membership.role != "teacher":
        raise HTTPException(403, detail="仅创建者或导师可删除")
    ds.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/datasets/{dataset_id}/upload")
def upload_dataset_file(
    dataset_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
) -> dict:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(404, detail="数据集不存在")
    teacher = _teacher(db, user, ds.lab_id)
    from fengsuan.app.core.safepath import read_upload_limited

    try:
        content = read_upload_limited(file)
    except ValueError:
        raise HTTPException(400, detail="文件过大")
    dest = save_dataset_file(ds.lab_id, file.filename or "dataset.bin", content)
    ds.file_path = str(dest)
    db.commit()
    db.refresh(ds)
    return _dto(db, ds, teacher=teacher)
