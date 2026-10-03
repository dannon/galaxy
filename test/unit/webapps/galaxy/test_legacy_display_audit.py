"""Audit events from the legacy routes that hand dataset content to external display sites.

The controller methods are real; the transaction, the models and the display
application machinery around them are stand-ins.
"""

import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import webob.exc

from galaxy import (
    exceptions,
    model,
)
from galaxy.datatypes.data import Data
from galaxy.managers.audit import (
    AUDIT_LOGGER_NAME,
    AuditService,
)
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.web.framework.base import Response as LegacyResponse
from galaxy.web.framework.request_scope import (
    request_scope,
    RequestIdentity,
)
from galaxy.webapps.galaxy.controllers.dataset import DatasetInterface
from galaxy.webapps.galaxy.controllers.root import RootController

SECURITY = IdEncodingHelper(id_secret="legacy-display-audit-test")
CONTROLLER_MODULE = "galaxy.webapps.galaxy.controllers.dataset"
EXTERNAL = "https://genome.example.edu/cgi-bin/hgTracks?db=hg38&position=chr1"


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events: list[dict] = []

    def emit(self, record):
        self.events.append(json.loads(record.getMessage()))


@pytest.fixture
def audit_events():
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    saved = (logger.handlers[:], logger.level, logger.propagate)
    handler = CapturingHandler()
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield handler.events
    finally:
        logger.handlers, logger.level, logger.propagate = saved


def make_hda(tmp_path):
    path = tmp_path / "dataset_11.dat"
    path.write_bytes(b"track bytes")
    hda = MagicMock(spec=model.HistoryDatasetAssociation)
    hda.id, hda.dataset_id, hda.history_id, hda.name = 42, 11, 3, "variants.bed"
    hda.history = SimpleNamespace(user_id=7, name="Alice's history")
    hda.dataset = SimpleNamespace(uuid=uuid.UUID(int=11))
    hda.states = model.Dataset.states
    hda.state = model.Dataset.states.OK
    hda.deleted = False
    hda.get_mime.return_value = "text/plain"
    hda.as_display_type.return_value = "formatted for ucsc"
    hda.ext = "bed"
    hda.path = str(path)
    return hda


class Harness:
    def __init__(self, tmp_path, monkeypatch, audit_settings):
        config = SimpleNamespace(audit_log=audit_settings, server_name="main.web.1", galaxy_infrastructure_url=None)
        self.audit = AuditService(config, SECURITY, MagicMock())  # type: ignore[arg-type]
        self.hda = make_hda(tmp_path)
        self.link_user = SimpleNamespace(id=7, all_roles=lambda: [])
        self.app = MagicMock()
        self.app.config.enable_old_display_applications = True
        self.app.security_agent.dataset_is_public.return_value = False
        self.app.security_agent.can_access_dataset.return_value = True
        self.app.host_security_agent.allow_action.return_value = True
        self.redirects: list[str] = []
        self.trans = SimpleNamespace(
            app=self.app,
            user=None,
            user_is_admin=False,
            get_current_user_roles=lambda: [],
            sa_session=MagicMock(),
            request=SimpleNamespace(remote_addr="203.0.113.9", method="GET"),
            # The real legacy response, so send_redirect validates and raises HTTPFound as it does in Galaxy.
            response=LegacyResponse(),
            show_error_message=lambda message: f"error: {message}",
            log_event=lambda message: None,
            set_cors_origin=lambda: None,
            set_cors_allow=lambda: None,
        )
        self.trans.sa_session.query.return_value.get.return_value = self.hda
        self.trans.sa_session.get.return_value = self.hda

        self.datasets = DatasetInterface.__new__(DatasetInterface)
        self.datasets.app = self.app
        self.datasets.audit = self.audit
        self.datasets.service = MagicMock()
        self.root = RootController.__new__(RootController)
        self.root.app = self.app
        self.root.audit = self.audit

        monkeypatch.setattr(f"{CONTROLLER_MODULE}.decode_dataset_user", lambda trans, d, u: (self.hda, self.link_user))
        monkeypatch.setattr(f"{CONTROLLER_MODULE}.encode_dataset_user", lambda trans, d, u: ("dh", "uh"))
        value = MagicMock()
        value.parameter.viewable = True
        value.parameter.type = "data"
        value.parameter.allow_cors = False
        value.get_file_name.return_value = self.hda.path
        value.mime_type.return_value = "text/plain"
        self.display_link = MagicMock()
        self.display_link.display_ready.return_value = True
        self.display_link.display_url.return_value = EXTERNAL
        self.display_link.get_param_name_by_url.return_value = "bed_file"
        self.display_link.get_param_value.return_value = value
        display_app = MagicMock()
        display_app.get_link.return_value = self.display_link
        self.app.datatypes_registry.display_applications.get.return_value = display_app

    def call(self, method, *args, **kwargs):
        with request_scope(request_id="req-1") as scope:
            scope.identity = RequestIdentity("anonymous")
            try:
                return method(self.trans, *args, **kwargs)
            except webob.exc.HTTPFound as redirect:
                # How the legacy stack sends a redirect; the framework turns it into the response.
                self.redirects.append(redirect.location)
                return redirect


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch, {"enabled": True})


