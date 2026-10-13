"""user avatar

Revision ID: b3e8a5d1f720
Revises: c7d2f08a19e4
Create Date: 2026-10-13 09:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b3e8a5d1f720"
down_revision = "c7d2f08a19e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("avatar_source", sa.String(length=16), server_default="none", nullable=False),
    )
    op.add_column("users", sa.Column("avatar_content_type", sa.String(length=32), nullable=True))
    op.add_column(
        "users", sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar_content_type")
    op.drop_column("users", "avatar_source")
