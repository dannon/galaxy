"""Audit events for requests that queue an export.

An export is prepared later, in a Celery task or a job, so nothing about the request
that asked for it reaches the work: there is no request scope in a worker. The event
is therefore recorded by the request, through an ordinary
:class:`~galaxy.managers.audit.AuditAttempt`, once the work is queued, carrying the id
that joins it to the task or job. A request that starts no export (an earlier one is
reused) withdraws its attempt.
"""

from typing import Any

from galaxy.managers.audit import AuditAttempt
from galaxy.managers.audit_actions.exports import (
    ExportDetails,
    storage_request_digest,
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


def export_queued(
    attempt: AuditAttempt,
    *,
    task_id: str | None = None,
    storage_request_id: Any = None,
    job_id: int | None = None,
) -> None:
    """The work was queued: the request succeeded, with the ids that join it to the work."""
    attempt.succeeded(
        details=lambda: ExportDetails(
            task_id=task_id,
            job_id=job_id,
            storage_request_digest=(
                storage_request_digest(storage_request_id) if storage_request_id is not None else None
            ),
        )
    )
