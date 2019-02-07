"""
Migration script to (a) create a table for PluggedMedia and (b) extend the HDA table
linking datasets to plugged media.
"""
from __future__ import print_function

import datetime
import logging

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, MetaData, Numeric, Table, TEXT


now = datetime.datetime.utcnow
log = logging.getLogger(__name__)
metadata = MetaData()

# Tables to add

PluggedMediaTable = Table(
    "plugged_media", metadata,
    Column("id", Integer, primary_key=True),
    Column("create_time", DateTime, default=now),
    Column("update_time", DateTime, default=now, onupdate=now),
    Column("user_id", Integer, ForeignKey("galaxy_user.id"), index=True),
    Column("usage", Numeric(15, 0), default=0),
    Column("order", Integer),
    Column("quota", Numeric(15, 0)),
    Column("category", TEXT, default="local"),
    Column("path", TEXT),
    Column("authz_id", Integer, ForeignKey("cloudauthz.id")),
    Column("deleted", Boolean, index=True, default=False),
    Column("purged", Boolean, index=True, default=False),
    Column("purgeable", Boolean, default=True))

PluggedMediaDatasetAssociation = Table(
    "plugged_media_dataset_association", metadata,
    Column("id", Integer, primary_key=True),
    Column("dataset_id", Integer, ForeignKey("dataset.id"), index=True),
    Column("plugged_media_id", Integer, ForeignKey("plugged_media.id"), index=True),
    Column("create_time", DateTime, default=now),
    Column("update_time", DateTime, default=now, onupdate=now),
    Column("deleted", Boolean, index=True, default=False),
    Column("purged", Boolean, index=True, default=False),
    Column("dataset_path_on_media", TEXT))


def upgrade(migrate_engine):
    print(__doc__)
    metadata.bind = migrate_engine
    metadata.reflect()

    # Create PluggedMedia table
    try:
        PluggedMediaTable.create()
    except Exception as e:
        log.error("Creating plugged_media table failed: %s" % str(e))

    # Create PluggedMedia Association table.
    try:
        PluggedMediaDatasetAssociation.create()
    except Exception as e:
        log.error("Creating plugged_media_dataset_association table failed: %s" % str(e))


def downgrade(migrate_engine):
    metadata.bind = migrate_engine
    metadata.reflect()

    # Drop plugged_media table
    try:
        PluggedMediaTable.drop()
    except Exception as e:
        log.debug("Dropping plugged_media table failed: %s" % str(e))

    try:
        PluggedMediaDatasetAssociation.drop()
    except Exception as e:
        log.error("Dropping plugged_media_dataset_association table failed: %s" % str(e))
