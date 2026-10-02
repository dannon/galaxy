"""add impersonated_by_user_id column to galaxy_session

Revision ID: 829a1fd26221
Revises: 64d49ad328d4
Create Date: 2026-10-02 12:00:00.000000

"""

from sqlalchemy import (
    Column,
    Integer,
)

from galaxy.model.migrations.util import (
    add_column,
    drop_column,
)

# revision identifiers, used by Alembic.
revision = "829a1fd26221"
down_revision = "64d49ad328d4"
branch_labels = None
depends_on = None


# database object names used in this revision
table_name = "galaxy_session"
column_name = "impersonated_by_user_id"


def upgrade() -> None:
    add_column(table_name, Column(column_name, Integer, nullable=True))


def downgrade() -> None:
    drop_column(table_name, column_name)
