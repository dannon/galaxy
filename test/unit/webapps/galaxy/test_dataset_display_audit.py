"""Audit events from the dataset display route, driven through a real FastAPI app.

The route, the auth dependencies, DatasetsService.display and Galaxy's response
classes are all real; only the managers behind the service and the app container
are stand-ins.
"""

import asyncio
import inspect
import json
import logging
import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import (
    Depends,
    FastAPI,
)
from fastapi.security import APIKeyCookie
from fastapi.testclient import TestClient
from sqlalchemy.orm import make_transient_to_detached
from starlette.responses import Response

from galaxy import (
    app as galaxy_app,
    model,
)
from galaxy.exceptions import (
    ItemAccessibilityException,
    ObjectNotFound,
)
from galaxy.managers.audit import (
    AUDIT_LOGGER_NAME,
    AuditObject,
    AuditService,
)
from galaxy.schema.fields import Security as IdSecurity
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.web.framework.request_scope import request_scope
from galaxy.webapps.base.api import (
    add_exception_handler,
    add_raw_context_middlewares,
)
from galaxy.webapps.base.audit import audited_response
from galaxy.webapps.galaxy.api import (
    get_api_user,
    get_session,
)
from galaxy.webapps.galaxy.api.datasets import router
from galaxy.webapps.galaxy.services.datasets import DatasetsService

SECURITY = IdEncodingHelper(id_secret="display-audit-test")
SESSION_COOKIE = "secret-session-cookie-0123456789"
CONTENT = b"0123456789"


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events: list[dict] = []

    def emit(self, record):
        self.events.append(json.loads(record.getMessage()))


def detached(instance, instance_id):
    instance.id = instance_id
    make_transient_to_detached(instance)
    return instance


SESSION_USER = detached(model.User(email="alice@example.org"), 7)


def impersonated_session(galaxysession: str = Depends(APIKeyCookie(name="galaxysession", auto_error=False))):
    if galaxysession != SESSION_COOKIE:
        return None
    galaxy_session = model.GalaxySession(user=SESSION_USER, impersonated_by_user_id=1, current_history=None)
    return detached(galaxy_session, 99)


def make_hda(tmp_path):
    path = tmp_path / "dataset_11.dat"
    path.write_bytes(CONTENT)
    # A spec'd mock passes the isinstance checks the audit description relies on,
    # while letting the test stub out the datatype and storage.
    hda = MagicMock(spec=model.HistoryDatasetAssociation)
    hda.id, hda.dataset_id, hda.history_id, hda.name = 42, 11, 3, "reads.fastq"
    hda.history = SimpleNamespace(user_id=7, name="Alice's history")
    hda.dataset = SimpleNamespace(uuid=uuid.UUID("12345678-1234-5678-1234-567812345678"))
    hda.extension = "txt"
    hda.get_file_name.return_value = str(path)
    hda.datatype.is_archive_download.return_value = False
    hda.datatype.display_data.side_effect = lambda *args, **kwargs: (open(path, "rb"), {})
    return hda


class Harness:
    def __init__(self, tmp_path, monkeypatch, audit_settings):
        self.hda = make_hda(tmp_path)
        config = SimpleNamespace(audit_log=audit_settings, server_name="main.web.1", galaxy_infrastructure_url=None)
        self.audit = AuditService(config, SECURITY, MagicMock())  # type: ignore[arg-type]
        self.service = DatasetsService(SECURITY, *(MagicMock() for _ in range(9)))
        hda_manager = cast(MagicMock, self.service.hda_manager)
        hda_manager.get_accessible.return_value = self.hda
        self.hda_manager = hda_manager
        registry = {DatasetsService: self.service, AuditService: self.audit}
        model_mapping = SimpleNamespace(
            set_request_id=lambda request_id: None,
            unset_request_id=lambda request_id: None,
            scoped_registry=SimpleNamespace(registry={}),
            request_scopefunc=lambda: None,
        )
        stub_app = SimpleNamespace(
            resolve=registry.__getitem__,
            model=model_mapping,
            install_model=model_mapping,
            config=SimpleNamespace(),
            object_store=MagicMock(),
            datatypes_registry=MagicMock(),
        )
        stub_app.object_store.get_data_stream.return_value = None
        monkeypatch.setattr(galaxy_app, "app", stub_app, raising=False)
        monkeypatch.setattr(IdSecurity, "security", SECURITY, raising=False)

        app = FastAPI()
        add_raw_context_middlewares(app)
        add_exception_handler(app)
        app.include_router(router)
        app.dependency_overrides[get_session] = impersonated_session
        app.dependency_overrides[inspect.signature(get_api_user).parameters["user_manager"].default.dependency] = (
            lambda: MagicMock()
        )
        self.client = TestClient(app, cookies={"galaxysession": SESSION_COOKIE})


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


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch, {"enabled": True})


