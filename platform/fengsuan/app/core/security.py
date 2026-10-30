"""JWT and encrypted API Key helpers."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet

from fengsuan.app.core.config import get_settings

TokenType = Literal["access", "refresh"]


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(subject: str, token_type: TokenType = "access") -> str:
    settings = get_settings()
    if token_type == "access":
        delta = timedelta(minutes=settings.jwt_expire_minutes)
    else:
        delta = timedelta(days=settings.jwt_refresh_days)
    payload = {
        "sub": subject,
        "type": token_type,
        "exp": datetime.now(timezone.utc) + delta,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str, expected: TokenType = "access") -> dict[str, Any]:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    if payload.get("type") != expected:
        raise jwt.InvalidTokenError("token type mismatch")
    return payload


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def mask_key(plain: str) -> str:
    if not plain:
        return ""
    if len(plain) <= 4:
        return "****"
    return "*" * (len(plain) - 4) + plain[-4:]
