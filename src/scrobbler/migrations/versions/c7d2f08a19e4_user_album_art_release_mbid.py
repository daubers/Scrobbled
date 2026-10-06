"""user album art release mbid

Revision ID: c7d2f08a19e4
Revises: a41c9e2d7b53
Create Date: 2026-10-06 23:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7d2f08a19e4"
down_revision = "a41c9e2d7b53"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_album_art", sa.Column("release_mbid", sa.String(length=36), nullable=True))


def downgrade():
    op.drop_column("user_album_art", "release_mbid")