def display_url(**params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"/api/datasets/{SECURITY.encode_id(42)}/display" + (f"?{query}" if query else "")


def test_display_success_names_who_and_what(harness, audit_events):
    response = harness.client.get(display_url(raw="true"))
    assert response.status_code == 200 and response.content == CONTENT
    (event,) = audit_events
    assert (event["action"], event["outcome"], event["stage"]) == ("dataset.display", "success", "respond")
    # The session was created by impersonation, so the admin is the actor.
    assert (event["actor"]["id"], event["effective_user"]["id"]) == (1, 7)
    assert event["auth"] == {"method": "session", "credential_id": 99, "switch": "impersonation"}
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert event["object"]["uuid"] == "12345678-1234-5678-1234-567812345678"
    assert event["details"] == {"raw": True}
    assert "alice@example.org" not in json.dumps(event) and "reads.fastq" not in json.dumps(event)


def test_download_with_to_ext_is_a_download(harness, audit_events):
    harness.client.get(display_url(to_ext="txt"))
    (event,) = audit_events
    assert (event["action"], event["outcome"]) == ("dataset.download", "success")
    assert event["details"] == {"to_ext": "txt"}


def test_valid_range_is_recorded_with_the_range(harness, audit_events):
    response = harness.client.get(display_url(raw="true"), headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == CONTENT[2:6]
    (event,) = audit_events
    assert event["outcome"] == "success"
    assert event["details"]["http_range"] == "bytes=2-5"


def test_invalid_range_is_an_error_not_a_success(harness, audit_events):
    response = harness.client.get(display_url(raw="true"), headers={"Range": "bytes=50-60"})
    assert response.status_code == 416
    (event,) = audit_events
    assert (event["outcome"], event["reason"], event["stage"]) == ("error", "invalid_range", "respond")


def test_head_records_no_access(harness, audit_events):
    response = harness.client.head(display_url(raw="true"))
    assert response.status_code == 200
    assert audit_events == []


def test_denial_names_the_requested_dataset(harness, audit_events):
    harness.hda_manager.get_accessible.side_effect = ItemAccessibilityException("nope")
    response = harness.client.get(display_url())
    assert response.status_code == 403
    (event,) = audit_events
    assert (event["outcome"], event["reason"], event["stage"]) == ("denied", "not_accessible", "authorize")
    assert event["object"] == {
        "type": "hda",
        "id": 42,
        "encoded_id": SECURITY.encode_id(42),
        "uuid": None,
        "dataset_id": None,
        "history_id": None,
        "owner_id": None,
        "name": None,
        "history_name": None,
    }


def test_head_still_records_a_denial(harness, audit_events):
    harness.hda_manager.get_accessible.side_effect = ItemAccessibilityException("nope")
    harness.client.head(display_url())
    assert [event["outcome"] for event in audit_events] == ["denied"]


def test_missing_file_after_authorization_is_an_error_at_prepare(harness, audit_events):
    harness.hda.datatype.display_data.side_effect = ObjectNotFound("File Not Found")
    response = harness.client.get(display_url())
    assert response.status_code == 404
    (event,) = audit_events
    assert (event["outcome"], event["reason"], event["stage"]) == ("error", "not_found", "prepare")
    assert event["object"]["uuid"] == "12345678-1234-5678-1234-567812345678"


def test_storage_failure_is_an_error_without_its_message(harness, audit_events):
    harness.hda.datatype.display_data.side_effect = OSError("object store said /secret/path")
    response = harness.client.get(display_url())
    assert response.status_code == 500
    (event,) = audit_events
    assert (event["outcome"], event["reason"], event["stage"]) == ("error", "internal_error", "prepare")
    assert "/secret/path" not in json.dumps(event)


def test_composite_archive_error_page_is_an_error(harness, audit_events):
    harness.hda.datatype.is_archive_download.return_value = True
    harness.hda.datatype.display_data.side_effect = lambda *args, **kwargs: ("<html>archive failed</html>", {})
    response = harness.client.get(display_url(to_ext="zip"))
    # The client still gets the page Galaxy has always returned.
    assert response.status_code == 200
    (event,) = audit_events
    assert (event["outcome"], event["reason"], event["stage"]) == ("error", "archive_failed", "prepare")


def test_audit_query_parameter_cannot_collide_with_the_attempt(harness, audit_events):
    response = harness.client.get(display_url(raw="true", audit_attempt="x"))
    assert response.status_code == 200
    assert [event["outcome"] for event in audit_events] == ["success"]


def test_disabled_audit_leaves_the_response_alone(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": False})
    response = harness.client.get(display_url(raw="true"), headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == CONTENT[2:6]
    assert audit_events == []


def test_denied_library_dataset_is_named_as_one(harness, audit_events):
    ldda_manager = cast(MagicMock, harness.service.ldda_manager)
    ldda_manager.get_accessible.side_effect = ItemAccessibilityException("nope")
    response = harness.client.get(display_url(hda_ldda="ldda"))
    assert response.status_code == 403
    (event,) = audit_events
    assert (event["outcome"], event["object"]["type"], event["object"]["id"]) == ("denied", "ldda", 42)
    assert event["details"] == {"source": "ldda"}


def test_composite_preview_page_is_not_an_archive_failure(harness, audit_events):
    harness.hda.datatype.is_archive_download.return_value = True
    harness.hda.datatype.display_data.side_effect = lambda *args, **kwargs: ("<html>preview</html>", {})
    harness.client.get(display_url(to_ext="zip", preview="true"))
    (event,) = audit_events
    assert event["outcome"] == "success"


def test_remote_user_sessions_say_so(harness, audit_events):
    config = cast(SimpleNamespace, galaxy_app.app).config
    config.use_remote_user, config.remote_user_header = True, "HTTP_REMOTE_USER"
    harness.client.get(display_url(raw="true"))
    (event,) = audit_events
    assert event["auth"]["method"] == "remote_user"


def test_response_handed_off_but_never_started_is_one_error(harness, audit_events):
    attempt = harness.audit.attempt("dataset.display", AuditObject(type="hda", id=42))
    with request_scope():
        attempt.authorized(harness.hda)
        audited_response(Response(b"never sent"), attempt)
    # The scope closing is the end of the request; nobody called the response.
    assert [(e["outcome"], e["reason"], e["stage"]) for e in audit_events] == [
        ("error", "response_not_started", "respond")
    ]


def test_response_started_is_not_settled_again_at_close(harness, audit_events):
    attempt = harness.audit.attempt("dataset.display", AuditObject(type="hda", id=42))
    with request_scope():
        audited_response(Response(b"sent"), attempt)
        attempt.response_started(200)
    assert [e["outcome"] for e in audit_events] == ["success"]


class CancelledBeforeStart:
    """ASGI middleware standing in for a server that cancels the request as the response starts.

    uvicorn instead accepts a send after a disconnect without complaint, so there the
    event says success: the start was handed to the server.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def disconnected_send(message):
            if message["type"] == "http.response.start":
                raise asyncio.CancelledError()
            await send(message)

        try:
            await self.app(scope, receive, disconnected_send)
        except asyncio.CancelledError:
            await Response(status_code=499)(scope, receive, send)


def test_cancelled_before_start_is_one_error_not_a_success(harness, audit_events):
    app = harness.client.app
    app.add_middleware(CancelledBeforeStart)
    harness.client.get(display_url(raw="true"))
    assert [(e["outcome"], e["reason"], e["stage"]) for e in audit_events] == [
        ("error", "response_not_started", "respond")
    ]


def test_disabled_audit_skips_the_archive_check(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": False})
    harness.hda.datatype.display_data.side_effect = lambda *args, **kwargs: ("<html>page</html>", {})
    # A Range header keeps the object-store streaming path, which has its own archive check, out of it.
    harness.client.get(display_url(to_ext="zip"), headers={"Range": "bytes=0-1"})
    harness.hda.datatype.is_archive_download.assert_not_called()
