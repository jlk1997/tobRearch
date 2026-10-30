from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, is_admin, require_teacher
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.security import decrypt_secret, encrypt_secret
from fengsuan.app.db.session import get_db
from fengsuan.app.models.credential import GPUProfile, LLMCredential
from fengsuan.app.models.identity import User
from fengsuan.app.schemas.common import CredentialUpsert
from fengsuan.app.services.activity import emit_audit
from fengsuan.app.services.llm import ping_credential

router = APIRouter(tags=["credentials"])


def _cred_out(row: LLMCredential | None) -> dict:
    if row is None:
        return {"configured": False}
    last4 = ""
    try:
        last4 = decrypt_secret(row.api_key_encrypted)[-4:] if row.api_key_encrypted else ""
    except Exception:
        last4 = ""
    return {
        "configured": True,
        "provider": row.provider,
        "base_url": row.base_url,
        "model_chat": row.model_chat,
        "model_vision": row.model_vision,
        "model_embed": getattr(row, "model_embed", "") or "",
        "model_rerank": getattr(row, "model_rerank", "") or "",
        "kind": getattr(row, "kind", None) or "cloud",
        "api_key_masked": ("*" * 8 + last4) if last4 else "",
        "is_active": row.is_active,
    }


def _upsert(db: Session, owner_type: str, owner_id: str, body: CredentialUpsert) -> LLMCredential:
    row = (
        db.query(LLMCredential)
        .filter(LLMCredential.owner_type == owner_type, LLMCredential.owner_id == owner_id)
        .first()
    )
    if row is None:
        row = LLMCredential(owner_type=owner_type, owner_id=owner_id)
        db.add(row)
    row.provider = body.provider
    row.base_url = body.base_url
    row.model_chat = body.model_chat
    row.model_vision = body.model_vision
    row.model_embed = body.model_embed or ""
    row.model_rerank = body.model_rerank or ""
    row.kind = body.kind or "cloud"
    if body.api_key:
        row.api_key_encrypted = encrypt_secret(body.api_key)
    return row


