import contextlib
import json
import logging
import re
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from galaxy import (
    exceptions,
    model,
)
from galaxy.app_unittest_utils.galaxy_mock import MockApp
from galaxy.managers import audit
from galaxy.managers.audit import (
    audit_failures,
    AuditEvent,
    AuditService,
    MAX_EVENT_BYTES,
    NULL_ATTEMPT,
    registered_actions,
)
from galaxy.managers.audit_actions import (
    AUDIT_ACTIONS,
    AuditDetails,
    AuditObject,
    DatasetContentDetails,
)
from galaxy.managers.audit_actions.datasets import DownloadUrlDetails
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.web.framework.request_scope import (
    request_scope,
    RequestIdentity,
    set_request_identity,
)

SECURITY = IdEncodingHelper(id_secret="audit-test-secret")
SECRETS = ("alice@example.org", "admin@example.org", "reads.fastq", "My history", "alice", "admin")


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


class BrokenHandler(logging.Handler):
    def emit(self, record):
        try:
            raise OSError("disk full")
        except OSError:
            self.handleError(record)


@pytest.fixture
def audit_handler():
    """Route galaxy.audit to a capturing handler only, the way a deployment isolates it."""
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = (logger.handlers[:], logger.level, logger.propagate)
    handler = CapturingHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield handler
    finally:
        logger.handlers, logger.level, logger.propagate = saved


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(audit, "_utc_now", lambda: "2026-10-02T12:00:00.000Z")
    monkeypatch.setattr(audit_failures, "statsd_client", None)


ADMIN = SimpleNamespace(username="admin", email="admin@example.org")
ALICE = SimpleNamespace(username="alice", email="alice@example.org")


class FakeSession:
    """Stands in for both the request's session and its engine; reads go through ``audit_read_session``."""

    def __init__(self, users=None):
        self.users = users if users is not None else {1: ADMIN, 7: ALICE}

    def get_bind(self):
        return self

    def get(self, model_class, user_id):
        assert model_class is model.User
        return self.users.get(user_id)


@pytest.fixture(autouse=True)
def read_sessions_from_the_fake(monkeypatch):
    # The names lookup opens a session on the engine; the fake engine is its own session.
    monkeypatch.setattr(audit, "audit_read_session", contextlib.nullcontext)


def make_service(enabled=True, actions=None, include_names=False, session=None, statsd_client=None) -> AuditService:
    settings: dict[str, Any] = {"enabled": enabled, "include_names": include_names}
    if actions is not None:
        settings["actions"] = actions
    config = SimpleNamespace(
        audit_log=settings, server_name="main.web.1", galaxy_infrastructure_url="https://galaxy.example.org"
    )
    service = AuditService(config, SECURITY, session or FakeSession(), statsd_client)  # type: ignore[arg-type]
    service._process = {"name": "main.web.1", "host": "galaxy-host", "pid": 4242}
    return service


def make_hda(name="reads.fastq") -> model.HistoryDatasetAssociation:
    history = model.History(name="My history")
    history.id = 3
    history.user_id = 7
    dataset = model.Dataset()
    dataset.id = 11
    dataset.uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    hda = model.HistoryDatasetAssociation(
        history=history, dataset=dataset, name=name, create_dataset=False, sa_session=None
    )
    hda.id = 42
    hda.dataset_id = 11
    hda.history_id = 3
    return hda


IMPERSONATION = RequestIdentity("session", 7, actor_id=1, switch="impersonation", credential_id=99)


def in_request(identity=IMPERSONATION, user_agent="curl/8.0"):
    scope = request_scope(request_id="req-1", remote_addr="192.0.2.10", user_agent=user_agent)

    class _Scope:
        def __enter__(self):
            scope.__enter__()
            if identity is not None:
                set_request_identity(identity)

        def __exit__(self, *exc):
            scope.__exit__(*exc)

    return _Scope()


