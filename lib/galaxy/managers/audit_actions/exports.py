"""Data leaving Galaxy as an archive or to a remote target: exports and collection downloads.

Exports run later in a Celery task (or, for the legacy history export, a job), so the
``*.export`` event is recorded by the request that asks for the work, with the ids
that join it to what happens next: ``task_id``/``job_id`` to the work itself, and
``storage_request_id`` to the ``archive.download`` event of whoever later fetches a
prepared download.
"""

from typing import (
    Any,
    ClassVar,
    Literal,
)
from urllib.parse import urlsplit

from pydantic import field_validator

from galaxy import model
from galaxy.managers.audit_actions.base import (
    AuditDetails,
    AuditObject,
    ObjectDescriber,
)

# Matches the clipping the audit service applies to the names it describes itself.
MAX_NAME_LENGTH = 256


def sanitize_target_uri(uri: str | None) -> str | None:
    """``scheme://host[:port]/path`` of an export target, without credentials, query or fragment.

    Target URIs are user supplied and some file sources accept credentials in them;
    an audit stream must never carry those. Anything that doesn't parse as a URI with
    an authority is reduced to its scheme rather than guessed at.
    """
    if uri is None:
        return None
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if not parts.netloc:
        # e.g. "user:secret@host/path" parses as scheme "user"; keep nothing that could be a secret.
        return f"{scheme}:" if scheme and "@" not in uri else None
    if "@" in uri[uri.index("//") + 2 + len(parts.netloc) :]:
        # Userinfo holding an unescaped "/", "?" or "#" ends the authority early
        # ("ftp://user:20/24@host"), leaving the secret in what parses as the path.
        return f"{scheme}:"
    # Not .hostname: that lowercases, and file source ids in the authority can be case-sensitive.
    host = parts.netloc.rpartition("@")[2]
    _, colon, port = host.rpartition(":")
    if colon and not host.endswith("]") and not port.isdigit():
        return f"{scheme}:"
    # ";params" can carry session tokens (";jsessionid=...") just like a query string.
    return f"{scheme}://{host}{parts.path.partition(';')[0]}"


class ExportDetails(AuditDetails):
    """A request to export an object as a download or to a remote file source."""

    destination: Literal["download", "remote"]
    # The model store format, or "zip" for a collection archive.
    format: str | None = None
    # Remote target, credentials and query stripped (see sanitize_target_uri).
    target: str | None = None
    include_files: bool | None = None
    include_hidden: bool | None = None
    include_deleted: bool | None = None
    # Celery task that does the work; Galaxy's task id, also on store_export_association.task_uuid.
    task_id: str | None = None
    # Short-term storage request the download will be served from.
    storage_request_id: str | None = None
    # The legacy history export runs as a job instead of a task.
    job_id: int | None = None

    @field_validator("target")
    @classmethod
    def _strip_credentials(cls, value: str | None) -> str | None:
        return sanitize_target_uri(value)


class ArchiveDetails(AuditDetails):
    """An archive built and served in the request itself."""

    identifying_fields: ClassVar[frozenset[str]] = frozenset({"filename"})

    # The archive name the client asked for.
    filename: str | None = None


class ArchiveDownloadDetails(AuditDetails):
    """A previously prepared export handed to the client."""

    source: Literal["short_term_storage", "job_export"]
    storage_request_id: str | None = None


ExportAction = Literal[
    "history.export",
    "dataset.export",
    "collection.export",
    "invocation.export",
    "history.download",
    "collection.download",
    "archive.download",
]

ACTIONS: dict[str, type[AuditDetails]] = {
    "history.export": ExportDetails,
    "dataset.export": ExportDetails,
    "collection.export": ExportDetails,
    "invocation.export": ExportDetails,
    "history.download": ArchiveDetails,
    "collection.download": ArchiveDetails,
    "archive.download": ArchiveDownloadDetails,
}


def _encode(audit_service: Any, object_id: int | None) -> str | None:
    return audit_service.security.encode_id(object_id) if object_id is not None else None


def _name(audit_service: Any, value: str | None) -> str | None:
    if not audit_service.include_names or value is None:
        return None
    return value[:MAX_NAME_LENGTH]


def describe_history(audit_service: Any, obj: Any) -> AuditObject | None:
    if not isinstance(obj, model.History):
        return None
    return AuditObject(
        type="history",
        id=obj.id,
        encoded_id=_encode(audit_service, obj.id),
        history_id=obj.id,
        owner_id=obj.user_id,
        history_name=_name(audit_service, obj.name),
    )


def describe_collection(audit_service: Any, obj: Any) -> AuditObject | None:
    if not isinstance(obj, model.HistoryDatasetCollectionAssociation):
        return None
    history = obj.history
    return AuditObject(
        type="hdca",
        id=obj.id,
        encoded_id=_encode(audit_service, obj.id),
        history_id=obj.history_id,
        owner_id=history.user_id if history is not None else None,
        name=_name(audit_service, obj.name),
        history_name=_name(audit_service, history.name) if history is not None else None,
    )


def describe_invocation(audit_service: Any, obj: Any) -> AuditObject | None:
    if not isinstance(obj, model.WorkflowInvocation):
        return None
    history = obj.history
    return AuditObject(
        type="invocation",
        id=obj.id,
        encoded_id=_encode(audit_service, obj.id),
        uuid=str(obj.uuid) if obj.uuid is not None else None,
        history_id=obj.history_id,
        owner_id=history.user_id if history is not None else None,
        history_name=_name(audit_service, history.name) if history is not None else None,
    )


def describe_history_export(audit_service: Any, obj: Any) -> AuditObject | None:
    if not isinstance(obj, model.JobExportHistoryArchive):
        return None
    history = obj.history
    return AuditObject(
        type="history_export",
        id=obj.id,
        encoded_id=_encode(audit_service, obj.id),
        dataset_id=obj.dataset_id,
        history_id=obj.history_id,
        owner_id=history.user_id if history is not None else None,
        history_name=_name(audit_service, history.name) if history is not None else None,
    )


DESCRIBERS: list[ObjectDescriber] = [
    describe_history,
    describe_collection,
    describe_invocation,
    describe_history_export,
]
