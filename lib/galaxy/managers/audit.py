"""Structured audit events: who did what, when, and from where.

Events go to the ``galaxy.audit`` logger as one JSON object per line, so routing,
retention and reporting stay with the deployment's log infrastructure rather than
Galaxy's database. Identity and request fields come from the request scope
(:mod:`galaxy.web.framework.request_scope`); call sites only say what happened::

    self.audit.record("dataset.download", hda, "success", details=DatasetContentDetails(to_ext="bam"))

Content access is usually recorded through :meth:`AuditService.attempt`, which
turns whatever happens -- a denial, a storage failure, a response that never
starts -- into exactly one event.

This module deliberately lives outside a ``galaxy.audit`` package: module loggers
under that name would be children of the audit logger and leak Galaxy's own
diagnostics into the audit stream.
"""

import asyncio
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    ClassVar,
    get_args,
    Literal,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from galaxy import (
    exceptions,
    model,
)
from galaxy.config import GalaxyAppConfiguration
from galaxy.model.scoped_session import galaxy_scoped_session
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.web.framework.request_scope import (
    AuthMethod,
    current_request_scope,
    IdentitySwitch,
    model_id,
)

log = logging.getLogger(__name__)

AUDIT_LOGGER_NAME = "galaxy.audit"
audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)

AUDIT_SCHEMA_VERSION: Literal[1] = 1
# Well under common syslog message limits (8 KiB in rsyslog), so a collector never
# cuts an event into invalid JSON.
MAX_EVENT_BYTES = 4096
# Names and detail strings are user-controlled; ensure_ascii can grow a character to
# 12 bytes, so the size budget below is what actually bounds an event.
MAX_STRING_LENGTH = 256
FAILURE_METRIC = "galaxy.audit.failures"

AuditOutcome = Literal["success", "denied", "error"]
# Where an attempt ended: checking access, preparing content, or starting the response.
AuditStage = Literal["authorize", "prepare", "respond"]
AuditReason = Literal[
    "not_accessible",
    "not_found",
    "invalid_request",
    "invalid_range",
    "archive_failed",
    "error_response",
    "response_not_started",
    "internal_error",
]


# -- Action registry --------------------------------------------------------------
#
# Every action an event can name, with the one details type allowed for it. New
# call sites add an entry here (and to AuditAction) rather than inventing names.


class AuditDetails(BaseModel):
    """Action-specific facts. Subclasses list fields explicitly; nothing else is serialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Fields that can carry user-chosen names; dropped unless include_names is on.
    identifying_fields: ClassVar[frozenset[str]] = frozenset()


class DatasetContentDetails(AuditDetails):
    identifying_fields: ClassVar[frozenset[str]] = frozenset({"filename"})

    source: Literal["hda", "ldda"] = "hda"
    preview: bool = False
    raw: bool = False
    # Strings are clipped when the event is written, so a long request value never fails validation.
    to_ext: str | None = None
    # A file inside the dataset's extra files directory.
    filename: str | None = None
    offset: int | None = None
    ck_size: int | None = None
    # The HTTP Range header, for partial reads (IGV and friends make one request per range).
    http_range: str | None = None


AuditAction = Literal["dataset.display", "dataset.download"]

AUDIT_ACTIONS: dict[str, type[AuditDetails]] = {
    "dataset.display": DatasetContentDetails,
    "dataset.download": DatasetContentDetails,
}


# -- Event schema -----------------------------------------------------------------


class AuditUser(BaseModel):
    id: int | None = None
    encoded_id: str | None = None
    # Only with include_names.
    username: str | None = None
    email: str | None = None


class AuditAuth(BaseModel):
    method: AuthMethod | None = None
    # galaxy_session.id or the api_keys row id; never the credential itself.
    credential_id: int | None = None
    # How actor and effective_user differ, when they do.
    switch: IdentitySwitch | None = None


class AuditObject(BaseModel):
    """What was acted on, described well enough to stand alone.

    Log consumers cannot join back to Galaxy's database, so each event carries the
    identifiers someone investigating would search for.
    """

    type: str
    id: int | None = None
    encoded_id: str | None = None
    uuid: str | None = None
    dataset_id: int | None = None
    history_id: int | None = None
    owner_id: int | None = None
    # Only with include_names.
    name: str | None = None
    history_name: str | None = None


class AuditProcess(BaseModel):
    name: str | None = None
    host: str | None = None
    pid: int | None = None


class AuditEvent(BaseModel):
    """Version 1 of the audit event schema.

    Field order is the serialized key order and every key is always present. Adding
    a field is a compatible change; renaming or removing one needs a new ``schema``.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal[1] = Field(default=AUDIT_SCHEMA_VERSION, alias="schema")
    time: str
    instance: str | None = None
    action: str
    outcome: AuditOutcome
    reason: AuditReason | None = None
    stage: AuditStage | None = None
    actor: AuditUser | None = None
    effective_user: AuditUser | None = None
    auth: AuditAuth
    request_id: str | None = None
    remote_addr: str | None = None
    user_agent: str | None = None
    process: AuditProcess
    target: AuditObject | None = Field(default=None, alias="object")
    details: dict[str, Any] = Field(default_factory=dict)
    # Parts left out to keep the event within MAX_EVENT_BYTES, or that could not be built.
    truncated: list[str] = Field(default_factory=list)