GOLDEN_DOWNLOAD_EVENT = (
    '{"schema":1,"time":"2026-10-02T12:00:00.000Z","instance":"https://galaxy.example.org",'
    '"action":"dataset.download","outcome":"success","reason":null,"stage":null,'
    '"actor":{"id":1,"encoded_id":"ENC_1","username":null,"email":null},'
    '"effective_user":{"id":7,"encoded_id":"ENC_7","username":null,"email":null},'
    '"auth":{"method":"session","credential_id":99,"switch":"impersonation"},'
    '"request_id":"req-1","remote_addr":"192.0.2.10","user_agent":"curl/8.0",'
    '"process":{"name":"main.web.1","host":"galaxy-host","pid":4242},'
    '"object":{"type":"hda","id":42,"encoded_id":"ENC_42","uuid":"12345678-1234-5678-1234-567812345678",'
    '"dataset_id":11,"history_id":3,"owner_id":7,"name":null,"history_name":null},'
    '"details":{"to_ext":"fastqsanger"},"truncated":[]}'
)
for _id in (1, 7, 42):
    GOLDEN_DOWNLOAD_EVENT = GOLDEN_DOWNLOAD_EVENT.replace(f"ENC_{_id}", SECURITY.encode_id(_id))


def test_golden_event(audit_handler):
    with in_request():
        make_service().record("dataset.download", make_hda(), details=DatasetContentDetails(to_ext="fastqsanger"))
    assert audit_handler.lines == [GOLDEN_DOWNLOAD_EVENT]


def test_event_keys_match_the_documented_schema(audit_handler):
    with in_request():
        make_service().record("dataset.display", make_hda(), details=DatasetContentDetails())
    event = json.loads(audit_handler.lines[0])
    schema = AuditEvent.model_json_schema(by_alias=True)
    assert list(event) == list(schema["properties"])
    AuditEvent.model_validate(event)


def test_default_events_carry_ids_but_no_names(audit_handler):
    with in_request():
        make_service().record(
            "dataset.display", make_hda(), details=DatasetContentDetails(filename="patient-1234/index.html")
        )
    (line,) = audit_handler.lines
    for secret in SECRETS + ("patient-1234",):
        assert secret not in line
    event = json.loads(line)
    assert event["object"]["uuid"] == "12345678-1234-5678-1234-567812345678"
    assert event["details"] == {}


def test_include_names_adds_identifying_fields(audit_handler):
    with in_request():
        make_service(include_names=True).record(
            "dataset.display", make_hda(), details=DatasetContentDetails(filename="index.html")
        )
    event = json.loads(audit_handler.lines[0])
    assert event["actor"]["email"] == "admin@example.org"
    assert event["effective_user"]["username"] == "alice"
    assert (event["object"]["name"], event["object"]["history_name"]) == ("reads.fastq", "My history")
    assert event["details"] == {"filename": "index.html"}


def test_names_that_fail_to_load_leave_the_ids(audit_handler):
    class BrokenSession:
        def get_bind(self):
            return self

        def get(self, model_class, user_id):
            raise RuntimeError("session closed")

    with in_request():
        make_service(include_names=True, session=BrokenSession()).record("dataset.display", make_hda())
    event = json.loads(audit_handler.lines[0])
    assert event["actor"]["id"] == 1 and event["actor"]["email"] is None
    assert event["truncated"] == ["actor.names", "effective_user.names"]


def test_disabled_is_a_no_op(audit_handler):
    class Explodes:
        def __getattr__(self, name):
            raise AssertionError(f"read {name}")

    service = make_service(enabled=False)
    service.record("dataset.display", Explodes(), details=DatasetContentDetails())
    assert service.attempt("dataset.display", Explodes()) is NULL_ATTEMPT  # type: ignore[arg-type]
    assert audit_handler.lines == []
    service = AuditService(SimpleNamespace(), SECURITY, FakeSession())  # type: ignore[arg-type]
    assert not service.enabled


def test_action_families_filter(audit_handler):
    service = make_service(actions=["history"])
    service.record("dataset.display", make_hda())
    assert audit_handler.lines == []
    assert make_service(actions=["dataset"]).wants("dataset.display")


def test_registry_lists_every_action_with_its_details_type():
    assert set(registered_actions()) == set(AUDIT_ACTIONS)
    for action in AUDIT_ACTIONS:
        assert re.fullmatch(r"[a-z_]+\.[a-z_]+", action)


def test_details_of_the_wrong_type_are_dropped_not_the_event(audit_handler, caplog):
    class OtherDetails(AuditDetails):
        note: str = "x"

    before = audit_failures.count
    with in_request():
        make_service().record("dataset.display", make_hda(), details=OtherDetails())
    event = json.loads(audit_handler.lines[0])
    assert event["details"] == {} and event["truncated"] == ["details"]
    assert audit_failures.count == before + 1


def test_details_reject_unknown_fields():
    with pytest.raises(ValueError):
        DatasetContentDetails(path="/etc/passwd")  # type: ignore[call-arg]