def summary(events):
    return [(e["action"], e["outcome"], e["reason"]) for e in events]


def outcomes(events):
    return [(e["action"], e["outcome"], e["reason"], e["stage"]) for e in events]


# -- display_at -------------------------------------------------------------------


def test_display_at_grant_names_the_site_and_host_only(harness, audit_events):
    harness.call(
        harness.datasets.display_at,
        "42",
        filename="ucsc_main",
        display_url="https://galaxy.example.org/display_as?id=42&secret=1",
        redirect_url="https://genome.example.edu/cgi-bin/hgTracks?url=%s",
    )
    assert len(harness.redirects) == 1
    (event,) = audit_events
    assert summary([event]) == [("dataset.external_link", "success", None)]
    assert event["details"] == {
        "via": "display_at",
        "site": "ucsc_main",
        "target_host": "genome.example.edu",
        "public": False,
    }
    assert "secret" not in json.dumps(event) and "hgTracks" not in json.dumps(event)
    harness.app.host_security_agent.set_dataset_permissions.assert_called_once()


def test_display_at_public_dataset_says_so(harness, audit_events):
    harness.app.security_agent.dataset_is_public.return_value = True
    harness.call(harness.datasets.display_at, "42", filename="ucsc_main", display_url="x", redirect_url=EXTERNAL)
    (event,) = audit_events
    assert event["details"]["public"] is True


def test_display_at_to_a_url_send_redirect_refuses_is_not_a_success(harness, audit_events):
    harness.app.security_agent.dataset_is_public.return_value = True
    with pytest.raises(webob.exc.HTTPInternalServerError):
        harness.call(
            harness.datasets.display_at,
            "42",
            filename="ucsc_main",
            display_url="x",
            redirect_url="https://genome.example.edu/\r\nLocation: https://evil.example",
        )
    assert harness.redirects == []
    # The URL came from the request, so the refusal is the request's fault.
    assert outcomes(audit_events) == [("dataset.external_link", "error", "invalid_request", "respond")]


def test_head_on_display_at_and_launch_records_no_success(harness, audit_events):
    harness.trans.request.method = "HEAD"
    harness.call(harness.datasets.display_at, "42", filename="ucsc_main", display_url="x", redirect_url=EXTERNAL)
    harness.call(harness.datasets.display_application, "dh", "uh", app_name="ucsc_bed", link_name="main")
    assert harness.redirects == [EXTERNAL, EXTERNAL]
    assert audit_events == []
    harness.app.security_agent.can_access_dataset.return_value = False
    harness.call(harness.datasets.display_at, "42", filename="ucsc_main", display_url="x", redirect_url=EXTERNAL)
    assert summary(audit_events) == [("dataset.external_link", "denied", "not_accessible")]


