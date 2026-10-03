"""Data leaving Galaxy as an archive or to a remote target: exports and collection downloads."""

from typing import Literal

from galaxy.managers.audit_actions.base import (
    AuditDetails,
    ObjectDescriber,
)


# Placeholder until this family's call sites land; replace with the real details.
class HistoryExportDetails(AuditDetails):
    pass


ExportAction = Literal["history.export"]

ACTIONS: dict[str, type[AuditDetails]] = {
    "history.export": HistoryExportDetails,
}

DESCRIBERS: list[ObjectDescriber] = []