@router.get("/me/llm-credentials")
def get_my_key(user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> dict:
    row = (
        db.query(LLMCredential)
        .filter(LLMCredential.owner_type == "user", LLMCredential.owner_id == user.id)
        .first()
    )
    return _cred_out(row)


@router.put("/me/llm-credentials")
def put_my_key(
    body: CredentialUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    row = _upsert(db, "user", user.id, body)
    emit_audit(db, user_id=user.id, action="credential.update", resource="llm_credential", resource_id=row.id)
    db.commit()
    return _cred_out(row)


@router.post("/me/llm-credentials/test")
def test_my_key(user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> dict:
    row = (
        db.query(LLMCredential)
        .filter(LLMCredential.owner_type == "user", LLMCredential.owner_id == user.id)
        .first()
    )
    if row is None or not row.api_key_encrypted:
        raise ClassifiedHTTPException(400, "API Key is not configured", "permission")
    return ping_credential(row)


@router.post("/labs/{lab_id}/llm-credentials/test")
def test_lab_key(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    from fengsuan.app.services.llm import visible_lab_credential

    row, _shared = visible_lab_credential(db, lab_id)
    if row is None or not row.api_key_encrypted:
        raise ClassifiedHTTPException(400, "API Key is not configured", "permission")
    return ping_credential(row)


@router.get("/labs/{lab_id}/llm-credentials")
def get_lab_key(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    from fengsuan.app.services.llm import visible_lab_credential

    row, shared = visible_lab_credential(db, lab_id)
    out = _cred_out(row)
    if shared and out.get("configured"):
        out["api_key_masked"] = "********"
        out["shared"] = True
    return out


@router.put("/labs/{lab_id}/llm-credentials")
def put_lab_key(
    lab_id: str,
    body: CredentialUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    from fengsuan.app.services.seed import is_demo_lab
    from fengsuan.app.models.lab import Lab

    lab = db.get(Lab, lab_id)
    if is_demo_lab(lab):
        raise HTTPException(403, detail="体验实验室使用已经配好的模型，不能在这里改密钥")
    row = _upsert(db, "lab", lab_id, body)
    emit_audit(db, user_id=user.id, action="credential.update", resource="lab_llm_credential", resource_id=lab_id)
    db.commit()
    return _cred_out(row)


@router.get("/admin/gpu-profiles")
def list_gpu(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    if not is_admin(db, user):
        raise HTTPException(403, detail="\u4ec5\u7ba1\u7406\u5458\u53ef\u914d\u7f6e GPU")
    from fengsuan.app.services.reproduce import (
        awaiting_gpu_counts,
        awaiting_gpu_items,
        gpu_profile_feedback,
        pending_without_worker_counts,
        pending_without_worker_items,
    )

    counts = awaiting_gpu_counts(db)
    waiting = awaiting_gpu_items(db, include_blocked=False, limit=20)
    pending = pending_without_worker_counts(db)
    queued = pending_without_worker_items(db, limit=20)
    feedback = gpu_profile_feedback(**counts, **pending)
    rows = db.query(GPUProfile).all()
    return [
        {
            "id": g.id,
            "name": g.name,
            "mode": g.mode,
            "docker_gpus": g.docker_gpus,
            "external_endpoint": g.external_endpoint,
            "is_default": g.is_default,
            **counts,
            **pending,
            "waiting": waiting,
            "pending_without_worker": queued,
            "feedback": feedback,
        }
        for g in rows
    ]


@router.put("/admin/gpu-profiles")
def upsert_gpu(
    body: dict,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="\u4ec5\u7ba1\u7406\u5458\u53ef\u914d\u7f6e GPU")
    row = db.query(GPUProfile).filter(GPUProfile.is_default.is_(True)).first()
    if row is None:
        row = GPUProfile(name="default", is_default=True)
        db.add(row)
    mode = body.get("mode", row.mode)
    if mode not in {"off", "internal", "external"}:
        raise HTTPException(400, detail="GPU \u6a21\u5f0f\u65e0\u6548")
    row.mode = mode
    row.docker_gpus = body.get("docker_gpus", row.docker_gpus)
    row.external_endpoint = body.get("external_endpoint", row.external_endpoint)
    emit_audit(db, user_id=user.id, action="gpu.update", resource="gpu_profile", resource_id=row.id)
    db.commit()
    from fengsuan.app.services.reproduce import (
        awaiting_gpu_counts,
        awaiting_gpu_items,
        gpu_profile_feedback,
        pending_without_worker_counts,
        pending_without_worker_items,
        release_awaiting_gpu,
    )

    released = {"released_research": 0, "released_reproduce": 0}
    if mode in {"internal", "external"}:
        released = release_awaiting_gpu(db)
    counts = awaiting_gpu_counts(db)
    waiting = awaiting_gpu_items(db, include_blocked=False, limit=20)
    pending = pending_without_worker_counts(db)
    queued = pending_without_worker_items(db, limit=20)
    return {
        "ok": True,
        "mode": row.mode,
        **counts,
        **pending,
        **released,
        "waiting": waiting,
        "pending_without_worker": queued,
        "feedback": gpu_profile_feedback(**counts, **pending, **released),
    }


@router.get("/admin/llm-credentials")
def get_platform_key(user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="\u4ec5\u7ba1\u7406\u5458\u53ef\u914d\u7f6e\u5e73\u53f0\u9ed8\u8ba4\u6a21\u578b")
    row = (
        db.query(LLMCredential)
        .filter(LLMCredential.owner_type == "platform", LLMCredential.owner_id == "platform")
        .first()
    )
    return _cred_out(row)


@router.put("/admin/llm-credentials")
def put_platform_key(
    body: CredentialUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="\u4ec5\u7ba1\u7406\u5458\u53ef\u914d\u7f6e\u5e73\u53f0\u9ed8\u8ba4\u6a21\u578b")
    row = _upsert(db, "platform", "platform", body)
    emit_audit(db, user_id=user.id, action="credential.update", resource="platform_llm_credential", resource_id=row.id)
    db.commit()
    return _cred_out(row)


@router.post("/admin/llm-credentials/test")
def test_platform_key(user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员可配置平台默认模型")
    row = (
        db.query(LLMCredential)
        .filter(LLMCredential.owner_type == "platform", LLMCredential.owner_id == "platform")
        .first()
    )
    if row is None or not row.api_key_encrypted:
        raise ClassifiedHTTPException(400, "API Key is not configured", "permission")
    return ping_credential(row)