def serialize_event(event: dict[str, Any]) -> str:
    # ensure_ascii escapes every non-ASCII character, including U+2028, U+0085 and the
    # other code points that some log shippers (and str.splitlines) treat as line
    # breaks, so user-controlled strings can never start a forged line.
    return json.dumps(event, ensure_ascii=True, separators=(",", ":"), default=str)


# Dropped in this order until an event fits; identifiers are never dropped.
_SHEDDABLE: tuple[tuple[str, ...], ...] = (
    ("user_agent",),
    ("object", "history_name"),
    ("object", "name"),
    ("details",),
    ("actor", "username"),
    ("actor", "email"),
    ("effective_user", "username"),
    ("effective_user", "email"),
)


def fit_event(event: dict[str, Any], max_bytes: int = MAX_EVENT_BYTES) -> str:
    line = serialize_event(event)
    for path in _SHEDDABLE:
        if len(line) <= max_bytes:
            break
        parent = event
        for key in path[:-1]:
            parent = parent.get(key) or {}
        if parent.get(path[-1]) in (None, {}):
            continue
        parent[path[-1]] = {} if path[-1] == "details" else None
        event["truncated"].append(".".join(path))
        line = serialize_event(event)
    if len(line) > max_bytes:
        # Only identifiers are left; say so rather than pretend the budget held.
        event["truncated"].append("over_budget")
        line = serialize_event(event)
    return line


def classify_failure(exc: BaseException) -> tuple[AuditOutcome, AuditReason]:
    if isinstance(
        exc,
        (
            exceptions.ItemAccessibilityException,
            exceptions.ItemOwnershipException,
            exceptions.InsufficientPermissionsException,
            exceptions.AuthenticationRequired,
            exceptions.AuthenticationFailed,
        ),
    ):
        return "denied", "not_accessible"
    if isinstance(exc, exceptions.ObjectNotFound):
        return "error", "not_found"
    if isinstance(exc, asyncio.CancelledError):
        # The client went away (or the server gave up) before the response started.
        return "error", "response_not_started"
    status_code = getattr(exc, "status_code", None)
    if status_code == 416:
        return "error", "invalid_range"
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return "error", "invalid_request"
    return "error", "internal_error"


# -- Failure reporting ------------------------------------------------------------


class _FailureCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0
        self.statsd_client: Any = None

    def report(self, kind: str, message: str, *args: Any) -> None:
        """Count a lost or degraded event and say so. Never raises."""
        try:
            with self._lock:
                self.count += 1
            if self.statsd_client is not None:
                self.statsd_client.incr(FAILURE_METRIC, tags={"kind": kind})
            # Galaxy's application log, where error alerting already looks.
            log.error(message, *args)
        except Exception:
            try:
                sys.stderr.write(f"galaxy.audit: could not report an audit failure ({kind})\n")
            except Exception:
                pass


audit_failures = _FailureCounter()


