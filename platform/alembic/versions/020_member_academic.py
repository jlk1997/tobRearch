"""成员学术档案与网上身份。加列，不建影子表。

Revision ID: 020_member_academic
Revises: 019_member_profile
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "020_member_academic"
down_revision = "019_member_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lab_memberships" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("lab_memberships")}
    for name in ("academic_json", "web_identity_json"):
        if name not in cols:
            op.add_column("lab_memberships", sa.Column(name, sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lab_memberships" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("lab_memberships")}
    for name in ("web_identity_json", "academic_json"):
        if name in cols:
            op.drop_column("lab_memberships", name)