def test_failed_description_keeps_a_minimal_event(audit_handler, monkeypatch):
    def detached(dataset_instance):
        raise RuntimeError("detached")

    monkeypatch.setattr(audit, "_dataset_uuid", detached)
    with in_request():
        make_service().record("dataset.display", make_hda())
    event = json.loads(audit_handler.lines[0])
    # A transient instance has no identity key; a persistent one would keep its id.
    assert event["object"] == {"type": "hda", "id": None}
    assert event["truncated"] == ["object"]
    assert event["actor"]["id"] == 1


def test_event_size_is_bounded_and_marked(audit_handler):
    # Astral characters escape to 12 bytes each, so clipping characters alone isn't enough.
    huge = "\U0001f600" * 5000
    with in_request(user_agent="ua" * 1000):
        make_service(include_names=True, session=FakeSession({1: SimpleNamespace(username=huge, email=huge)})).record(
            "dataset.display", make_hda(name=huge), details=DatasetContentDetails(filename=huge)
        )
    (line,) = audit_handler.lines
    assert len(line) <= MAX_EVENT_BYTES
    event = json.loads(line)
    assert event["object"]["id"] == 42 and event["object"]["uuid"]
    assert event["actor"]["id"] == 1 and event["effective_user"]["id"] == 7
    assert event["request_id"] == "req-1"
    assert event["truncated"] == ["optional_fields"]
    assert event["user_agent"] is None and event["object"]["name"] is None and event["details"] == {}


@pytest.mark.parametrize(
    "hostile",
    ["a\nb", "a\rb", "a b", "a b", "a\u0085b", "a\x1eb", "a\x00b", 'a"}{"x":"b', "%s%d"],
)
def test_user_controlled_strings_cannot_forge_lines(audit_handler, hostile):
    with in_request(user_agent=hostile):
        make_service(include_names=True).record(
            "dataset.display", make_hda(name=hostile), details=DatasetContentDetails(filename=hostile)
        )
    (line,) = audit_handler.lines
    assert line.isascii() and len(line.splitlines()) == 1
    event = json.loads(line)
    assert event["object"]["name"] == hostile and event["user_agent"] == hostile


def test_events_outside_a_request_have_no_identity(audit_handler):
    make_service().record("dataset.display", make_hda())
    event = json.loads(audit_handler.lines[0])
    assert event["actor"] is None and event["auth"] == {"method": None, "credential_id": None, "switch": None}


def test_unrouted_audit_logger_warns_counts_and_drops(caplog):
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = (logger.handlers[:], logger.level, logger.propagate)
    logger.handlers, logger.propagate = [], False
    try:
        with caplog.at_level(logging.WARNING, logger="galaxy.managers.audit"):
            service = make_service()
            before = audit_failures.count
            service.record("dataset.display", make_hda())
            service.record("dataset.display", make_hda())
        assert audit_failures.count == before + 2
        startup = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(startup) == 1 and "no handler accepting INFO" in startup[0].getMessage()
    finally:
        logger.handlers, logger.level, logger.propagate = saved


def test_handler_at_warning_counts_as_unrouted():
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = (logger.handlers[:], logger.level, logger.propagate)
    logger.handlers, logger.propagate = [logging.NullHandler(level=logging.WARNING)], False
    logger.setLevel(logging.INFO)
    try:
        assert not audit.audit_logger_routed()
        logger.handlers[0].setLevel(logging.INFO)
        assert audit.audit_logger_routed()
    finally:
        logger.handlers, logger.level, logger.propagate = saved


def test_a_failing_handler_never_fails_the_request():
    # A handler's own write errors are logging's business (and the log platform's gap
    # detection); record() just must not raise.
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = (logger.handlers[:], logger.level, logger.propagate)
    logger.handlers, logger.propagate = [BrokenHandler(level=logging.INFO)], False
    logger.setLevel(logging.INFO)
    try:
        make_service().record("dataset.display", make_hda())
    finally:
        logger.handlers, logger.level, logger.propagate = saved


def test_failures_are_counted_and_sent_to_statsd(audit_handler, monkeypatch, caplog):
    calls: list[tuple[str, Any]] = []
    statsd = SimpleNamespace(incr=lambda path, n=1, tags=None: calls.append((path, tags)))

    def broken_fit(event, max_bytes=MAX_EVENT_BYTES):
        raise RuntimeError("cannot serialize")

    monkeypatch.setattr(audit, "fit_event", broken_fit)
    before = audit_failures.count
    with caplog.at_level(logging.ERROR, logger="galaxy.managers.audit"):
        make_service(statsd_client=statsd).record("dataset.display", make_hda())
    assert audit_failures.count == before + 1
    assert calls == [("galaxy.audit.failures", {"kind": "emit"})]
    assert any("Failed to write audit event" in r.getMessage() for r in caplog.records)


