from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership

PROFILE_TEXT_FIELDS = ("bio", "homepage")
PROFILE_LIST_FIELDS = ("interests", "skills")
PROFILE_DATE_FIELDS = ("enrolled_at", "expected_graduate_at")


def _list(raw: str | None) -> list[str]:
    try:
        val = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()][:20]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def membership_of(db: Session, lab_id: str, user_id: str) -> LabMembership | None:
    return (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
        .first()
    )


def profile_dto(db: Session, lab_id: str, mem: LabMembership, user: User) -> dict:
    from fengsuan.app.services.directions import member_direction_maps, membership_status

    dirs = (member_direction_maps(db, lab_id, [user.id]) or {}).get(user.id) or []
    advisor = db.get(User, mem.advisor_user_id) if mem.advisor_user_id else None
    return {
        "user_id": user.id,
        "display_name": user.display_name or user.email,
        "email": user.email,
        "role": mem.role,
        "title": mem.title or "",
        "status": membership_status(mem),
        "bio": mem.bio or "",
        "interests": _list(mem.interests_json),
        "skills": _list(mem.skills_json),
        "advisor_user_id": mem.advisor_user_id or None,
        "advisor_name": (advisor.display_name or advisor.email) if advisor else "",
        "enrolled_at": _iso(mem.enrolled_at),
        "expected_graduate_at": _iso(mem.expected_graduate_at),
        "homepage": mem.homepage or "",
        "academic": _academic(mem),
        "identity": _public_identity(mem),
        "sources_status": _source_status(mem),
        "directions": dirs,
        "href": f"/labs/{lab_id}/cockpit/members/{user.id}",
        "profile_complete": bool((mem.bio or "").strip() and _list(mem.interests_json)),
    }


def member_profile(db: Session, lab_id: str, user_id: str) -> dict | None:
    mem = membership_of(db, lab_id, user_id)
    if mem is None:
        return None
    user = db.get(User, user_id)
    if user is None:
        return None
    return profile_dto(db, lab_id, mem, user)


def lab_profiles(db: Session, lab_id: str) -> list[dict]:
    rows = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id)
        .all()
    )
    return [profile_dto(db, lab_id, m, u) for m, u in rows]


def _parse_date(raw) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def apply_profile_patch(db: Session, mem: LabMembership, patch: dict) -> dict:
    """按字段增量更新。`None` 表示不改，空字符串表示清空。"""
    changed: list[str] = []
    for field in PROFILE_TEXT_FIELDS:
        if field in patch and patch[field] is not None:
            setattr(mem, field, str(patch[field]).strip()[:2000 if field == "bio" else 300])
            changed.append(field)
    for field in PROFILE_LIST_FIELDS:
        if field in patch and patch[field] is not None:
            vals = patch[field]
            items = [str(x).strip() for x in vals if str(x).strip()][:20] if isinstance(vals, list) else []
            setattr(mem, f"{field}_json", json.dumps(items, ensure_ascii=False))
            changed.append(field)
    for field in PROFILE_DATE_FIELDS:
        if field in patch and patch[field] is not None:
            setattr(mem, field, _parse_date(patch[field]))
            changed.append(field)
    academic_fields = (
        "name_en",
        "aliases",
        "affiliation",
        "past_affiliations",
        "education",
        "links",
        "works",
        "ai_note",
        "sources",
        "files",
    )
    if any(field in patch and patch[field] is not None for field in academic_fields):
        current = _academic_raw(mem)
        for field in academic_fields:
            if field in patch and patch[field] is not None:
                current[field] = _cap_academic(field, patch[field])
                changed.append(field)
        mem.academic_json = json.dumps(current, ensure_ascii=False)
    if "advisor_user_id" in patch and patch["advisor_user_id"] is not None:
        advisor_id = str(patch["advisor_user_id"]).strip()
        if advisor_id and membership_of(db, mem.lab_id, advisor_id) is None:
            raise ValueError("导师不是本实验室成员")
        mem.advisor_user_id = advisor_id or None
        changed.append("advisor_user_id")
    db.flush()
    return {"changed": changed}


