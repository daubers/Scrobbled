"""user album art

Revision ID: a41c9e2d7b53
Revises: 3ba21eb1ea0c
Create Date: 2026-10-06 21:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a41c9e2d7b53"
down_revision = "3ba21eb1ea0c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_album_art",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("artist", sa.Text(), nullable=False),
        sa.Column("album", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("search_artist", sa.Text(), nullable=True),
        sa.Column("search_album", sa.Text(), nullable=True),
        sa.Column("release_group_mbid", sa.String(length=36), nullable=True),
        sa.Column("content_type", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_album_art", schema=None) as batch_op:
        batch_op.create_index(
            "uq_user_album_art_user_artist_album_lower",
            ["user_id", sa.literal_column("lower(artist)"), sa.literal_column("lower(album)")],
            unique=True,
        )


def downgrade():
    with op.batch_alter_table("user_album_art", schema=None) as batch_op:
        batch_op.drop_index("uq_user_album_art_user_artist_album_lower")

    op.drop_table("user_album_art")