def test_reporting_a_failure_never_raises(audit_handler, monkeypatch):
    def broken_error(*args, **kwargs):
        raise RuntimeError("diagnostic logging is broken too")

    def broken_fit(event, max_bytes=MAX_EVENT_BYTES):
        raise RuntimeError("cannot serialize")

    monkeypatch.setattr(audit.log, "error", broken_error)
    monkeypatch.setattr(audit, "fit_event", broken_fit)
    statsd = SimpleNamespace(incr=broken_error)
    # Neither the failed write nor the failed report escapes record().
    make_service(statsd_client=statsd).record("dataset.display", make_hda())
    assert audit_handler.lines == []


def test_mock_app_registers_the_service(monkeypatch):
    # Galaxy's config points tempfile.tempdir at the mock app's directory; don't leak it.
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    app = MockApp()
    tempdir = tempfile.tempdir
    service = app[AuditService]
    # Built on demand, the container would make a whole Galaxy configuration, which repoints
    # tempfile's directory for every later test in the process.
    assert tempfile.tempdir == tempdir
    assert audit.audit_service_for(app) is service
    assert not service.enabled


# -- attempts -----------------------------------------------------------------------


def requested_hda(dataset_id=42):
    return AuditObject(type="hda", id=dataset_id)


def test_attempt_success_names_the_authorized_object(audit_handler):
    attempt = make_service().attempt("dataset.display", requested_hda(), DatasetContentDetails())
    with in_request():
        with attempt.guard():
            attempt.authorized(make_hda())
            attempt.succeeded()
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["stage"], event["object"]["uuid"]) == (
        "success",
        "prepare",
        "12345678-1234-5678-1234-567812345678",
    )


def test_denied_attempt_names_what_was_requested_without_a_lookup(audit_handler):
    with in_request():
        attempt = make_service().attempt("dataset.display", requested_hda(77))
        with pytest.raises(exceptions.ItemAccessibilityException):
            with attempt.guard():
                raise exceptions.ItemAccessibilityException("nope")
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["reason"], event["stage"]) == ("denied", "not_accessible", "authorize")
    assert event["object"]["id"] == 77 and event["object"]["encoded_id"] == SECURITY.encode_id(77)
    assert event["actor"]["id"] == 1


class RangeNotSatisfiable(Exception):
    # Like the HTTPException GalaxyFileResponse raises for a bad Range header.
    status_code = 416


@pytest.mark.parametrize(
    "exc,outcome,reason",
    [
        (exceptions.ObjectNotFound("gone"), "error", "not_found"),
        (exceptions.RequestParameterInvalidException("bad"), "error", "invalid_request"),
        (exceptions.InternalServerError("object store down"), "error", "internal_error"),
        (OSError("disk"), "error", "internal_error"),
        (RangeNotSatisfiable(), "error", "invalid_range"),
    ],
)
def test_failures_are_classified(exc, outcome, reason):
    assert audit.classify_failure(exc) == (outcome, reason)


def test_storage_failure_after_authorization_is_an_error_at_prepare(audit_handler):
    with in_request():
        attempt = make_service().attempt("dataset.download", requested_hda())
        with pytest.raises(exceptions.InternalServerError):
            with attempt.guard():
                attempt.authorized(make_hda())
                raise exceptions.InternalServerError("object store down")
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["reason"], event["stage"]) == ("error", "internal_error", "prepare")
    assert "object store down" not in audit_handler.lines[0]


def test_attempt_left_unsettled_is_an_error_not_a_success(audit_handler):
    attempt = make_service().attempt("dataset.display", requested_hda())
    with attempt.guard():
        attempt.authorized(make_hda())
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["reason"]) == ("error", "internal_error")


def test_attempt_emits_exactly_once(audit_handler):
    attempt = make_service().attempt("dataset.display", requested_hda())
    attempt.authorized(make_hda())
    attempt.succeeded()
    attempt.failed("internal_error")
    attempt.response_started(200)
    assert len(audit_handler.lines) == 1