@pytest.mark.parametrize("enabled", [True, False])
def test_display_at_with_a_repeated_filename_still_redirects(tmp_path, monkeypatch, audit_events, enabled):
    harness = Harness(tmp_path, monkeypatch, {"enabled": enabled})
    harness.app.security_agent.dataset_is_public.return_value = True
    harness.call(
        harness.datasets.display_at,
        "42",
        filename=["ucsc_main", "ucsc_main"],
        display_url="x",
        redirect_url=EXTERNAL,
    )
    assert harness.redirects == [EXTERNAL]
    if enabled:
        (event,) = audit_events
        # The details couldn't take a list, so they're left out; the event itself still stands.
        assert summary([event]) == [("dataset.external_link", "success", None)] and event["details"] == {}
    else:
        assert audit_events == []


def test_display_at_refusal_is_a_denial(harness, audit_events):
    harness.app.security_agent.can_access_dataset.return_value = False
    result = harness.call(
        harness.datasets.display_at, "42", filename="ucsc_main", display_url="x", redirect_url=EXTERNAL
    )
    assert result.startswith("error:") and harness.redirects == []
    assert summary(audit_events) == [("dataset.external_link", "denied", "not_accessible")]


# -- display applications -----------------------------------------------------------


def test_display_application_launch_records_the_link(harness, audit_events):
    harness.call(harness.datasets.display_application, "dh", "uh", app_name="ucsc_bed", link_name="main")
    assert harness.redirects == [EXTERNAL]
    (event,) = audit_events
    assert summary([event]) == [("dataset.external_link", "success", None)]
    assert event["details"] == {
        "via": "display_application",
        "app_name": "ucsc_bed",
        "link_name": "main",
        "target_host": "genome.example.edu",
    }
    assert "hgTracks" not in json.dumps(event)


def test_external_site_fetch_names_who_the_link_was_for(harness, audit_events):
    handle = harness.call(
        harness.datasets.display_application,
        "dh",
        "uh",
        app_name="ucsc_bed",
        link_name="main",
        app_action="data",
        action_param="galaxy.bed",
    )
    assert handle.read() == b"track bytes"
    handle.close()
    (event,) = audit_events
    assert summary([event]) == [("dataset.external_fetch", "success", None)]
    assert event["effective_user"] is None
    assert event["details"] == {
        "via": "display_application",
        "app_name": "ucsc_bed",
        "link_name": "main",
        "app_action": "data",
        # The parameter the link's URL name resolved to.
        "action_param": "bed_file",
        "link_user_id": 7,
    }


def test_display_application_refusals(harness, audit_events):
    harness.app.security_agent.can_access_dataset.return_value = False
    harness.call(harness.datasets.display_application, "dh", "uh", app_name="ucsc_bed", link_name="main")
    harness.call(
        harness.datasets.display_application,
        "dh",
        "uh",
        app_name="ucsc_bed",
        link_name="main",
        app_action="data",
        action_param="galaxy.bed",
    )
    assert summary(audit_events) == [
        ("dataset.external_link", "denied", "not_accessible"),
        ("dataset.external_fetch", "denied", "not_accessible"),
    ]


def test_display_application_messages_page_is_an_error(harness, audit_events):
    harness.display_link.display_ready.return_value = False
    result = harness.call(harness.datasets.display_application, "dh", "uh", app_name="ucsc_bed", link_name="main")
    assert "msg" in result and harness.redirects == []
    assert summary(audit_events) == [("dataset.external_link", "error", "invalid_request")]


def fetch(harness, **kwargs):
    params = dict(app_name="ucsc_bed", link_name="main", app_action="data", action_param="galaxy.bed")
    params.update(kwargs)
    return harness.call(harness.datasets.display_application, "dh", "uh", **params)


def test_fetching_an_extra_file_the_parameter_does_not_share_is_a_denial(harness, audit_events):
    harness.display_link.get_param_value.return_value.parameter.allow_extra_files_access = False
    with pytest.raises(AssertionError):
        fetch(harness, action_param_extra="../other_dataset.dat")
    assert summary(audit_events) == [("dataset.external_fetch", "denied", "not_accessible")]


