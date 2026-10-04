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
import os
import socket
import sys
import threading
from collections.abc import (
    Callable,
    Iterator,
)
from contextlib import contextmanager
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    get_args,
    Literal,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)
from sqlalchemy import (
    event as sa_event,
    inspect as sa_inspect,
)
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import DetachedInstanceError
from sqlalchemy.orm.util import identity_key

from galaxy import (
    exceptions,
    model,
)
from galaxy.config import GalaxyAppConfiguration
from galaxy.managers.audit_actions import (
    AUDIT_ACTIONS,
    AuditAction,
    AuditDetails,
    AuditObject,
    OBJECT_DESCRIBERS,
)
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
# Actions and their details models live in ``galaxy.managers.audit_actions``, one
# module per family of call sites. Re-exported here for existing imports.

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


def fit_event(event: dict[str, Any], max_bytes: int = MAX_EVENT_BYTES) -> str:
    """Serialize an event, dropping what users chose -- never identifiers -- if it's too big."""
    line = serialize_event(event)
    if len(line) <= max_bytes:
        return line
    event["user_agent"] = None
    event["details"] = {}
    for key, fields in (("actor", _USER_NAMES), ("effective_user", _USER_NAMES), ("object", _OBJECT_NAMES)):
        part = event.get(key) or {}
        for field_name in fields & part.keys():
            part[field_name] = None
    event["truncated"].append("optional_fields")
    line = serialize_event(event)
    if len(line) > max_bytes:
        # Only identifiers and admin-configured values are left; say so rather than cut the JSON.
        event["truncated"].append("over_budget")
        line = serialize_event(event)
    return line


