"""Data leaving Galaxy as an archive or to a remote target: exports and collection downloads.

Exports run later in a Celery task (or, for the legacy history export, a job), so the
``*.export`` event is recorded by the request that asks for the work, with the ids
that join it to what happens next: ``task_id``/``job_id`` to the work itself, and
``storage_request_digest`` to the ``archive.download`` event of whoever later fetches
a prepared download.
"""

import hashlib
import re
import unicodedata
from typing import (
    Any,
    ClassVar,
    Literal,
)

from pydantic import field_validator

from galaxy import model
from galaxy.managers.audit_actions.base import (
    AuditDetails,
    AuditObject,
    ObjectDescriber,
)

# Matches the clipping the audit service applies to the names it describes itself.
MAX_NAME_LENGTH = 256


_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*")
_AUTHORITY_END = re.compile(r"[/?#]")
_PATH_END = re.compile(r"[?#;]")
_DELIMITERS = frozenset("/?#@:;[]\\%")


def _hostile(char: str) -> bool:
    # Whitespace and control or format characters are dropped by some URL parsers and kept by
    # others, so where the authority ends depends on who reads the string. A backslash is a
    # path separator to some parsers and not to others.
    if char.isspace() or char == "\\" or unicodedata.category(char) in ("Cc", "Cf"):
        return True
    # Lookalikes such as a fullwidth "@" become delimiters for parsers that apply NFKC.
    return not char.isascii() and any(normal in _DELIMITERS for normal in unicodedata.normalize("NFKC", char))


def storage_request_digest(storage_request_id: Any) -> str:
    """A one-way stand-in for a short-term storage request id, the same on both sides of a join.

    The id is the whole credential for ``/api/short_term_storage/{id}``, so an audit
    stream must never carry it. The ids are random UUID4s, so an unkeyed SHA-256 can't
    be inverted or guessed into; unkeyed so that anyone holding an id (from the export
    response or a web server log) can find its events with standard tools, and so the
    value survives a change of Galaxy's id secret.
    """
    return hashlib.sha256(str(storage_request_id).encode()).hexdigest()[:32]


def _valid_host(host: str) -> bool:
    if host.startswith("["):
        _, bracket, port = host.partition("]")
        return bool(bracket) and (port == "" or (port.startswith(":") and port[1:].isdigit()))
    _, colon, port = host.rpartition(":")
    return not colon or port.isdigit()


def sanitize_target_uri(uri: str | None) -> str | None:
    """``scheme://host[:port]/path`` of an export target, without credentials, query or fragment.

    Target URIs are user supplied and some file sources accept credentials in them;
    an audit stream must never carry those. The URI is split by hand, on the original
    string, rather than by ``urlsplit()``: that normalizes as it parses, so its pieces
    don't line up with the text the file source will see. Anything that doesn't split
    cleanly is reduced to its scheme rather than guessed at. Never raises.
    """
    if uri is None:
        return None
    try:
        return _sanitize(uri)
    except Exception:
        return None


def _sanitize(uri: str) -> str | None:
    scheme, colon, rest = uri.partition(":")
    if not colon or not _SCHEME.fullmatch(scheme):
        return None
    scheme_only = f"{scheme.lower()}:"
    if any(_hostile(char) for char in uri):
        # "alice:secret\t@host" would otherwise leave the user name as the "scheme".
        return scheme_only if rest.startswith("/") else None
    if not rest.startswith("//"):
        # e.g. "user:secret@host/path" splits as scheme "user"; keep nothing that could be a secret.
        return None if "@" in rest else scheme_only
    remainder = rest[2:]
    match = _AUTHORITY_END.search(remainder)
    authority, tail = (remainder[: match.start()], remainder[match.start() :]) if match else (remainder, "")
    if "@" in tail:
        # Userinfo holding an unescaped "/", "?" or "#" ends the authority early
        # ("ftp://user:20/24@host"), leaving the secret in what splits as the path.
        return scheme_only
    # Case kept: file source ids in the authority can be case-sensitive.
    host = authority.rpartition("@")[2]
    if not _valid_host(host):
        return scheme_only
    # ";params" can carry session tokens (";jsessionid=...") just like a query string.
    path = _PATH_END.split(tail, maxsplit=1)[0]
    return f"{scheme_only}//{host}{path}"


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
    # Short-term storage request the download will be served from (see storage_request_digest).
    storage_request_digest: str | None = None
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
    # See storage_request_digest; joins the export that prepared the download.
    storage_request_digest: str | None = None


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