def test_fetching_a_parameter_that_is_not_viewable_is_a_denial(harness, audit_events):
    harness.display_link.get_param_value.return_value.parameter.viewable = False
    with pytest.raises(AssertionError):
        fetch(harness)
    harness.display_link.get_param_value.return_value = None
    with pytest.raises(AssertionError):
        fetch(harness)
    assert summary(audit_events) == [
        ("dataset.external_fetch", "denied", "not_accessible"),
        ("dataset.external_fetch", "error", "invalid_request"),
    ]


def test_fetching_a_missing_file_is_an_error(harness, audit_events):
    harness.display_link.get_param_value.return_value.get_file_name.return_value = "/nonexistent/dataset.dat"
    result = fetch(harness)
    assert result.code == 404
    assert summary(audit_events) == [("dataset.external_fetch", "error", "not_found")]


def test_fetching_from_a_dataset_that_is_not_ready_is_an_error(harness, audit_events):
    harness.hda.state = model.Dataset.states.RUNNING
    result = fetch(harness)
    assert "msg" in result
    harness.hda.state = model.Dataset.states.OK
    harness.display_link.display_ready.return_value = False
    assert fetch(harness).startswith("error:")
    assert summary(audit_events) == [
        ("dataset.external_fetch", "error", "invalid_request"),
        ("dataset.external_fetch", "error", "invalid_request"),
    ]


def test_unknown_display_application_or_link_is_not_found(harness, audit_events):
    harness.app.datatypes_registry.display_applications.get.return_value = None
    assert fetch(harness).code == 404
    assert summary(audit_events) == [("dataset.external_fetch", "error", "not_found")]


def test_launch_to_a_url_send_redirect_refuses_is_not_a_success(harness, audit_events):
    harness.display_link.display_url.return_value = "https://genome.example.edu/\r\nSet-Cookie: x=1"
    with pytest.raises(webob.exc.HTTPInternalServerError):
        harness.call(harness.datasets.display_application, "dh", "uh", app_name="ucsc_bed", link_name="main")
    assert harness.redirects == []
    assert outcomes(audit_events) == [("dataset.external_link", "error", "internal_error", "respond")]


# -- display_as ---------------------------------------------------------------------


def test_display_as_success_and_authz_method(harness, audit_events):
    content = harness.call(harness.root.display_as, id="42", display_app="ucsc", authz_method="display_at")
    assert content == "formatted for ucsc"
    (event,) = audit_events
    assert summary([event]) == [("dataset.external_fetch", "success", None)]
    assert event["details"] == {"via": "display_as", "app_name": "ucsc", "authz_method": "display_at"}
    assert event["object"]["uuid"] == str(uuid.UUID(int=11))


@pytest.mark.parametrize("enabled", [True, False])
def test_display_as_repeated_authz_method_is_refused_as_before(tmp_path, monkeypatch, audit_events, enabled):
    harness = Harness(tmp_path, monkeypatch, {"enabled": enabled})
    result = harness.call(harness.root.display_as, id="42", display_app="ucsc", authz_method=["rbac", "rbac"])
    assert harness.trans.response.status == 403 and result == "You are not allowed to access this dataset."
    if enabled:
        (event,) = audit_events
        assert summary([event]) == [("dataset.external_fetch", "denied", "not_accessible")]
        assert event["details"] == {}
    else:
        assert audit_events == []


def test_display_as_without_content_is_not_a_success(harness, audit_events):
    class Track(Data):
        supported_display_apps = {"ucsc": {"file_function": "broken_track"}}

        def get_display_types(self):
            return ["ucsc"]

        def broken_track(self, dataset, **kwd):
            raise OSError("cannot build the track")

    # Real Data.as_display_type: a type it doesn't offer, and one whose function fails, both fall back to a message.
    harness.hda.as_display_type.side_effect = lambda display_app, **kwd: Track().as_display_type(
        harness.hda, display_app
    )
    for display_app in ("bogus", "ucsc"):
        content = harness.call(harness.root.display_as, id="42", display_app=display_app)
        assert content == f"This display type ({display_app}) is not implemented for this datatype (bed)."
    assert summary(audit_events) == [("dataset.external_fetch", "error", "invalid_request")] * 2


