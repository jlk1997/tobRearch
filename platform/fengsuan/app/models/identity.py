from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Org(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "orgs"

    name: Mapped[str] = mapped_column(String(200), default="默认机构")
    status: Mapped[str] = mapped_column(String(20), default="approved", index=True)
    kind: Mapped[str] = mapped_column(String(40), default="other")
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    contact_phone: Mapped[str] = mapped_column(String(60), default="")
    apply_note: Mapped[str] = mapped_column(Text, default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    # 不挂 users FK，避免与 users.org_id 循环依赖；由服务层保证引用有效。
    applicant_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "users"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base, UUIDPrimaryKey):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255), default="")


class Permission(Base, UUIDPrimaryKey):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class Invite(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "invites"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(20))
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
