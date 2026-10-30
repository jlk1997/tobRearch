"""Pin core literature on direction links.

Revision ID: 012_literature_core
Revises: 011_org_onboarding
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "012_literature_core"
down_revision = "011_org_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "literature_direction_links" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("literature_direction_links")}
    if "is_core" not in existing:
        op.add_column(
            "literature_direction_links",
            sa.Column("is_core", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "literature_direction_links" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("literature_direction_links")}
    if "is_core" in existing:
        op.drop_column("literature_direction_links", "is_core")