_USER_NAMES = frozenset({"username", "email"})
_OBJECT_NAMES = frozenset({"name", "history_name"})


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
        if self.enabled:
            _count_commits(sa_session)
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
            # Usually called once the change has committed, so only what is loaded is described.
            event = self._prepare(action, obj, details, _BEFORE_ANY_COMMIT)
        except Exception:
            audit_failures.report("prepare", "Failed to build audit event for action %s", action)
            return
        self._emit(event, outcome, reason, stage)

    def prepare(self, action: AuditAction, obj: Any = None) -> "PreparedEvent":
        """Describe ``obj`` and the request's users now, to record once a later commit carries the change.

        Call it before that commit: the description may read through the request's own
        session, which is free of trouble until a commit expires what it holds, and the
        commit's outcome is all that is left to say.
        """
        if not self.wants(action):
            return NULL_PREPARED
        try:
            return PreparedEvent(self, self._prepare(action, obj, None, None))
        except Exception:
            audit_failures.report("prepare", "Failed to prepare audit event for action %s", action)
            return NULL_PREPARED

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
            # Started before the attempt's own work, so reading through the request's session is safe.
            window = self._read_window()
            return AuditAttempt(self, self._prepare(action, requested, details, None), record_success, window)
        except Exception:
            audit_failures.report("prepare", "Failed to start audit attempt for action %s", action)
            return NULL_ATTEMPT

    def _prepare(self, action: str, obj: Any, details: AuditDetails | None, window: "_ReadWindow") -> dict[str, Any]:
        """Capture everything except the outcome.

        Reads go through the request's own session, and only while ``window`` says no
        commit has expired what they would read; otherwise loaded state is all there is.
        """
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
            may_read = self._may_read(self._request_session(), window)
            event["actor"] = self._user(identity.actor_id, truncated, "actor", may_read)
            event["effective_user"] = self._user(identity.user_id, truncated, "effective_user", may_read)
        if obj is not None:
            # Keep the event however this goes: what was touched matters more than how well it is described.
            event["object"] = {"type": _object_type(obj), "id": model_id(obj)}
            self._describe_into(event, obj, window)
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

    def _user(self, user_id: int | None, truncated: list[str], label: str, may_read: bool) -> dict[str, Any] | None:
        if user_id is None:
            return None
        user = {"id": user_id, "encoded_id": self._encode(user_id), "username": None, "email": None}
        if self.include_names:
            try:
                names = self._loaded_names(user_id)
                if names is None:
                    if not may_read:
                        raise _QueryRefused()
                    session = self.sa_session
                    with session.no_autoflush:
                        instance = session.get(model.User, user_id)
                    names = (instance.username, instance.email) if instance is not None else (None, None)
                user["username"], user["email"] = (_clip(name) for name in names)
            except Exception:
                truncated.append(f"{label}.names")
        return user

    def _loaded_names(self, user_id: int) -> tuple[str | None, str | None] | None:
        """The user's names if the request's session already holds them, read without a query.

        The usual case: the request loaded its user. A commit expires them, and then they
        come only from an event prepared before it.
        """
        try:
            instance = self.sa_session.identity_map.get(identity_key(model.User, user_id))
            if instance is None or not _USER_NAMES.isdisjoint(sa_inspect(instance).unloaded):
                return None
            return instance.username, instance.email
        except Exception:
            return None

    def _describe_into(self, event: dict[str, Any], obj: Any, window: "_ReadWindow") -> None:
        """Describe ``obj`` into ``event``, leaving what is already there if that can't be done.

        Describers follow relationships (an item's history, its owner). Before a commit
        those are reads on the request's own session and connection. After one, they
        would refresh what the commit expired: a query on a transaction the request may
        still need, so only loaded state is used. No read ever takes a connection of its own,
        which a request already holding one could wait on for as long as the pool's timeout.
        """
        try:
            target = self._describe_within(obj, window)
            if target is not None:
                event["object"] = target.model_dump()
        except (_QueryRefused, DetachedInstanceError):
            # Only what is loaded could be described; leaving the rest out is not a failure.
            if "object" not in event["truncated"]:
                event["truncated"].append("object")
        except Exception:
            if "object" not in event["truncated"]:
                event["truncated"].append("object")
            audit_failures.report("describe", "Could not describe the object of audit action %s", event["action"])

    def _describe_within(self, obj: Any, window: "_ReadWindow") -> AuditObject | None:
        state = sa_inspect(obj, raiseerr=False) if obj is not None and not isinstance(obj, AuditObject) else None
        session = state.session if state is not None else None
        if session is None:
            return self.describe(obj)
        with session.no_autoflush:
            if self._may_read(session, window):
                return self.describe(obj)
            with _queries_refused(session):
                return self.describe(obj)

    def _request_session(self) -> Session | None:
        try:
            session: Session = self.sa_session()
            return session
        except Exception:
            return None

    def _read_window(self) -> "_ReadWindow":
        session = self._request_session()
        return (session, _commit_count(session)) if session is not None else _BEFORE_ANY_COMMIT

    @staticmethod
    def _may_read(session: Session | None, window: "_ReadWindow") -> bool:
        """Whether reads through ``session`` are still safe for an event begun at ``window``."""
        if window is None:
            return True
        if session is None:
            return False
        opened_on, commits = window
        return _commit_count(session) == (commits if session is opened_on else 0)

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
        for describer in OBJECT_DESCRIBERS:
            described = describer(self, obj)
            if described is not None:
                return described
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

    def __init__(
        self,
        service: AuditService,
        event: dict[str, Any],
        record_success: bool,
        window: "_ReadWindow" = None,
    ) -> None:
        self._service = service
        self._event = event
        self._record_success = record_success
        self._window = window
        self.stage: AuditStage = "authorize"
        self.settled = False
        self.handed_off = False

    @property
    def active(self) -> bool:
        return True

    def authorized(self, obj: Any) -> None:
        """Access was granted to ``obj``; describe it now, while its session is open.

        Reads through the request's session are used only if nothing has committed since
        the attempt started; otherwise ``obj`` is described from what is loaded.
        """
        self._service._describe_into(self._event, obj, self._window)
        self.stage = "prepare"

    def add_details(self, details: AuditDetails | Callable[[], AuditDetails]) -> None:
        """Add facts only known once the attempt is under way (a task id, an issued URL's expiry).

        Fields set here to other than their defaults replace the same fields given at the
        start; a default (None, False) can't clear an earlier value. ``details`` may be a
        factory, called only for an audited action, so request values are parsed only then;
        if it fails, the event loses these details and the request never sees why.
        """
        if self.settled:
            return
        try:
            built = details() if callable(details) else details
            self._event["details"].update(self._service._details(self._event["action"], built))
        except Exception:
            self._event["truncated"].append("details")
            audit_failures.report("details", "Invalid details for audit action %s", self._event["action"])

    def succeeded(
        self,
        details: AuditDetails | Callable[[], AuditDetails] | None = None,
        stage: AuditStage | None = None,
    ) -> None:
        if not self._record_success:
            self.settled = True
            return
        if details is not None:
            self.add_details(details)
        self._settle("success", None, stage)

    def denied(self, stage: AuditStage | None = None) -> None:
        """Access was refused without an exception to classify."""
        self._settle("denied", "not_accessible", stage)

    def withdraw(self) -> None:
        """End the attempt without an event: another request or record says what happened."""
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

        If the request ends without the response being called, the attempt is settled
        as an error when the request scope closes. Outside a request scope nothing does
        that, so callers without one must settle the attempt themselves.
        """
        self.handed_off = True
        self.stage = "respond"
        scope = current_request_scope()
        if scope is not None:
            scope.on_close.append(self._abandoned)

    def _abandoned(self) -> None:
        try:
            self.failed("response_not_started", "respond")
        except Exception:
            audit_failures.report(
                "abandoned", "Could not settle an abandoned audit attempt for %s", self._event["action"]
            )

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

    def add_details(self, details: AuditDetails | Callable[[], AuditDetails]) -> None:
        pass

    def hand_off(self) -> None:
        pass

    def _settle(self, outcome: AuditOutcome, reason: AuditReason | None, stage: AuditStage | None = None) -> None:
        pass


NULL_ATTEMPT = _NullAttempt()


class PreparedEvent:
    """An event described before a commit, recorded at most once after it."""

    def __init__(self, service: AuditService, event: dict[str, Any]) -> None:
        self._service = service
        self._event = event
        self.recorded = False

    def record(
        self,
        outcome: AuditOutcome = "success",
        *,
        details: AuditDetails | Callable[[], AuditDetails] | None = None,
        reason: AuditReason | None = None,
        stage: AuditStage | None = None,
    ) -> None:
        if self.recorded:
            return
        self.recorded = True
        if details is not None:
            try:
                built = details() if callable(details) else details
                self._event["details"] = self._service._details(self._event["action"], built)
            except Exception:
                self._event["truncated"].append("details")
                audit_failures.report("details", "Invalid details for audit action %s", self._event["action"])
        self._service._emit(self._event, outcome, reason, stage)


class _NullPrepared(PreparedEvent):
    def __init__(self) -> None:
        self.recorded = True

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass


NULL_PREPARED = _NullPrepared()


# -- Call-site helpers ------------------------------------------------------------


def audit_service_for(app: Any) -> AuditService:
    """The application's audit service.

    Galaxy registers it at startup. A container that never did (scripts building a
    partial app) gets one built from its own config, rather than letting the container
    construct a whole new Galaxy configuration to satisfy it.
    """
    if AuditService in getattr(app, "defined_types", (AuditService,)):
        service: AuditService = app[AuditService]
        return service
    return AuditService(app.config, app.security, app.model.context)


def _build_for_audit(factory: Callable[[], Any] | None, action: str) -> Any:
    # Request values go into these models, and one that doesn't fit (a repeated query
    # parameter arrives as a list) must cost the event its details, never the request its answer.
    if factory is None:
        return None
    try:
        return factory()
    except Exception:
        audit_failures.report("details", "Could not build audit details for action %s", action)
        return None


def begin_audit_attempt(
    audit: AuditService,
    action: AuditAction,
    requested_id: int | None = None,
    details: Callable[[], AuditDetails] | None = None,
    requested_type: str | None = "hda",
    record_success: bool = True,
) -> AuditAttempt:
    """Start an attempt, building what it records only when the action is audited."""
    if not audit.wants(action):
        return NULL_ATTEMPT
    requested = None
    if requested_type is not None:
        requested = _build_for_audit(lambda: AuditObject(type=requested_type, id=requested_id), action)
    return audit.attempt(action, requested, _build_for_audit(details, action), record_success=record_success)


def record_audit_event(
    audit: AuditService,
    action: AuditAction,
    obj: Any,
    outcome: AuditOutcome,
    details: Callable[[], AuditDetails] | None = None,
    reason: AuditReason | None = None,
    stage: AuditStage | None = None,
) -> None:
    """Record one event, building its details only when the action is audited."""
    if not audit.wants(action):
        return
    audit.record(action, obj, outcome, details=_build_for_audit(details, action), reason=reason, stage=stage)


def audit_id(obj: Any) -> int | None:
    """The primary key of a persistent ``obj``, read from its identity without a query.

    A commit expires every attribute, ``id`` included, so reading ``obj.id`` afterwards
    refreshes the object through the request's session.
    """
    state = sa_inspect(obj, raiseerr=False)
    identity = state.identity if state is not None else None
    return identity[0] if identity else None


# Outer commits of a session, counted so an audit read can tell whether one has expired
# what it would read. Kept on the session: Galaxy opens one per web request, but a
# thread's session in background work lives on, so there the count only grows and
# events describe only what is loaded.
_COMMITS = "galaxy.audit.commits"

# Opened on a session, and the number of its commits then; None: reads are safe now.
_ReadWindow = tuple[Session | None, int] | None
# For events recorded at any point: reads are safe only if the session has never committed.
_BEFORE_ANY_COMMIT: _ReadWindow = (None, 0)


def _on_commit(session: Session) -> None:
    # A released savepoint fires this too, but expires nothing.
    if not session.in_nested_transaction():
        session.info[_COMMITS] = session.info.get(_COMMITS, 0) + 1


def _commit_count(session: Session) -> int:
    try:
        return int(session.info.get(_COMMITS, 0))
    except Exception:
        return 0


def _count_commits(sessions: Any) -> None:
    """Count the commits of every session ``sessions`` (a scoped session or factory) makes."""
    try:
        if not sa_event.contains(sessions, "after_commit", _on_commit):
            sa_event.listen(sessions, "after_commit", _on_commit)
    except Exception:
        # Not a session factory (a stand-in in scripts and tests): nothing is counted.
        log.debug("Audit cannot follow commits of %r", sessions)


class _QueryRefused(Exception):
    pass


@contextmanager
def _queries_refused(session: Session) -> Iterator[None]:
    # Raised before anything reaches the database, so the session's transaction is untouched.
    # A listener of its own per call, so a nested use can't remove the outer one.
    def refuse(orm_execute_state: Any) -> None:
        raise _QueryRefused()

    sa_event.listen(session, "do_orm_execute", refuse)
    try:
        yield
    finally:
        sa_event.remove(session, "do_orm_execute", refuse)


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