def test_display_as_refused_host_is_a_denial(harness, audit_events):
    harness.app.host_security_agent.allow_action.return_value = False
    harness.call(harness.root.display_as, id="42", display_app="ucsc", authz_method="display_at")
    assert harness.trans.response.status == 403
    (event,) = audit_events
    assert summary([event]) == [("dataset.external_fetch", "denied", "not_accessible")]
    assert (event["object"]["id"], event["object"]["uuid"]) == (42, None)


def test_display_as_missing_dataset_and_storage_failure(harness, audit_events):
    harness.trans.sa_session.get.return_value = None
    harness.call(harness.root.display_as, id="43", display_app="ucsc")
    harness.trans.sa_session.get.return_value = harness.hda
    harness.app.hda_manager.ensure_dataset_on_disk.side_effect = exceptions.ObjectNotFound("/secret/path")
    harness.call(harness.root.display_as, id="42", display_app="ucsc")
    assert summary(audit_events) == [
        ("dataset.external_fetch", "error", "not_found"),
        ("dataset.external_fetch", "error", "not_found"),
    ]
    assert "/secret/path" not in json.dumps(audit_events)


def test_display_as_formatting_failure_is_an_error(harness, audit_events):
    harness.hda.as_display_type.side_effect = OSError("disk")
    with pytest.raises(OSError):
        harness.call(harness.root.display_as, id="42", display_app="ucsc")
    assert summary(audit_events) == [("dataset.external_fetch", "error", "internal_error")]


# -- legacy metadata file -------------------------------------------------------------


def test_legacy_metadata_file_success_and_denial(harness, audit_events):
    def get_metadata_file(trans, history_content_id, metadata_file, open_file, audit_attempt):
        audit_attempt.authorized(harness.hda)
        return "file-handle", {"Content-Type": "application/octet-stream"}

    harness.datasets.service.get_metadata_file.side_effect = get_metadata_file
    harness.datasets.decode_id = SECURITY.decode_id
    # Undecorated, so the exception reaches the test rather than an error page.
    get_file = DatasetInterface.get_metadata_file.__wrapped__.__get__(harness.datasets)
    assert harness.call(get_file, SECURITY.encode_id(42), "bam_index") == "file-handle"
    harness.datasets.service.get_metadata_file.side_effect = exceptions.ItemAccessibilityException("no")
    with pytest.raises(exceptions.ItemAccessibilityException):
        harness.call(get_file, SECURITY.encode_id(42), "bam_index")
    assert summary(audit_events) == [
        ("dataset.download_metadata_file", "success", None),
        ("dataset.download_metadata_file", "denied", "not_accessible"),
    ]
    assert audit_events[0]["details"] == {"metadata_file": "bam_index"}


def test_legacy_head_probes_record_refusals_but_not_access(harness, audit_events):
    harness.trans.request.method = "HEAD"
    handle = harness.call(
        harness.datasets.display_application,
        "dh",
        "uh",
        app_name="ucsc_bed",
        link_name="main",
        app_action="data",
        action_param="galaxy.bed",
    )
    handle.close()
    harness.call(harness.root.display_as, id="42", display_app="ucsc")
    assert audit_events == []
    harness.app.security_agent.can_access_dataset.return_value = False
    harness.call(harness.root.display_as, id="42", display_app="ucsc")
    assert summary(audit_events) == [("dataset.external_fetch", "denied", "not_accessible")]


def test_display_at_with_a_repeated_redirect_parameter_still_works(harness, audit_events):
    harness.call(
        harness.datasets.display_at,
        "42",
        filename="ucsc_main",
        display_url="x",
        redirect_url=[EXTERNAL, EXTERNAL],
    )
    (event,) = audit_events
    assert event["outcome"] == "success" and "target_host" not in event["details"]


def test_disabled_audit_records_nothing(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": False})
    harness.call(harness.datasets.display_application, "dh", "uh", app_name="ucsc_bed", link_name="main")
    harness.call(harness.datasets.display_at, "42", filename="ucsc_main", display_url="x", redirect_url=EXTERNAL)
    harness.call(harness.root.display_as, id="42", display_app="ucsc")
    assert audit_events == []
    assert harness.redirects == [EXTERNAL, EXTERNAL]
