"""Direction as research-group unit. Core business stays research attribution."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership, MembershipDirectionLink, ResearchDirection

MEMBER_STATUSES = {"active", "on_leave", "graduated"}


def membership_status(mem: LabMembership | None) -> str:
    raw = (getattr(mem, "status", None) or "active").strip() or "active"
    return raw if raw in MEMBER_STATUSES else "active"


def is_active_member(mem: LabMembership | None) -> bool:
    return membership_status(mem) == "active"


def live_direction(db: Session, lab_id: str, direction_id: str | None) -> ResearchDirection | None:
    did = str(direction_id or "").strip()
    if not did:
        return None
    row = db.get(ResearchDirection, did)
    if row is None or row.lab_id != lab_id or row.deleted_at is not None:
        return None
    return row


def resolve_direction_scope(
    db: Session, lab_id: str | None, direction_id: str | None, *, include_children: bool = True
) -> list[str]:
    """父方向含子课题，子方向只含自己。无效方向返回空，不假装看全库。"""
    did = str(direction_id or "").strip()
    if not did:
        return []
    cache = db.info.setdefault("_fs_dir_scope", {})
    key = (str(lab_id or ""), did, bool(include_children))
    hit = cache.get(key)
    if hit is not None:
        return list(hit)
    resolved = did
    if lab_id:
        row = live_direction(db, lab_id, did)
        if row is None:
            cache[key] = []
            return []
        resolved = row.id
    ids = direction_scope_ids(db, resolved, include_children=include_children)
    cache[key] = list(ids)
    return list(ids)


def expand_direction_ids(db: Session, lab_id: str | None, direction_ids: list[str] | None) -> list[str]:
    """手稿/检索/点名用的方向并集：每个父方向带上子课题，去重保序。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in direction_ids or []:
        for sid in resolve_direction_scope(db, lab_id, raw):
            if sid in seen:
                continue
            seen.add(sid)
            out.append(sid)
    return out