def test_head_attempts_record_denials_but_not_success(audit_handler):
    service = make_service()
    head = service.attempt("dataset.display", requested_hda(), record_success=False)
    head.authorized(make_hda())
    head.response_started(200)
    assert audit_handler.lines == []
    denied = service.attempt("dataset.display", requested_hda(), record_success=False)
    denied.failed_with(exceptions.ItemAccessibilityException("nope"))
    assert json.loads(audit_handler.lines[0])["outcome"] == "denied"


def test_error_status_at_response_start_is_not_success(audit_handler):
    attempt = make_service().attempt("dataset.display", requested_hda())
    attempt.authorized(make_hda())
    attempt.hand_off()
    attempt.response_started(404)
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["reason"], event["stage"]) == ("error", "error_response", "respond")


def test_details_added_later_join_those_given_at_the_start(audit_handler):
    attempt = make_service().attempt("dataset.download_url", requested_hda(), DownloadUrlDetails(to_ext="bam"))
    attempt.authorized(make_hda())
    attempt.add_details(DownloadUrlDetails(to_ext="bam", url_host="bucket.example.org"))
    attempt.succeeded(details=lambda: DownloadUrlDetails(to_ext="bam", expires_in=3600), stage="respond")
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["stage"]) == ("success", "respond")
    assert event["details"] == {"to_ext": "bam", "url_host": "bucket.example.org", "expires_in": 3600}


def test_details_that_fail_to_build_cost_only_the_details(audit_handler):
    def broken():
        raise ValueError("a repeated query parameter")

    attempt = make_service().attempt("dataset.download_url", requested_hda(), DownloadUrlDetails(to_ext="bam"))
    attempt.add_details(broken)
    attempt.succeeded(details=DatasetContentDetails())
    event = json.loads(audit_handler.lines[0])
    assert event["outcome"] == "success"
    assert event["details"] == {"to_ext": "bam"}
    assert event["truncated"] == ["details", "details"]


def test_head_success_builds_no_details(audit_handler):
    def must_not_run():
        raise AssertionError("built for an event that is never written")

    attempt = make_service().attempt("dataset.display", requested_hda(), record_success=False)
    attempt.succeeded(details=must_not_run)
    assert attempt.settled and audit_handler.lines == []


def test_withdrawn_attempt_records_nothing(audit_handler):
    attempt = make_service().attempt("dataset.download_url", requested_hda())
    with attempt.guard():
        attempt.authorized(make_hda())
        attempt.withdraw()
    attempt.add_details(DownloadUrlDetails(url_host="late.example.org"))
    attempt.response_started(200)
    assert audit_handler.lines == []


def test_denied_without_an_exception(audit_handler):
    attempt = make_service().attempt("drs.object", requested_hda(77))
    attempt.authorized(make_hda())
    attempt.denied()
    event = json.loads(audit_handler.lines[0])
    assert (event["outcome"], event["reason"], event["stage"]) == ("denied", "not_accessible", "prepare")


def test_null_attempt_accepts_every_call():
    def must_not_run():
        raise AssertionError("built for an action that isn't audited")

    with NULL_ATTEMPT.guard():
        NULL_ATTEMPT.authorized(object())
        NULL_ATTEMPT.add_details(must_not_run)
        NULL_ATTEMPT.succeeded(details=must_not_run)
        NULL_ATTEMPT.denied()
        NULL_ATTEMPT.withdraw()
        NULL_ATTEMPT.failed("internal_error")
        NULL_ATTEMPT.failed_with(OSError())
        NULL_ATTEMPT.response_started(200)
        NULL_ATTEMPT.hand_off()
    assert not NULL_ATTEMPT.active


def test_failure_to_start_an_attempt_returns_a_null_attempt(monkeypatch):
    service = make_service()

    def broken_prepare(*args):
        raise RuntimeError("broken")

    monkeypatch.setattr(service, "_prepare", broken_prepare)
    assert service.attempt("dataset.display", requested_hda()) is NULL_ATTEMPT
    service.record("dataset.display", make_hda())


def test_event_over_budget_after_shedding_is_marked():
    event = {"details": {}, "truncated": [], "request_id": "x" * 100, "object": {"type": "hda", "id": 1}}
    line = audit.fit_event(event, max_bytes=50)
    parsed = json.loads(line)
    assert parsed["truncated"] == ["optional_fields", "over_budget"]
    # A minimal object doesn't grow name keys it never had.
    assert parsed["object"] == {"type": "hda", "id": 1}
