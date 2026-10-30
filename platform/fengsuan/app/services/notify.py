from __future__ import annotations

import json
import logging
import os
import smtplib
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.db.base import utcnow
from fengsuan.app.models.activity import Notification
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership

logger = logging.getLogger(__name__)

# 老师默认订阅（不含高频噪声）
TEACHER_VERBS = {
    "literature.added",
    "literature.imported_ext",
    "reading.started",
    "reading.finished",
    "note.created",
    "analysis.ready",
    "manuscript.created",
    "manuscript.updated",
    "manuscript.status_changed",
    "manuscript.commented",
    "kb.indexed",
    "run.started",
    "run.failed",
    "run.succeeded",
    "run.awaiting_gpu",
    "run.released",
    "reproduce.stage",
    "reproduce.failed",
    "reproduce.succeeded",
    "reproduce.awaiting_gpu",
    "reproduce.released",
    "task.assigned",
    "task.done",
    "meeting.minutes_ready",
    "work_log.created",
    "writing.outline",
    "writing.section",
    "language.done",
    "dataset.created",
    "note.updated",
    "file.uploaded",
    "milestone.created",
    "ask.lab",
}

STUDENT_VERBS = {
    "task.assigned",
    "manuscript.commented",
    "manuscript.status_changed",
    "run.failed",
    "run.succeeded",
    "reproduce.failed",
    "reproduce.succeeded",
    "milestone.created",
}

EMAIL_VERBS = {
    "task.assigned",
    "run.failed",
    "run.succeeded",
    "reproduce.failed",
    "reproduce.succeeded",
    "meeting.minutes_ready",
}


def notify_users(
    db: Session,
    *,
    user_ids: list[str],
    ntype: str,
    title: str,
    content: str,
    email: bool = False,
    object_type: str = "",
    object_id: str = "",
    lab_id: str = "",
    payload: dict | None = None,
) -> None:
    seen: set[str] = set()
    blob = json.dumps(payload or {}, ensure_ascii=False)
    for uid in user_ids:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        db.add(
            Notification(
                user_id=uid,
                type=ntype,
                title=title[:200],
                content=content,
                object_type=object_type or "",
                object_id=object_id or "",
                lab_id=lab_id or "",
                payload_json=blob,
                created_at=utcnow(),
            )
        )
        if email:
            user = db.get(User, uid)
            if user and user.email:
                send_mail(user.email, title, content)


def fanout_activity(
    db: Session,
    *,
    lab_id: str,
    actor_id: str,
    verb: str,
    summary: str,
    object_owner_id: str | None = None,
    assignee_id: str | None = None,
    object_type: str = "",
    object_id: str = "",
    payload: dict | None = None,
) -> None:
    if verb in {"presence.heartbeat", "kb.query", "language.done", "kb.summarize"}:
        return
    members = db.query(LabMembership).filter(LabMembership.lab_id == lab_id).all()
    teachers = [m.user_id for m in members if m.role == "teacher" and m.user_id != actor_id]
    recipients: list[str] = []
    use_email = verb in EMAIL_VERBS
    if verb in TEACHER_VERBS:
        recipients.extend(teachers)
    if verb in STUDENT_VERBS:
        if assignee_id:
            recipients.append(assignee_id)
        if object_owner_id and object_owner_id != actor_id:
            recipients.append(object_owner_id)
    if verb in {"run.awaiting_gpu", "reproduce.awaiting_gpu", "run.released", "reproduce.released"} and actor_id:
        recipients.append(actor_id)
    notify_users(
        db,
        user_ids=recipients,
        ntype=verb.split(".")[0],
        title=summary or verb,
        content=verb,
        email=use_email,
        object_type=object_type,
        object_id=object_id,
        lab_id=lab_id,
        payload=payload,
    )


def notification_extra(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def serialize_notification(n: Notification) -> dict:
    from fengsuan.app.services.insight import object_open_href

    extra = notification_extra(n.payload_json)
    out = {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "content": n.content,
        "verb": n.content,
        "is_read": n.is_read,
        "object_type": n.object_type,
        "object_id": n.object_id,
        "lab_id": n.lab_id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "href": object_open_href(n.lab_id, n.object_type, n.object_id, n.content or "", extra=extra),
    }
    if (n.content or "") in {"run.awaiting_gpu", "reproduce.awaiting_gpu"}:
        from fengsuan.app.services.reproduce import AWAIT_GPU_NEXT

        out["next_step"] = AWAIT_GPU_NEXT
    elif (n.content or "") in {"run.released", "reproduce.released"}:
        from fengsuan.app.services.reproduce import RELEASE_GPU_NEXT

        out["next_step"] = RELEASE_GPU_NEXT
    elif (n.content or "") == "analysis.ready" and (n.object_type or "") == "literature" and n.object_id:
        from fengsuan.app.services.analysis import analysis_ready_notice_followups

        chips = analysis_ready_notice_followups(n.object_id, n.lab_id or "")
        if chips:
            out["followups"] = chips
    if extra.get("comment_id"):
        out["comment_id"] = extra["comment_id"]
    if extra.get("note_id"):
        out["note_id"] = extra["note_id"]
    if extra.get("page_no") is not None:
        out["page_no"] = extra["page_no"]
    return out


def send_mail(to_addr: str, subject: str, body: str) -> bool:
    """H1: EMAIL_* env first; else reuse OpenLens SMTP in config.toml when present.

    Returns True only after a provider accepted the message. Tests set FENGSUN_SKIP_MAIL=1.
    """
    if os.environ.get("FENGSUN_SKIP_MAIL") == "1":
        logger.info("mail skipped (FENGSUN_SKIP_MAIL): %s -> %s", to_addr, subject)
        return False
    settings = get_settings()
    host = os.environ.get("EMAIL_HOST") or getattr(settings, "email_host", "") or ""
    if host:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[蜂算科研] {subject}"
            msg["From"] = os.environ.get("EMAIL_FROM") or os.environ.get("EMAIL_USER") or "noreply@localhost"
            msg["To"] = to_addr
            port = int(os.environ.get("EMAIL_PORT") or 587)
            user = os.environ.get("EMAIL_USER") or ""
            password = os.environ.get("EMAIL_PASSWORD") or ""
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.starttls()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
            return True
        except Exception as exc:
            logger.warning("mail failed: %s", exc)
            return False
    try:
        from openlens_ai.utils.config import Config
        from openlens_ai.utils.send_email import send_email

        cfg = Config.from_toml("config.toml")
        if getattr(getattr(cfg, "email_server", None), "smtp_server", None):
            send_email(cfg, f"[蜂算科研] {subject}", body, to_addr)
            return True
    except Exception as exc:
        logger.info("openlens mail unused: %s", exc)
    logger.info("mail skipped (no EMAIL_HOST): %s -> %s", to_addr, subject)
    return False