def direction_scope_keywords(
    db: Session,
    lab_id: str | None,
    direction_id: str | None = None,
    direction_ids: list[str] | None = None,
) -> tuple[list[str], bool]:
    """父方向叠子课题关键词，去重保序。无效方向返回空词，不假装全库。"""
    import json

    wanted: list[str] = []
    seen_w: set[str] = set()
    for raw in [direction_id, *(direction_ids or [])]:
        did = str(raw or "").strip()
        if not did or did in seen_w:
            continue
        seen_w.add(did)
        wanted.append(did)
    if direction_id and lab_id and not resolve_direction_scope(db, lab_id, direction_id):
        return [], False
    ids = expand_direction_ids(db, lab_id, wanted)
    includes_children = len(ids) > len(wanted)
    kws: list[str] = []
    seen: set[str] = set()
    if ids:
        rows = {
            row.id: row
            for row in db.query(ResearchDirection)
            .filter(ResearchDirection.id.in_(ids), ResearchDirection.deleted_at.is_(None))
            .all()
        }
        for did in ids:
            row = rows.get(did)
            if row is None:
                continue
            try:
                raw = json.loads(row.keywords_json or "[]")
            except json.JSONDecodeError:
                raw = []
            for kw in raw:
                name = str(kw or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                kws.append(name)
    return kws, includes_children


def member_has_scope_reading(
    db: Session,
    lab_id: str,
    user_id: str | None,
    direction_id: str | None,
    *,
    statuses: tuple[str, ...] = ("reading",),
) -> bool:
    """主方向是父时，读子课题文献也算已开读。无效方向不算。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem, LiteratureReading

    uid = str(user_id or "").strip()
    scope = resolve_direction_scope(db, lab_id, direction_id)
    if not uid or not scope:
        return False
    return (
        db.query(LiteratureReading.id)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .join(LiteratureDirectionLink, LiteratureDirectionLink.item_id == LiteratureItem.id)
        .filter(
            LiteratureReading.user_id == uid,
            LiteratureReading.status.in_(list(statuses)),
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureDirectionLink.direction_id.in_(scope),
        )
        .first()
        is not None
    )


def direction_scope_ids(db: Session, direction_id: str, include_children: bool = True) -> list[str]:
    did = str(direction_id or "").strip()
    if not did:
        return []
    row = db.get(ResearchDirection, did)
    if row is None or row.deleted_at is not None:
        return []
    ids = [row.id]
    if include_children:
        kids = (
            db.query(ResearchDirection.id)
            .filter(
                ResearchDirection.parent_id == row.id,
                ResearchDirection.deleted_at.is_(None),
            )
            .all()
        )
        ids.extend(kid for (kid,) in kids if kid)
    return ids


def children_by_parent(db: Session, lab_id: str) -> dict[str, list[str]]:
    from collections import defaultdict

    out: dict[str, list[str]] = defaultdict(list)
    rows = (
        db.query(ResearchDirection.id, ResearchDirection.parent_id)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .all()
    )
    for did, parent_id in rows:
        if parent_id:
            out[parent_id].append(did)
    return dict(out)


def assert_parent_ok(db: Session, lab_id: str, parent_id: str | None, self_id: str | None = None) -> str | None:
    pid = str(parent_id or "").strip() or None
    if not pid:
        return None
    if self_id and pid == self_id:
        raise HTTPException(400, detail="方向不能做自己的子课题")
    parent = live_direction(db, lab_id, pid)
    if parent is None:
        raise HTTPException(404, detail="父方向不存在")
    if parent.parent_id:
        raise HTTPException(400, detail="只允许两级：子课题不能再挂子课题")
    if self_id:
        has_kids = (
            db.query(ResearchDirection.id)
            .filter(ResearchDirection.parent_id == self_id, ResearchDirection.deleted_at.is_(None))
            .first()
        )
        if has_kids:
            raise HTTPException(400, detail="已有子课题的方向不能再挂到别的方向下")
    return parent.id


def _dir_map_bag(db: Session) -> dict:
    return db.info.setdefault("_fs_dir_maps", {})


def invalidate_member_direction_maps(db: Session, lab_id: str | None = None) -> None:
    """写方向挂靠后丢掉本会话缓存，避免同一请求读到旧名册。"""
    if lab_id:
        _dir_map_bag(db).pop(lab_id, None)
        return
    db.info.pop("_fs_dir_maps", None)


def backfill_membership_links(db: Session, lab_id: str | None = None) -> int:
    """只给迁移/写路径用。读名册不得调用：GET 写库会和并发插入撞唯一约束。"""
    from sqlalchemy.exc import IntegrityError

    q = db.query(LabMembership).filter(LabMembership.direction_id.isnot(None))
    if lab_id:
        q = q.filter(LabMembership.lab_id == lab_id)
    members = [m for m in q.all() if m.direction_id]
    if not members:
        return 0
    labs = {m.lab_id for m in members}
    existing = {
        (r.lab_id, r.user_id, r.direction_id)
        for r in db.query(MembershipDirectionLink).filter(MembershipDirectionLink.lab_id.in_(labs)).all()
    }
    n = 0
    for mem in members:
        key = (mem.lab_id, mem.user_id, mem.direction_id)
        if key in existing:
            continue
        db.add(
            MembershipDirectionLink(
                lab_id=mem.lab_id,
                user_id=mem.user_id,
                direction_id=mem.direction_id,
                is_primary=True,
            )
        )
        existing.add(key)
        n += 1
    if not n:
        return 0
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        n = 0
    invalidate_member_direction_maps(db, lab_id)
    return n


def set_member_directions(
    db: Session,
    membership: LabMembership,
    direction_ids: list[str] | None,
    *,
    primary_id: str | None = None,
    replace: bool = True,
) -> str | None:
    lab_id = membership.lab_id
    wanted = [str(x).strip() for x in (direction_ids or []) if str(x or "").strip()]
    if primary_id:
        primary_id = str(primary_id).strip()
        if primary_id and primary_id not in wanted:
            wanted.insert(0, primary_id)
    if not wanted and primary_id is None and not replace:
        return membership.direction_id
    seen: list[str] = []
    for did in wanted:
        if did in seen:
            continue
        if live_direction(db, lab_id, did) is None:
            raise HTTPException(404, detail="方向不存在")
        seen.append(did)
    if replace:
        db.query(MembershipDirectionLink).filter(
            MembershipDirectionLink.lab_id == lab_id,
            MembershipDirectionLink.user_id == membership.user_id,
        ).delete(synchronize_session=False)
    else:
        if primary_id:
            db.query(MembershipDirectionLink).filter(
                MembershipDirectionLink.lab_id == lab_id,
                MembershipDirectionLink.user_id == membership.user_id,
                MembershipDirectionLink.is_primary.is_(True),
            ).update({MembershipDirectionLink.is_primary: False}, synchronize_session=False)
        existing = {
            row.direction_id: row
            for row in db.query(MembershipDirectionLink)
            .filter(
                MembershipDirectionLink.lab_id == lab_id,
                MembershipDirectionLink.user_id == membership.user_id,
            )
            .all()
        }
        for did in seen:
            if did in existing:
                continue
            db.add(
                MembershipDirectionLink(
                    lab_id=lab_id,
                    user_id=membership.user_id,
                    direction_id=did,
                    is_primary=False,
                )
            )
        db.flush()
        primary = primary_id or (seen[0] if seen else None)
        membership.direction_id = primary
        if primary:
            row = (
                db.query(MembershipDirectionLink)
                .filter(
                    MembershipDirectionLink.lab_id == lab_id,
                    MembershipDirectionLink.user_id == membership.user_id,
                    MembershipDirectionLink.direction_id == primary,
                )
                .first()
            )
            if row:
                row.is_primary = True
            else:
                db.add(
                    MembershipDirectionLink(
                        lab_id=lab_id,
                        user_id=membership.user_id,
                        direction_id=primary,
                        is_primary=True,
                    )
                )
        invalidate_member_direction_maps(db, lab_id)
        return membership.direction_id
    primary = primary_id or (seen[0] if seen else None)
    for did in seen:
        db.add(
            MembershipDirectionLink(
                lab_id=lab_id,
                user_id=membership.user_id,
                direction_id=did,
                is_primary=did == primary,
            )
        )
    membership.direction_id = primary
    invalidate_member_direction_maps(db, lab_id)
    return primary


def pick_primary_direction(db: Session, membership: LabMembership, direction_id: str | None) -> str | None:
    did = str(direction_id or "").strip() or None
    if did is None:
        db.query(MembershipDirectionLink).filter(
            MembershipDirectionLink.lab_id == membership.lab_id,
            MembershipDirectionLink.user_id == membership.user_id,
            MembershipDirectionLink.is_primary.is_(True),
        ).update({MembershipDirectionLink.is_primary: False}, synchronize_session=False)
        membership.direction_id = None
        invalidate_member_direction_maps(db, membership.lab_id)
        return None
    return set_member_directions(db, membership, [did], primary_id=did, replace=False)


def _load_member_direction_maps(db: Session, lab_id: str, user_ids: list[str] | None) -> dict[str, list[dict]]:
    """读名册：链接表为主，旧的 membership.direction_id 只在内存里补，不写库。"""
    q = db.query(MembershipDirectionLink).filter(MembershipDirectionLink.lab_id == lab_id)
    mq = db.query(LabMembership.user_id, LabMembership.direction_id).filter(
        LabMembership.lab_id == lab_id, LabMembership.direction_id.isnot(None)
    )
    if user_ids:
        q = q.filter(MembershipDirectionLink.user_id.in_(user_ids))
        mq = mq.filter(LabMembership.user_id.in_(user_ids))
    links = q.all()
    legacy = {uid: did for uid, did in mq.all() if uid and did}
    dir_ids = {row.direction_id for row in links} | set(legacy.values())
    names: dict[str, str] = {}
    if dir_ids:
        names = {
            d.id: d.name
            for d in db.query(ResearchDirection)
            .filter(ResearchDirection.id.in_(list(dir_ids)), ResearchDirection.deleted_at.is_(None))
            .all()
        }
    out: dict[str, list[dict]] = {}
    for row in links:
        if row.direction_id not in names:
            continue
        out.setdefault(row.user_id, []).append(
            {
                "id": row.direction_id,
                "name": names[row.direction_id],
                "is_primary": bool(row.is_primary),
                "href": f"/labs/{lab_id}/directions/{row.direction_id}",
            }
        )
    for uid, did in legacy.items():
        if did not in names:
            continue
        rows = out.setdefault(uid, [])
        if any(r.get("id") == did for r in rows):
            continue
        rows.append(
            {
                "id": did,
                "name": names[did],
                "is_primary": True,
                "href": f"/labs/{lab_id}/directions/{did}",
            }
        )
    for uid, rows in out.items():
        rows.sort(key=lambda x: (not x.get("is_primary"), x.get("name") or ""))
    return out


def member_direction_maps(db: Session, lab_id: str, user_ids: list[str] | None = None) -> dict[str, list[dict]]:
    bag = _dir_map_bag(db).setdefault(lab_id, {"_all": False, "users": {}})
    wanted = [str(uid) for uid in (user_ids or []) if uid]
    users: dict[str, list[dict]] = bag["users"]
    if not wanted:
        if not bag["_all"]:
            loaded = _load_member_direction_maps(db, lab_id, None)
            users.clear()
            users.update(loaded)
            bag["_all"] = True
        return {uid: list(rows) for uid, rows in users.items()}
    missing = [uid for uid in wanted if uid not in users]
    if missing and not bag["_all"]:
        loaded = _load_member_direction_maps(db, lab_id, missing)
        users.update(loaded)
        for uid in missing:
            users.setdefault(uid, [])
    return {uid: list(users.get(uid) or []) for uid in wanted}


def member_primary_direction_id(db: Session, lab_id: str, user_id: str) -> str:
    """本人主方向。入门卡与入门追问共用，不另开一套方向选择。"""
    rows = (member_direction_maps(db, lab_id, [user_id]) or {}).get(user_id) or []
    if not rows:
        return ""
    primary = next((d for d in rows if d.get("is_primary")), rows[0])
    return str(primary.get("id") or "")


def direction_member_ids(db: Session, lab_id: str, direction_ids: list[str]) -> dict[str, set[str]]:
    ids = [str(did).strip() for did in direction_ids if str(did or "").strip()]
    out: dict[str, set[str]] = {did: set() for did in ids}
    if not ids:
        return out
    for row in (
        db.query(MembershipDirectionLink)
        .filter(MembershipDirectionLink.lab_id == lab_id, MembershipDirectionLink.direction_id.in_(ids))
        .all()
    ):
        if row.user_id:
            out.setdefault(row.direction_id, set()).add(row.user_id)
    for row in (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.direction_id.in_(ids))
        .all()
    ):
        if row.user_id and row.direction_id in out:
            out[row.direction_id].add(row.user_id)
    return out


def direction_members(
    db: Session, lab_id: str, direction_id: str, *, include_children: bool = False
) -> list[tuple[LabMembership, User]]:
    ids = direction_scope_ids(db, direction_id, include_children=include_children) or [direction_id]
    mapped = direction_member_ids(db, lab_id, ids)
    uids: set[str] = set()
    for bucket in mapped.values():
        uids |= bucket
    if not uids:
        return []
    return (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id, LabMembership.user_id.in_(list(uids)))
        .all()
    )


def owner_names(db: Session, user_ids) -> dict[str, str]:
    """一次取完方向负责人姓名，替掉方向树逐条 db.get。"""
    wanted = {str(uid) for uid in (user_ids or []) if uid}
    if not wanted:
        return {}
    rows = db.query(User.id, User.display_name, User.email).filter(User.id.in_(wanted)).all()
    return {uid: (name or email or "") for uid, name, email in rows}


def owner_name(db: Session, user_id: str | None) -> str:
    if not user_id:
        return ""
    return owner_names(db, [user_id]).get(user_id, "")
