from __future__ import annotations

from datetime import timezone

from sqlalchemy.orm import Session

from fengsuan.app.core.concurrency import parse_iso
from fengsuan.app.db.base import utcnow
from fengsuan.app.models.lab import DegreeMilestone

_KIND_LABEL = {"proposal": "开题", "midterm": "中期", "defense": "答辩"}


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _due_overdue(row: DegreeMilestone) -> bool:
    if not row.due_at or row.status == "done":
        return False
    return _aware(row.due_at) < utcnow()


def _due_meta(row: DegreeMilestone) -> tuple[bool, int | None, str]:
    overdue = _due_overdue(row)
    if not row.due_at:
        return overdue, None, ""
    due = _aware(row.due_at)
    now = utcnow()
    days = (due.date() - now.date()).days
    if row.status == "done":
        label = due.date().isoformat()
    elif days < 0:
        label = f"已过期 {abs(days)} 天"
    elif days == 0:
        label = "今天截止"
    elif days == 1:
        label = "明天截止"
    elif days <= 7:
        label = f"还有 {days} 天"
    else:
        label = due.date().isoformat()
    return overdue, days, label


def milestone_dict(row: DegreeMilestone) -> dict:
    overdue, days_left, due_label = _due_meta(row)
    return {
        "id": row.id,
        "lab_id": row.lab_id,
        "direction_id": row.direction_id,
        "manuscript_id": row.manuscript_id,
        "owner_id": row.owner_id,
        "kind": row.kind,
        "kind_label": _KIND_LABEL.get(row.kind) or row.kind,
        "status": row.status,
        "title": row.title or "",
        "notes": row.notes or "",
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "days_left": days_left,
        "due_label": due_label,
        "overdue": overdue,
        "manuscript_href": f"/manuscripts/{row.manuscript_id}" if row.manuscript_id else None,
        "direction_href": f"/labs/{row.lab_id}/directions/{row.direction_id}",
    }


def upcoming_milestones(
    db: Session,
    lab_id: str,
    *,
    owner_id: str | None = None,
    teacher: bool = False,
    limit: int = 5,
) -> list[dict]:
    q = db.query(DegreeMilestone).filter(
        DegreeMilestone.lab_id == lab_id,
        DegreeMilestone.deleted_at.is_(None),
        DegreeMilestone.status != "done",
    )
    if not teacher:
        q = q.filter(DegreeMilestone.owner_id == owner_id)
    rows = q.limit(20).all()
    now = utcnow()

    def sort_key(r: DegreeMilestone):
        return (r.due_at is None, r.due_at or now, r.created_at or now)

    rows.sort(key=sort_key)
    return [milestone_dict(r) for r in rows[:limit]]


def list_direction_milestones(db: Session, direction_id: str) -> list[DegreeMilestone]:
    return (
        db.query(DegreeMilestone)
        .filter(DegreeMilestone.direction_id == direction_id, DegreeMilestone.deleted_at.is_(None))
        .order_by(DegreeMilestone.created_at.asc())
        .all()
    )


def list_manuscript_milestones(db: Session, manuscript_id: str) -> list[DegreeMilestone]:
    return (
        db.query(DegreeMilestone)
        .filter(DegreeMilestone.manuscript_id == manuscript_id, DegreeMilestone.deleted_at.is_(None))
        .order_by(DegreeMilestone.created_at.asc())
        .all()
    )


def parse_due(value: str | None):
    if not value:
        return None
    return parse_iso(value)
