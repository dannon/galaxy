"""add expiration_time to user_role_association

Revision ID: 16c8e5e36a53
Revises: 98a36835baf5
Create Date: 2026-11-16 21:00:00.000000

"""

from sqlalchemy import (
    Column,
    DateTime,
)

from galaxy.model.migrations.util import (
    add_column,
    create_index,
    drop_column,
    drop_index,
)

# revision identifiers, used by Alembic.
revision = "16c8e5e36a53"
down_revision = "98a36835baf5"
branch_labels = None
depends_on = None


# database object names used in this revision
table_name = "user_role_association"
column_name = "expiration_time"
index_name = "ix_user_role_association_expiration_time"


def upgrade() -> None:
    add_column(table_name, Column(column_name, DateTime, nullable=True))
    create_index(index_name, table_name, [column_name])


def downgrade() -> None:
    drop_index(index_name, table_name)
    drop_column(table_name, column_name)
