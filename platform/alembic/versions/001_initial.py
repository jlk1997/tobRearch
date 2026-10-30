"""empty

Revision ID: 001_initial
Revises:
Create Date: 2026-09-05
"""

from alembic import op

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from fengsuan.app.db.base import Base
    from fengsuan.app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from fengsuan.app.db.base import Base
    from fengsuan.app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
