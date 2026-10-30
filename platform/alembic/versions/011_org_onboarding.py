"""Org onboarding status and apply fields.

Revision ID: 011_org_onboarding
Revises: 010_notification_payload
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "011_org_onboarding"
down_revision = "010_notification_payload"
branch_labels = None
depends_on = None

_COLS = {
    "status": sa.Column("status", sa.String(20), nullable=False, server_default="approved"),
    "kind": sa.Column("kind", sa.String(40), nullable=False, server_default="other"),
    "contact_name": sa.Column("contact_name", sa.String(120), nullable=False, server_default=""),
    "contact_phone": sa.Column("contact_phone", sa.String(60), nullable=False, server_default=""),
    "apply_note": sa.Column("apply_note", sa.Text(), nullable=False, server_default=""),
    "review_note": sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
    "applicant_user_id": sa.Column("applicant_user_id", sa.String(36), nullable=True),
    "reviewed_by": sa.Column("reviewed_by", sa.String(36), nullable=True),
    "reviewed_at": sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "orgs" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("orgs")}
    for name, col in _COLS.items():
        if name not in existing:
            op.add_column("orgs", col)
    if "status" not in existing or True:
        op.execute(sa.text("UPDATE orgs SET status = 'approved' WHERE status IS NULL OR status = ''"))
    indexes = {ix["name"] for ix in insp.get_indexes("orgs")}
    if "ix_orgs_status" not in indexes:
        try:
            op.create_index("ix_orgs_status", "orgs", ["status"])
        except Exception:
            pass
    if "ix_orgs_applicant_user_id" not in indexes:
        try:
            op.create_index("ix_orgs_applicant_user_id", "orgs", ["applicant_user_id"])
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "orgs" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("orgs")}
    indexes = {ix["name"] for ix in insp.get_indexes("orgs")}
    if "ix_orgs_status" in indexes:
        op.drop_index("ix_orgs_status", table_name="orgs")
    if "ix_orgs_applicant_user_id" in indexes:
        op.drop_index("ix_orgs_applicant_user_id", table_name="orgs")
    for name in reversed(list(_COLS)):
        if name in existing:
            op.drop_column("orgs", name)
