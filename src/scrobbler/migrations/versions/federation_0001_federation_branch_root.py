"""federation branch root

Root of the federation branch: federation_* tables are added in revisions on this
branch (flask db migrate --head federation@head) so they stay independent of core
migrations. Intentionally empty.

Revision ID: federation_0001
Revises: 
Create Date: 2026-09-25 19:58:27.552417

"""
# revision identifiers, used by Alembic.
revision = 'federation_0001'
down_revision = None
branch_labels = ('federation',)
# Federation tables reference users, created by the core's first revision.
depends_on = 'd6113d27cce6'


def upgrade():
    pass


def downgrade():
    pass