def _obj(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _academic_raw(mem: LabMembership) -> dict:
    data = _obj(getattr(mem, "academic_json", "") or "")
    data.setdefault("links", {})
    data.setdefault("sources", [])
    return data


def _academic(mem: LabMembership) -> dict:
    """给人看的学术档案。阅读习惯只给本人的检索用，不出现在这里。"""
    data = _academic_raw(mem)
    data.pop("_habits", None)
    return data


def _public_identity(mem: LabMembership) -> dict:
    data = _obj(getattr(mem, "web_identity_json", "") or "")
    snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
    return {
        "confirmed": bool(data.get("confirmed_at")),
        "confirmed_at": data.get("confirmed_at") or "",
        "snapshot": snap,
        "ids": data.get("ids") or {},
    }


def _source_status(mem: LabMembership) -> list[dict]:
    data = _obj(getattr(mem, "web_identity_json", "") or "")
    rows = data.get("sources")
    return rows if isinstance(rows, list) else []


def _cap_academic(field: str, value):
    if field in {"aliases", "files"} and isinstance(value, list):
        return [str(x).strip()[:200] for x in value if str(x).strip()][:20]
    if field in {"past_affiliations", "education", "works", "sources"} and isinstance(value, list):
        out = []
        for item in value[:12]:
            if isinstance(item, dict):
                out.append({str(k)[:40]: str(v)[:300] for k, v in list(item.items())[:8]})
            elif str(item).strip():
                out.append(str(item).strip()[:300])
        return out
    if field == "links" and isinstance(value, dict):
        return {str(k)[:40]: str(v)[:300] for k, v in list(value.items())[:12]}
    return str(value or "")[:1000]


def suggest_profile(db: Session, lab_id: str, user_id: str) -> dict:
    """只出草稿，不写库。"""
    prof = member_profile(db, lab_id, user_id) or {}
    name = prof.get("display_name") or ""
    draft = {"name_en": "", "affiliation": "", "links": {}, "works": [], "topics": [], "note": "这是草稿，保存后才会写入。"}
    try:
        from fengsuan.app.services.web import scholarly

        people = scholarly.openalex_search_authors(name) if name else []
        if people:
            top = people[0]
            draft["affiliation"] = (top.get("affiliations") or [""])[0]
            draft["topics"] = top.get("topics") or []
            if top.get("openalex_id"):
                draft["links"]["openalex"] = "https://openalex.org/" + top["openalex_id"]
            if top.get("orcid"):
                draft["links"]["orcid"] = "https://orcid.org/" + top["orcid"]
            draft["works"] = [
                {"title": row.get("title") or "", "doi": row.get("doi") or ""}
                for row in scholarly.work_list(scholarly.openalex_author_works(top.get("openalex_id") or "", limit=3))
            ]
    except Exception:
        draft["note"] = "网上这次没对上，草稿是空的。保存前可以自己填。"
    return draft


def confirm_identity(db: Session, mem: LabMembership, *, user_id: str, ids: dict, reject: bool = False) -> dict:
    from fengsuan.app.db.base import utcnow

    blob = _obj(getattr(mem, "web_identity_json", "") or "")
    if reject:
        token = str(ids.get("openalex_id") or ids.get("scholar_id") or ids.get("orcid") or "")
        rejected = list(blob.get("rejected") or [])
        if token and token not in rejected:
            rejected.append(token)
        blob["rejected"] = rejected[:20]
    else:
        blob["ids"] = {k: str(ids.get(k) or "")[:200] for k in ("openalex_id", "orcid", "scholar_id", "dblp", "homepage")}
        blob["confirmed_by"] = user_id
        blob["confirmed_at"] = utcnow().isoformat()
        blob["snapshot"] = {"name": ids.get("name") or "", **blob["ids"]}
    mem.web_identity_json = json.dumps(blob, ensure_ascii=False)
    db.flush()
    return _public_identity(mem)


def refresh_member_sources(db: Session, mem: LabMembership, *, user_id: str) -> list[dict]:
    """读取成员允许助手看的网站，写入状态和知识库。"""
    from fengsuan.app.services.kb import index_member_note
    from fengsuan.app.services.web.fetch import fetch_page

    academic = _academic(mem)
    blob = _obj(getattr(mem, "web_identity_json", "") or "")
    status = []
    for row in academic.get("sources") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url or str(row.get("allow") or row.get("允许读取") or "1") in {"0", "false", "否"}:
            status.append({"url": url, "state": "skipped"})
            continue
        page = fetch_page(url, strip=True)
        if not page.get("ok"):
            status.append({"url": url, "state": "failed", "error": page.get("error") or ""})
            continue
        text = (page.get("text") or "")[:4000]
        doc_id = ""
        try:
            doc_id = index_member_note(db, mem.lab_id, user_id, url=url, text=text or page.get("title") or url, member_user_id=mem.user_id)
        except Exception:
            doc_id = ""
        status.append({"url": url, "state": "read", "title": page.get("title") or "", "summary": text[:240], "doc_id": doc_id})
    blob["sources"] = status
    mem.web_identity_json = json.dumps(blob, ensure_ascii=False)
    db.flush()
    return status


def scan_one_member_papers(db: Session) -> int:
    """已确认身份的成员，发现新论文就提醒本人。一次只看一位。"""
    from fengsuan.app.db.base import utcnow
    from fengsuan.app.services.notify import notify_users
    from fengsuan.app.services.web import scholarly

    rows = (
        db.query(LabMembership)
        .filter(LabMembership.web_identity_json.contains("confirmed_at"))
        .all()
    )
    for mem in rows:
        blob = _obj(getattr(mem, "web_identity_json", "") or "")
        ids = blob.get("ids") if isinstance(blob.get("ids"), dict) else {}
        aid = str(ids.get("openalex_id") or "")
        if not aid or not blob.get("confirmed_at"):
            continue
        stamp = str(blob.get("papers_checked_at") or "")
        if stamp:
            try:
                then = datetime.fromisoformat(stamp)
            except ValueError:
                then = None
            if then is not None:
                if then.tzinfo is None:
                    then = then.replace(tzinfo=timezone.utc)
                if (utcnow() - then).days < 7:
                    continue
        try:
            works = scholarly.work_list(scholarly.openalex_author_works(aid, years=1, limit=5))
        except Exception:
            works = []
        known = set(blob.get("seen_titles") or [])
        fresh = [row for row in works if row.get("title") and row["title"] not in known]
        blob["seen_titles"] = list(known | {row.get("title") for row in works if row.get("title")})[:40]
        blob["papers_checked_at"] = utcnow().isoformat()
        mem.web_identity_json = json.dumps(blob, ensure_ascii=False)
        if fresh:
            title = fresh[0].get("title") or "新论文"
            notify_users(
                db,
                user_ids=[mem.user_id],
                ntype="member.paper",
                title="你有新论文",
                content=f"《{title}》要收进组里吗？",
                lab_id=mem.lab_id,
                payload={"title": title, "doi": fresh[0].get("doi") or ""},
            )
        db.flush()
        return 1
    return 0
