from __future__ import annotations

from sqlalchemy.orm import Session

from fengsuan.app.core.constants import GUIDE_ITEMS
from fengsuan.app.models.credential import LLMCredential
from fengsuan.app.models.lab import LabMembership, ResearchDirection
from fengsuan.app.models.literature import LiteratureItem, LiteratureReading, LiteratureDirectionLink
from fengsuan.app.models.manuscript import Manuscript


def build_lab_guide(db: Session, lab_id: str, role: str | None = None, user_id: str | None = None) -> list[dict]:
    has_student = (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.role == "student")
        .first()
        is not None
    )
    lab_key = (
        db.query(LLMCredential)
        .filter(LLMCredential.owner_type == "lab", LLMCredential.owner_id == lab_id, LLMCredential.is_active.is_(True))
        .first()
    )
    if lab_key is None:
        from fengsuan.app.services.llm import visible_lab_credential

        borrowed, shared = visible_lab_credential(db, lab_id)
        if shared and borrowed is not None:
            lab_key = borrowed
    has_lit = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .count()
        > 0
    )
    has_reading = (
        db.query(LiteratureReading)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureReading.status.in_(["reading", "want_read"]))
        .first()
        is not None
    )
    has_ms = (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
        .first()
        is not None
    )
    has_direction_pick = (
        db.query(LabMembership)
        .join(ResearchDirection, ResearchDirection.id == LabMembership.direction_id)
        .filter(
            LabMembership.lab_id == lab_id,
            ResearchDirection.lab_id == lab_id,
            ResearchDirection.deleted_at.is_(None),
        )
        .first()
        is not None
    )
    has_dir_papers = (
        db.query(LiteratureDirectionLink)
        .join(LiteratureItem, LiteratureItem.id == LiteratureDirectionLink.item_id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .first()
        is not None
    )
    out = []
    teacher_only = {"invite", "lab_key"}
    for item in GUIDE_ITEMS:
        if role == "student" and item["key"] in teacher_only:
            continue
        done = False
        if item["key"] == "invite":
            done = has_student
        elif item["key"] == "lab_key":
            done = lab_key is not None
        elif item["key"] == "import_lit":
            done = has_lit
        elif item["key"] == "mark_read":
            done = has_reading
        elif item["key"] == "manuscript":
            done = has_ms
        elif item["key"] == "pick_direction":
            if role == "student" and user_id:
                from fengsuan.app.services.insight import live_direction_id

                mine = (
                    db.query(LabMembership)
                    .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
                    .first()
                )
                done = bool(live_direction_id(db, lab_id, mine.direction_id if mine else None))
            else:
                done = has_direction_pick
        elif item["key"] == "open_direction_papers":
            done = False
            if user_id:
                from fengsuan.app.services.insight import live_direction_id

                mine = (
                    db.query(LabMembership)
                    .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
                    .first()
                )
                live_id = live_direction_id(db, lab_id, mine.direction_id if mine else None)
                if live_id:
                    from fengsuan.app.services.directions import member_has_scope_reading

                    done = member_has_scope_reading(
                        db, lab_id, user_id, live_id, statuses=("reading", "finished")
                    )
        elif item["key"] == "cockpit":
            done = has_student
        href = f"/labs/{lab_id}{item.get('path') or ''}"
        if item["key"] == "pick_direction":
            from fengsuan.app.services.insight import pick_direction_href

            href = pick_direction_href(lab_id, role=role, surface="guide")
            n_dir = (
                db.query(ResearchDirection)
                .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
                .count()
            )
            if (role or "") != "student" and int(n_dir or 0) == 0 and has_lit:
                href = f"/labs/{lab_id}/literature"
        if item["key"] == "open_direction_papers" and user_id:
            from fengsuan.app.services.insight import live_direction_id

            mine = (
                db.query(LabMembership)
                .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
                .first()
            )
            live_id = live_direction_id(db, lab_id, mine.direction_id if mine else None)
            if live_id:
                href = f"/labs/{lab_id}/literature?direction_id={live_id}"
        out.append({**item, "done": done, "href": href})
    from fengsuan.app.services.reproduce import lab_reproduce_spine, ungrounded_manuscript_ids

    spine = lab_reproduce_spine(db, lab_id)
    if spine.get("not_run"):
        out.append(
            {
                "key": "reproduce_not_run",
                "label": f"组内有 {spine['not_run']} 条复现尚无实测",
                "path": "/reproduces",
                "done": False,
                "href": spine.get("not_run_href") or f"/labs/{lab_id}/reproduces?status=failed",
            }
        )
    ungrounded_n = len(ungrounded_manuscript_ids(db, lab_id))
    if ungrounded_n:
        out.append(
            {
                "key": "manuscripts_ungrounded",
                "label": f"组内有 {ungrounded_n} 篇手稿还没复现就写成成功",
                "path": "/manuscripts",
                "done": False,
                "href": f"/labs/{lab_id}/manuscripts?ungrounded=1",
            }
        )
    return out
