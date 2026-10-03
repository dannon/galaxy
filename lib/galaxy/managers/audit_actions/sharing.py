"""Data changing hands inside Galaxy: sharing, publishing, permissions and imports."""

from typing import Literal

from galaxy.managers.audit_actions.base import (
    AuditDetails,
    ObjectDescriber,
)


# Placeholder until this family's call sites land; replace with the real details.
class HistoryShareDetails(AuditDetails):
    pass


SharingAction = Literal["history.share"]

ACTIONS: dict[str, type[AuditDetails]] = {
    "history.share": HistoryShareDetails,
}

DESCRIBERS: list[ObjectDescriber] = []
