"""federation share avatar

Revision ID: e5a91c3d7b08
Revises: 2533b727da79
Create Date: 2026-10-13 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e5a91c3d7b08"
down_revision = "2533b727da79"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("federation_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("share_avatar", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade():
    with op.batch_alter_table("federation_settings", schema=None) as batch_op:
        batch_op.drop_column("share_avatar")
