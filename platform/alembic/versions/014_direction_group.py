"""Direction as research-group unit: parent, multi-link, membership status.

Revision ID: 014_direction_group
Revises: 013_literature_file_hash
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "014_direction_group"
down_revision = "013_literature_file_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "lab_memberships" in tables:
        cols = {c["name"] for c in insp.get_columns("lab_memberships")}
        if "status" not in cols:
            op.add_column(
                "lab_memberships",
                sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            )
    if "research_directions" in tables:
        cols = {c["name"] for c in insp.get_columns("research_directions")}
        if "parent_id" not in cols:
            op.add_column(
                "research_directions",
                sa.Column("parent_id", sa.String(36), sa.ForeignKey("research_directions.id"), nullable=True),
            )
    if "membership_direction_links" not in tables:
        op.create_table(
            "membership_direction_links",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id"), nullable=False, index=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("direction_id", sa.String(36), sa.ForeignKey("research_directions.id"), nullable=False, index=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.UniqueConstraint("lab_id", "user_id", "direction_id"),
        )
        op.create_index("ix_mdl_lab_user", "membership_direction_links", ["lab_id", "user_id"])
        op.create_index("ix_mdl_direction", "membership_direction_links", ["direction_id"])
        if "lab_memberships" in tables:
            import uuid

            rows = bind.execute(
                sa.text(
                    "SELECT lab_id, user_id, direction_id FROM lab_memberships "
                    "WHERE direction_id IS NOT NULL AND direction_id != ''"
                )
            ).fetchall()
            for lab_id, user_id, direction_id in rows:
                bind.execute(
                    sa.text(
                        "INSERT INTO membership_direction_links (id, lab_id, user_id, direction_id, is_primary) "
                        "VALUES (:id, :lab_id, :user_id, :direction_id, :is_primary)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "lab_id": lab_id,
                        "user_id": user_id,
                        "direction_id": direction_id,
                        "is_primary": True,
                    },
                )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "membership_direction_links" in insp.get_table_names():
        op.drop_index("ix_mdl_direction", table_name="membership_direction_links")
        op.drop_index("ix_mdl_lab_user", table_name="membership_direction_links")
        op.drop_table("membership_direction_links")
    if "research_directions" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("research_directions")}
        if "parent_id" in cols:
            op.drop_column("research_directions", "parent_id")
    if "lab_memberships" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("lab_memberships")}
        if "status" in cols:
            op.drop_column("lab_memberships", "status")
