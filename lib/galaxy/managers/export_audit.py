"""Audit events for requests that queue an export.

An export is prepared later, in a Celery task or a job, so nothing about the request
that asked for it reaches the work: there is no request scope in a worker. The event
is therefore recorded by the request, once the work is queued, carrying the id that
joins it to the task or job. Like an :class:`~galaxy.managers.audit.AuditAttempt`,
an :class:`ExportAudit` ends in exactly one event, whether the request was refused,
failed, or queued the work -- unless the call site says no export was started.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from galaxy.managers.audit import (
    AuditOutcome,
    AuditReason,
    AuditService,
    AuditStage,
    classify_failure,
)
from galaxy.managers.audit_actions import AuditObject
from galaxy.managers.audit_actions.exports import (
    ExportAction,
    ExportDetails,
)
from galaxy.schema.schema import StoreExportPayload


def store_export_details(payload: StoreExportPayload, target_uri: str | None = None) -> ExportDetails:
    """Details of a model store export: a download unless it is written to ``target_uri``."""
    return ExportDetails(
        destination="remote" if target_uri is not None else "download",
        # Payload models keep enum values, not members.
        format=str(getattr(payload.model_store_format, "value", payload.model_store_format)),
        target=target_uri,
        include_files=payload.include_files,
        include_hidden=payload.include_hidden,
        include_deleted=payload.include_deleted,
    )


class ExportAudit:
    """One request to start an export, recorded when the request has queued the work."""

    def __init__(
        self,
        audit: AuditService,
        action: ExportAction,
        requested: AuditObject,
        details: ExportDetails,
    ) -> None:
        self._audit = audit
        self._action = action
        self.active = audit.wants(action)
        # Nothing to settle when the action isn't audited; every method is then a no-op.
        self.settled = not self.active
        self.stage: AuditStage = "authorize"
        self._details = details
        self._target: Any = requested
        if self.active and requested.encoded_id is None and requested.id is not None:
            try:
                self._target = requested.model_copy(update={"encoded_id": audit.security.encode_id(requested.id)})
            except Exception:
                # Never break the export over its audit event; the numeric id is still there.
                pass

    def authorized(self, obj: Any) -> None:
        """Access to ``obj`` was granted; describe it now, before a commit expires it."""
        if not self.active:
            return
        try:
            self._target = self._audit.describe(obj)
        except Exception:
            # record() describes it again and reports the failure if it still can't.
            self._target = obj
        self.stage = "prepare"

    def queued(self, *, task_id: str | None = None, storage_request_id: Any = None, job_id: int | None = None) -> None:
        """The work was queued: record the request as a success with the ids that join it to the work."""
        if self.settled:
            return
        updates: dict[str, Any] = {"task_id": task_id, "job_id": job_id}
        if storage_request_id is not None:
            updates["storage_request_id"] = str(storage_request_id)
        details = ExportDetails(**{**self._details.model_dump(), **updates})
        self._settle("success", None, details)

    def not_started(self) -> None:
        """The request was valid but started no export (an earlier one is reused); record nothing."""
        self.settled = True

    def _settle(self, outcome: AuditOutcome, reason: AuditReason | None, details: ExportDetails | None = None) -> None:
        self.settled = True
        self._audit.record(
            self._action, self._target, outcome, details=details or self._details, reason=reason, stage=self.stage
        )

    @contextmanager
    def guard(self) -> Iterator["ExportAudit"]:
        """Settle from whatever escapes the block, unless the request already did."""
        try:
            yield self
        except BaseException as exc:
            if not self.settled:
                outcome, reason = classify_failure(exc)
                self._settle(outcome, reason)
            raise
        if not self.settled:
            # The call site returned without saying whether it queued anything.
            self._settle("error", "internal_error")
