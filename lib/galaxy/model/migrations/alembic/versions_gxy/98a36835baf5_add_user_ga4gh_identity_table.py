"""add user_ga4gh_identity table

Revision ID: 98a36835baf5
Revises: 64d49ad328d4
Create Date: 2026-11-13 15:36:29.169691

"""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
)

from galaxy.model.database_object_names import build_unique_constraint_name
from galaxy.model.migrations.util import (
    create_table,
    create_unique_constraint,
    drop_table,
    transaction,
)

# revision identifiers, used by Alembic.
revision = "98a36835baf5"
down_revision = "64d49ad328d4"
branch_labels = None
depends_on = None

table_name = "user_ga4gh_identity"
issuer_sub_constraint_name = build_unique_constraint_name(table_name, ["issuer", "sub"])
user_issuer_constraint_name = build_unique_constraint_name(table_name, ["user_id", "issuer"])


def upgrade() -> None:
    with transaction():
        create_table(
            table_name,
            Column("id", Integer, primary_key=True),
            Column("user_id", Integer, ForeignKey("galaxy_user.id"), nullable=False),
            Column("issuer", Text, nullable=False),
            Column("sub", Text, nullable=False),
            Column("preferred_username", Text),
            Column("create_time", DateTime, nullable=False),
            Column("update_time", DateTime, nullable=False),
        )
        create_unique_constraint(issuer_sub_constraint_name, table_name, ["issuer", "sub"])
        create_unique_constraint(user_issuer_constraint_name, table_name, ["user_id", "issuer"])


def downgrade() -> None:
    drop_table(table_name)
