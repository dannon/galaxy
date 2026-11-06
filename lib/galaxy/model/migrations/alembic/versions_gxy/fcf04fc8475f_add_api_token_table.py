"""add api_token table and history.api_token_id

Revision ID: fcf04fc8475f
Revises: 64d49ad328d4
Create Date: 2026-11-05 13:00:00.000000

"""

import sqlalchemy as sa

from galaxy.model.custom_types import (
    JSONType,
    TrimmedString,
)
from galaxy.model.database_object_names import (
    build_foreign_key_name,
    build_index_name,
)
from galaxy.model.migrations.util import (
    add_column,
    create_foreign_key,
    create_index,
    create_table,
    drop_column,
    drop_constraint,
    drop_index,
    drop_table,
    transaction,
)

# revision identifiers, used by Alembic.
revision = "fcf04fc8475f"
down_revision = "64d49ad328d4"
branch_labels = None
depends_on = None

token_table = "api_token"
history_table = "history"
history_column = "api_token_id"
history_index = build_index_name(history_table, history_column)
history_fk = build_foreign_key_name(history_table, history_column)


def upgrade() -> None:
    with transaction():
        _upgrade()


def downgrade() -> None:
    with transaction():
        _downgrade()


def _upgrade() -> None:
    create_table(
        token_table,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("create_time", sa.DateTime),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("galaxy_user.id"), index=True, nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, index=True, nullable=False),
        sa.Column("token_hint", sa.String(16), nullable=False),
        sa.Column("name", TrimmedString(255), nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
        sa.Column("constraints", JSONType, nullable=True),
        sa.Column("expires_at", sa.DateTime, index=True, nullable=True),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=True),
    )
    add_column(history_table, sa.Column(history_column, sa.Integer, nullable=True))
    create_index(history_index, history_table, [history_column])
    create_foreign_key(history_fk, history_table, token_table, [history_column], ["id"], ondelete="SET NULL")


def _downgrade() -> None:
    drop_constraint(history_fk, history_table)
    drop_index(history_index, history_table)
    drop_column(history_table, history_column)
    drop_table(token_table)