class ReportsAuditFailures:
    """Mix into a logging handler so a failed audit write is counted and reported.

    logging's default ``handleError`` prints a traceback to stderr and carries on,
    so a full disk or a dead syslog socket would lose audit events silently.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        if record.name == AUDIT_LOGGER_NAME:
            audit_failures.report("handler", "Audit log handler %r failed; an audit event was lost", self)
        else:
            super().handleError(record)  # type: ignore[misc]


class AuditSysLogHandler(ReportsAuditFailures, logging.handlers.SysLogHandler):
    pass


class AuditWatchedFileHandler(ReportsAuditFailures, logging.handlers.WatchedFileHandler):
    pass


def audit_logger_routed() -> bool:
    """Whether an INFO record on galaxy.audit reaches at least one handler that accepts it."""
    if not audit_logger.isEnabledFor(logging.INFO):
        return False
    current: logging.Logger | None = audit_logger
    while current is not None:
        if any(handler.level <= logging.INFO for handler in current.handlers):
            return True
        if not current.propagate:
            break
        current = current.parent
    return False


# -- Service ----------------------------------------------------------------------


class AuditService:
    def __init__(
        self,
        config: GalaxyAppConfiguration,
        security: IdEncodingHelper,
        sa_session: galaxy_scoped_session,
        statsd_client: Any = None,
    ):
        settings = getattr(config, "audit_log", None) or {}
        self.enabled = bool(settings.get("enabled", False))
        self.include_names = bool(settings.get("include_names", False))
        self._families = frozenset(settings.get("actions") or ()) or None
        self.security = security
        self.sa_session = sa_session
        self._instance = getattr(config, "galaxy_infrastructure_url", None)
        self._process = {"name": getattr(config, "server_name", None), "host": socket.gethostname(), "pid": os.getpid()}
        self._warned_unrouted = False
        if statsd_client is not None:
            audit_failures.statsd_client = statsd_client
        if self.enabled and not audit_logger_routed():
            self._warned_unrouted = True
            log.warning(
                "audit_log is enabled but the %s logger has no handler accepting INFO; audit events will be dropped",
                AUDIT_LOGGER_NAME,
            )

    def wants(self, action: str) -> bool:
        if not self.enabled:
            return False
        return self._families is None or action.split(".", 1)[0] in self._families

    def record(
        self,
        action: AuditAction,
        obj: Any = None,
        outcome: AuditOutcome = "success",
        *,
        details: AuditDetails | None = None,
        reason: AuditReason | None = None,
        stage: AuditStage | None = None,
    ) -> None:
        """Emit one audit event. Never raises: a broken audit pipeline is reported, not propagated."""
        if not self.wants(action):
            return
        try:
            event = self._prepare(action, obj, details)
        except Exception:
            audit_failures.report("prepare", "Failed to build audit event for action %s", action)
            return
        self._emit(event, outcome, reason, stage)

    def attempt(
        self,
        action: AuditAction,
        requested: "AuditObject | None" = None,
        details: AuditDetails | None = None,
        record_success: bool = True,
    ) -> "AuditAttempt":
        """Start recording an attempt that ends in exactly one event.

        ``requested`` describes what the caller asked for before access is checked,
        so a denial still names it without another query. ``record_success`` False
        (HEAD requests) still records denials and errors, but not success.
        """
        if not self.wants(action):
            return NULL_ATTEMPT
        try:
            if requested is not None and requested.encoded_id is None and requested.id is not None:
                requested = requested.model_copy(update={"encoded_id": self._encode(requested.id)})
            return AuditAttempt(self, self._prepare(action, requested, details), record_success)
        except Exception:
            audit_failures.report("prepare", "Failed to start audit attempt for action %s", action)
            return NULL_ATTEMPT

    def _prepare(self, action: str, obj: Any, details: AuditDetails | None) -> dict[str, Any]:
        """Capture everything except the outcome, while the request's database session is still open."""
        truncated: list[str] = []
        scope = current_request_scope()
        identity = scope.identity if scope is not None else None
        event: dict[str, Any] = {
            "schema": AUDIT_SCHEMA_VERSION,
            "time": None,
            "instance": self._instance,
            "action": action,
            "outcome": None,
            "reason": None,
            "stage": None,
            "actor": None,
            "effective_user": None,
            "auth": {"method": None, "credential_id": None, "switch": None},
            "request_id": scope.request_id if scope else None,
            "remote_addr": _clip(scope.remote_addr) if scope else None,
            "user_agent": scope.user_agent if scope else None,
            "process": dict(self._process),
            "object": None,
            "details": {},
            "truncated": truncated,
        }
        if identity is not None:
            event["auth"] = {
                "method": identity.auth_method,
                "credential_id": identity.credential_id,
                "switch": identity.switch,
            }
            event["actor"] = self._user(identity.actor_id, truncated, "actor")
            event["effective_user"] = self._user(identity.user_id, truncated, "effective_user")
        try:
            target = self.describe(obj)
            event["object"] = target.model_dump() if target is not None else None
        except Exception:
            # Keep the event: what was touched matters more than how well it is described.
            event["object"] = {"type": _object_type(obj), "id": model_id(obj)}
            truncated.append("object")
            audit_failures.report("describe", "Could not describe the object of audit action %s", action)
        if details is not None:
            try:
                event["details"] = self._details(action, details)
            except Exception:
                truncated.append("details")
                audit_failures.report("details", "Invalid details for audit action %s", action)
        return event

    def _emit(
        self, event: dict[str, Any], outcome: AuditOutcome, reason: AuditReason | None, stage: AuditStage | None
    ) -> None:
        try:
            event["time"] = _utc_now()
            event["outcome"] = outcome
            event["reason"] = reason
            event["stage"] = stage
            line = fit_event(event)
            if not audit_logger_routed():
                if not self._warned_unrouted:
                    self._warned_unrouted = True
                    audit_failures.report(
                        "unrouted", "audit_log is enabled but no handler accepts %s at INFO", AUDIT_LOGGER_NAME
                    )
                else:
                    audit_failures.report("unrouted", "Dropped audit event %s: logger not routed", event["action"])
                return
            audit_logger.info("%s", line)
        except Exception:
            audit_failures.report("emit", "Failed to write audit event for action %s", event.get("action"))

    def _details(self, action: str, details: AuditDetails) -> dict[str, Any]:
        expected = AUDIT_ACTIONS[action]
        if type(details) is not expected:
            raise TypeError(f"{action} takes {expected.__name__}, not {type(details).__name__}")
        exclude = set() if self.include_names else set(details.identifying_fields)
        values = details.model_dump(exclude=exclude, exclude_defaults=True)
        return {key: _clip(value) for key, value in values.items()}

    def _user(self, user_id: int | None, truncated: list[str], label: str) -> dict[str, Any] | None:
        if user_id is None:
            return None
        user = {"id": user_id, "encoded_id": self._encode(user_id), "username": None, "email": None}
        if self.include_names:
            try:
                # An identity-map hit on the request's session in the usual case.
                instance = self.sa_session.get(model.User, user_id)
                if instance is not None:
                    user["username"] = _clip(instance.username)
                    user["email"] = _clip(instance.email)
            except Exception:
                truncated.append(f"{label}.names")
        return user

    def describe(self, obj: Any) -> AuditObject | None:
        if obj is None or isinstance(obj, AuditObject):
            return obj
        if isinstance(obj, model.HistoryDatasetAssociation):
            history = obj.history
            return AuditObject(
                type="hda",
                id=obj.id,
                encoded_id=self._encode(obj.id),
                uuid=_dataset_uuid(obj),
                dataset_id=obj.dataset_id,
                history_id=obj.history_id,
                owner_id=history.user_id if history is not None else None,
                name=_clip(obj.name) if self.include_names else None,
                history_name=_clip(history.name) if self.include_names and history is not None else None,
            )
        if isinstance(obj, model.LibraryDatasetDatasetAssociation):
            return AuditObject(
                type="ldda",
                id=obj.id,
                encoded_id=self._encode(obj.id),
                uuid=_dataset_uuid(obj),
                dataset_id=obj.dataset_id,
                # Mapped imperatively, so the type checker can't see the column.
                owner_id=getattr(obj, "user_id", None),
                name=_clip(obj.name) if self.include_names else None,
            )
        object_id = model_id(obj)
        return AuditObject(type=_object_type(obj), id=object_id, encoded_id=self._encode(object_id))

    def _encode(self, object_id: int | None) -> str | None:
        if object_id is None:
            return None
        return self.security.encode_id(object_id)


