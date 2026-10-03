"""The audit action registry.

Every action an event can name, with the one details type allowed for it. Each
family of call sites owns one module here and adds its actions there rather than
inventing names at the call site. ``AuditAction`` is the union of the families, so
mypy catches a misspelled action.
"""

from typing import Literal

from galaxy.managers.audit_actions import (
    datasets,
    exports,
    sharing,
)
from galaxy.managers.audit_actions.base import (
    AuditDetails,
    AuditObject,
    ObjectDescriber,
)
from galaxy.managers.audit_actions.datasets import DatasetContentDetails

AuditAction = Literal[datasets.DatasetAction, exports.ExportAction, sharing.SharingAction]

AUDIT_ACTIONS: dict[str, type[AuditDetails]] = {**datasets.ACTIONS, **exports.ACTIONS, **sharing.ACTIONS}

OBJECT_DESCRIBERS: list[ObjectDescriber] = [*datasets.DESCRIBERS, *exports.DESCRIBERS, *sharing.DESCRIBERS]

__all__ = (
    "AUDIT_ACTIONS",
    "AuditAction",
    "AuditDetails",
    "AuditObject",
    "DatasetContentDetails",
    "OBJECT_DESCRIBERS",
    "ObjectDescriber",
)