class AuditAttempt:
    """One attempt at an audited action, ending in exactly one event.

    The attempt starts at the ``authorize`` stage. The call site moves it on with
    :meth:`authorized` once access is granted, and either settles it directly or
    hands it to the HTTP response (see ``galaxy.webapps.base.audit``), which settles
    it when the response starts.
    """

    def __init__(self, service: AuditService, event: dict[str, Any], record_success: bool) -> None:
        self._service = service
        self._event = event
        self._record_success = record_success
        self.stage: AuditStage = "authorize"
        self.settled = False
        self.handed_off = False

    @property
    def active(self) -> bool:
        return True

    def authorized(self, obj: Any) -> None:
        """Access was granted to ``obj``; describe it now, while its session is open."""
        try:
            target = self._service.describe(obj)
            if target is not None:
                self._event["object"] = target.model_dump()
        except Exception:
            self._event["truncated"].append("object")
            audit_failures.report("describe", "Could not describe the object of audit action %s", self._event["action"])
        self.stage = "prepare"

    def succeeded(self) -> None:
        if self._record_success:
            self._settle("success", None)
        else:
            self.settled = True

    def failed(self, reason: AuditReason, stage: AuditStage | None = None) -> None:
        self._settle("error", reason, stage)

    def failed_with(self, exc: BaseException, stage: AuditStage | None = None) -> None:
        outcome, reason = classify_failure(exc)
        self._settle(outcome, reason, stage)

    def response_started(self, status: int) -> None:
        if status < 400:
            self.succeeded()
        else:
            self.failed("error_response", "respond")

    def hand_off(self) -> None:
        """The response will settle this attempt when it starts.

        If the request ends first -- the client disconnects, or the server never calls
        the response -- the attempt is settled as an error when the request scope closes.
        """
        self.handed_off = True
        self.stage = "respond"
        scope = current_request_scope()
        if scope is not None:
            scope.on_close.append(self._abandoned)

    def _abandoned(self) -> None:
        self.failed("response_not_started", "respond")

    def _settle(self, outcome: AuditOutcome, reason: AuditReason | None, stage: AuditStage | None = None) -> None:
        if self.settled:
            return
        self.settled = True
        self._service._emit(self._event, outcome, reason, stage or self.stage)

    @contextmanager
    def guard(self) -> Iterator["AuditAttempt"]:
        """Settle the attempt from whatever escapes the block, unless something already did."""
        try:
            yield self
        except BaseException as exc:
            if not self.settled:
                self.failed_with(exc)
            raise
        if not self.settled and not self.handed_off:
            # The call site returned without saying how the attempt ended.
            self.failed("internal_error")


class _NullAttempt(AuditAttempt):
    """What attempt() returns when the action isn't audited: every method does nothing."""

    def __init__(self) -> None:
        self._record_success = False
        self.stage = "authorize"
        self.settled = True
        self.handed_off = False

    @property
    def active(self) -> bool:
        return False

    def authorized(self, obj: Any) -> None:
        pass

    def hand_off(self) -> None:
        pass

    def _settle(self, outcome: AuditOutcome, reason: AuditReason | None, stage: AuditStage | None = None) -> None:
        pass


NULL_ATTEMPT = _NullAttempt()


def _object_type(obj: Any) -> str:
    if isinstance(obj, model.HistoryDatasetAssociation):
        return "hda"
    if isinstance(obj, model.LibraryDatasetDatasetAssociation):
        return "ldda"
    return type(obj).__name__.lower()


def _dataset_uuid(dataset_instance: Any) -> str | None:
    dataset = dataset_instance.dataset
    if dataset is None or dataset.uuid is None:
        return None
    return str(dataset.uuid)


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
        return value[:MAX_STRING_LENGTH]
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def registered_actions() -> tuple[str, ...]:
    return get_args(AuditAction)
