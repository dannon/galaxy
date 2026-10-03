"""Audit events from export, archive and prepared-download routes, driven through a real FastAPI app.

The routes, the auth dependencies, the services and short-term storage are real;
the managers behind the services, the Celery tasks and the app container are
stand-ins.
"""

import inspect
import json
import logging
import random
import uuid
from datetime import datetime
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
from pydantic import ValidationError
from sqlalchemy.orm import make_transient_to_detached

from galaxy import (
    app as galaxy_app,
    model,
)
from galaxy.exceptions import (
    ItemAccessibilityException,
    RequestParameterInvalidException,
)
from galaxy.managers import hdcas
from galaxy.managers.audit import (
    AUDIT_LOGGER_NAME,
    AuditService,
)
from galaxy.managers.audit_actions import AuditObject
from galaxy.managers.audit_actions.exports import (
    ExportDetails,
    sanitize_target_uri,
)
from galaxy.managers.export_audit import ExportAudit
from galaxy.schema.fields import Security as IdSecurity
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.short_term_storage import (
    ShortTermStorageConfiguration,
    ShortTermStorageManager,
    ShortTermStorageMonitor,
)
from galaxy.webapps.base.api import (
    add_exception_handler,
    add_raw_context_middlewares,
)
from galaxy.webapps.galaxy.api import (
    get_api_user,
    get_session,
    histories as histories_api,
    history_contents as history_contents_api,
    short_term_storage as short_term_storage_api,
    workflows as workflows_api,
)
from galaxy.webapps.galaxy.services import (
    histories as histories_services,
    history_contents as history_contents_services,
    invocations as invocations_services,
)
from galaxy.webapps.galaxy.services.histories import HistoriesService
from galaxy.webapps.galaxy.services.history_contents import HistoriesContentsService
from galaxy.webapps.galaxy.services.invocations import InvocationsService

SECURITY = IdEncodingHelper(id_secret="export-audit-test")
SESSION_COOKIE = "secret-session-cookie-0123456789"
SECRET = "hunter2-very-secret"
HISTORY_NAME = "Alice's private history"


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


def make_history():
    history = MagicMock(spec=model.History)
    history.id, history.user_id, history.name = 3, 7, HISTORY_NAME
    history.latest_export = None
    return history


def make_hda():
    hda = MagicMock(spec=model.HistoryDatasetAssociation)
    hda.id, hda.dataset_id, hda.history_id, hda.name = 42, 11, 3, "reads.fastq"
    hda.history = SimpleNamespace(user_id=7, name=HISTORY_NAME)
    hda.dataset = SimpleNamespace(uuid=uuid.UUID("12345678-1234-5678-1234-567812345678"))
    return hda


def make_hdca():
    hdca = MagicMock(spec=model.HistoryDatasetCollectionAssociation)
    hdca.id, hdca.history_id, hdca.hid, hdca.name = 21, 3, 4, "paired reads"
    hdca.history = SimpleNamespace(user_id=7, name=HISTORY_NAME)
    return hdca


def make_invocation():
    invocation = MagicMock(spec=model.WorkflowInvocation)
    invocation.id, invocation.history_id = 13, 3
    invocation.uuid = uuid.UUID("87654321-4321-8765-4321-876543218765")
    invocation.history = SimpleNamespace(user_id=7, name=HISTORY_NAME)
    invocation.workflow = None
    invocation.create_time = datetime(2026, 10, 3)
    return invocation


class FakeTask:
    """Stands in for a Celery task: records what it was asked to do and returns a result id."""

    def __init__(self, task_id):
        self.task_id = task_id
        self.requests: list = []
        self.error: Exception | None = None

    def delay(self, request, task_user_id=None):
        if self.error is not None:
            raise self.error
        self.requests.append((request, task_user_id))
        return SimpleNamespace(id=self.task_id, name="task", queue="celery", ignored=False)


class FakeArchive:
    def response(self):
        yield b"PK archive bytes"

    def get_headers(self):
        return {"Content-Disposition": 'attachment; filename="archive.zip"'}


class Harness:
    def __init__(self, tmp_path, monkeypatch, audit_settings):
        config = SimpleNamespace(audit_log=audit_settings, server_name="main.web.1", galaxy_infrastructure_url=None)
        self.audit = AuditService(config, SECURITY, MagicMock())  # type: ignore[arg-type]
        self.storage = ShortTermStorageManager(ShortTermStorageConfiguration(str(tmp_path / "short_term")))

        self.history = make_history()
        self.hda = make_hda()
        self.hdca = make_hdca()
        self.invocation = make_invocation()

        self.histories = HistoriesService(SECURITY, *(MagicMock() for _ in range(10)))
        self.histories.short_term_storage_allocator = self.storage
        self.history_manager = cast(MagicMock, self.histories.manager)
        self.history_manager.get_accessible.return_value = self.history
        self.history_manager.queue_history_export.return_value = SimpleNamespace(id=55)
        export_manager = cast(MagicMock, self.histories.history_export_manager)
        export_manager.create_export_association.return_value = SimpleNamespace(id=5, task_uuid=None)

        self.contents = HistoriesContentsService(SECURITY, *(MagicMock() for _ in range(16)))
        self.contents.short_term_storage_allocator = self.storage
        cast(MagicMock, self.contents.hda_manager).get_accessible.return_value = self.hda
        self.collection_manager = cast(MagicMock, self.contents.dataset_collection_manager)
        self.collection_manager.get_dataset_collection_instance.return_value = self.hdca
        cast(MagicMock, self.contents.history_manager).get_accessible.return_value = self.history

        self.invocations = InvocationsService(SECURITY, MagicMock(), MagicMock(), self.storage, MagicMock())
        self.workflows_manager = cast(MagicMock, self.invocations._workflows_manager)
        self.workflows_manager.get_invocation.return_value = self.invocation

        self.tasks = {}
        for module, name in (
            (histories_services, "prepare_history_download"),
            (histories_services, "write_history_to"),
            (history_contents_services, "prepare_history_content_download"),
            (history_contents_services, "write_history_content_to"),
            (history_contents_services, "prepare_dataset_collection_download"),
            (invocations_services, "prepare_invocation_download"),
            (invocations_services, "write_invocation_to"),
        ):
            self.tasks[name] = FakeTask(f"task-{name}")
            monkeypatch.setattr(module, name, self.tasks[name])
        monkeypatch.setattr(hdcas, "stream_dataset_collection", lambda **kwargs: FakeArchive())

        registry = {
            HistoriesService: self.histories,
            HistoriesContentsService: self.contents,
            InvocationsService: self.invocations,
            ShortTermStorageManager: self.storage,
            AuditService: self.audit,
        }
        self.sa_session = MagicMock()
        model_mapping = SimpleNamespace(
            set_request_id=lambda request_id: None,
            unset_request_id=lambda request_id: None,
            scoped_registry=SimpleNamespace(registry={}),
            request_scopefunc=lambda: None,
            session=self.sa_session,
        )
        self.app_config = SimpleNamespace(enable_celery_tasks=True, upstream_mod_zip=False, upstream_gzip=False)
        stub_app = SimpleNamespace(
            resolve=lambda dependency: registry.get(dependency) or self._monitor(dependency),
            model=model_mapping,
            install_model=model_mapping,
            config=self.app_config,
        )
        monkeypatch.setattr(galaxy_app, "app", stub_app, raising=False)
        monkeypatch.setattr(IdSecurity, "security", SECURITY, raising=False)

        app = FastAPI()
        add_raw_context_middlewares(app)
        add_exception_handler(app)
        for module in (histories_api, history_contents_api, workflows_api, short_term_storage_api):
            app.include_router(module.router)
        app.dependency_overrides[get_session] = impersonated_session
        app.dependency_overrides[inspect.signature(get_api_user).parameters["user_manager"].default.dependency] = (
            lambda: MagicMock()
        )
        self.client = TestClient(app, cookies={"galaxysession": SESSION_COOKIE})

    def _monitor(self, dependency):
        # The short-term storage route asks for the abstract monitor; everything else unrelated is a stand-in.
        if dependency is ShortTermStorageMonitor:
            return self.storage
        return MagicMock()


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


def encoded(object_id):
    return SECURITY.encode_id(object_id)


HISTORY_URL = f"/api/histories/{encoded(3)}"
DATASET_URL = f"{HISTORY_URL}/contents/datasets/{encoded(42)}"
COLLECTION_URL = f"{HISTORY_URL}/contents/dataset_collections/{encoded(21)}"
INVOCATION_URL = f"/api/invocations/{encoded(13)}"


def outcomes(events):
    return [(event["action"], event["outcome"], event["reason"], event["stage"]) for event in events]


# -- History export ------------------------------------------------------------------


def test_history_download_export_names_who_what_and_the_join_ids(harness, audit_events):
    response = harness.client.post(f"{HISTORY_URL}/prepare_store_download", json={"model_store_format": "tgz"})
    assert response.status_code == 200
    body = response.json()
    (event,) = audit_events
    assert (event["action"], event["outcome"], event["reason"]) == ("history.export", "success", None)
    # The session was created by impersonation, so the admin is the actor.
    assert (event["actor"]["id"], event["effective_user"]["id"]) == (1, 7)
    assert event["auth"]["switch"] == "impersonation"
    assert event["object"]["type"] == "history"
    assert (event["object"]["id"], event["object"]["owner_id"]) == (3, 7)
    assert event["details"] == {
        "destination": "download",
        "format": "tgz",
        "include_files": True,
        "include_hidden": False,
        "include_deleted": False,
        "task_id": "task-prepare_history_download",
        "storage_request_id": body["storage_request_id"],
    }
    assert body["task"]["id"] == event["details"]["task_id"]
    assert HISTORY_NAME not in json.dumps(event)


def test_history_remote_export_records_the_target_without_credentials(harness, audit_events):
    target = f"ftp://alice:{SECRET}@files.example.org/exports/run.tgz?token={SECRET}"
    response = harness.client.post(f"{HISTORY_URL}/write_store", json={"target_uri": target})
    assert response.status_code == 200
    (event,) = audit_events
    assert (event["action"], event["outcome"]) == ("history.export", "success")
    assert event["details"]["destination"] == "remote"
    assert event["details"]["target"] == "ftp://files.example.org/exports/run.tgz"
    assert event["details"]["task_id"] == "task-write_history_to"
    assert "storage_request_id" not in event["details"]
    assert SECRET not in json.dumps(event)
    # The task still gets the URI the user gave; only the audit copy is stripped.
    (request, task_user_id), *_ = harness.tasks["write_history_to"].requests
    assert request.target_uri == target and task_user_id == 7


def test_history_export_denied_names_the_requested_history(harness, audit_events):
    harness.history_manager.get_accessible.side_effect = ItemAccessibilityException("nope")
    response = harness.client.post(f"{HISTORY_URL}/write_store", json={"target_uri": "s3://bucket/key"})
    assert response.status_code == 403
    (event,) = audit_events
    assert outcomes([event]) == [("history.export", "denied", "not_accessible", "authorize")]
    assert (event["object"]["type"], event["object"]["id"], event["object"]["encoded_id"]) == (
        "history",
        3,
        encoded(3),
    )
    assert event["object"]["owner_id"] is None
    assert event["details"]["target"] == "s3://bucket/key"
    assert harness.tasks["write_history_to"].requests == []


@pytest.mark.parametrize("denied", [True, False])
def test_hostile_target_is_reduced_to_its_scheme_whatever_the_outcome(harness, audit_events, denied):
    if denied:
        harness.history_manager.get_accessible.side_effect = ItemAccessibilityException("nope")
    target = f"ftp:/\t/alice:2024/{SECRET}@host//x"
    response = harness.client.post(f"{HISTORY_URL}/write_store", json={"target_uri": target})
    assert response.status_code == (403 if denied else 200)
    (event,) = audit_events
    assert event["outcome"] == ("denied" if denied else "success")
    assert event["details"]["target"] == "ftp:"
    assert SECRET not in json.dumps(event) and "alice" not in json.dumps(event)


def test_history_export_that_cannot_queue_is_one_error(harness, audit_events):
    harness.tasks["prepare_history_download"].error = OSError("broker at amqp://guest:guest@mq down")
    with pytest.raises(OSError):
        harness.client.post(f"{HISTORY_URL}/prepare_store_download", json={})
    (event,) = audit_events
    assert outcomes([event]) == [("history.export", "error", "internal_error", "prepare")]
    assert event["object"]["owner_id"] == 7


def test_export_queued_before_a_failed_commit_is_still_a_success(harness, audit_events):
    harness.sa_session.commit.side_effect = OSError("database went away")
    with pytest.raises(OSError):
        harness.client.post(f"{HISTORY_URL}/write_store", json={"target_uri": "s3://bucket/key"})
    # The task is already running and will write the data, so the event says so and names it.
    (event,) = audit_events
    assert (event["outcome"], event["details"]["task_id"]) == ("success", "task-write_history_to")


def test_disabled_audit_records_nothing_and_changes_nothing(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": False})
    response = harness.client.post(f"{HISTORY_URL}/prepare_store_download", json={})
    assert response.status_code == 200 and response.json()["task"]["id"] == "task-prepare_history_download"
    assert audit_events == []


def test_action_families_can_be_switched_off(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": True, "actions": ["dataset"]})
    harness.client.post(f"{HISTORY_URL}/prepare_store_download", json={})
    harness.client.post(f"{DATASET_URL}/prepare_store_download", json={})
    assert [event["action"] for event in audit_events] == ["dataset.export"]


# -- Legacy job-based history export ---------------------------------------------------


def test_legacy_job_export_to_a_directory_records_the_job(harness, audit_events):
    response = harness.client.put(
        f"{HISTORY_URL}/exports",
        json={"directory_uri": f"gxftp://alice:{SECRET}@MyFTP/exports", "file_name": "run", "gzip": False},
    )
    assert response.status_code == 200
    (event,) = audit_events
    assert (event["action"], event["outcome"]) == ("history.export", "success")
    assert event["details"] == {
        "destination": "remote",
        "format": "tar",
        "target": "gxftp://MyFTP/exports",
        "include_hidden": False,
        "include_deleted": False,
        "job_id": 55,
    }


def test_legacy_export_with_an_up_to_date_archive_records_nothing(harness, audit_events):
    harness.history.latest_export = SimpleNamespace(up_to_date=True, ready=False, job=SimpleNamespace(id=54))
    cast(MagicMock, harness.histories.history_export_manager).serialize.return_value = {
        "id": encoded(8),
        "job_id": encoded(54),
        "ready": False,
        "preparing": True,
        "up_to_date": True,
        "download_url": "/download",
        "external_download_latest_url": "/latest",
        "external_download_permanent_url": "/permanent",
    }
    response = harness.client.put(f"{HISTORY_URL}/exports", json={})
    assert response.status_code == 202
    harness.history_manager.queue_history_export.assert_not_called()
    assert audit_events == []


def test_legacy_archive_download_is_one_content_event(harness, audit_events, tmp_path):
    archive = tmp_path / "export.tgz"
    archive.write_bytes(b"tarball")
    jeha = MagicMock(spec=model.JobExportHistoryArchive)
    jeha.id, jeha.history_id, jeha.dataset_id, jeha.compressed, jeha.export_name = 8, 3, 30, True, "export.tgz"
    jeha.history = SimpleNamespace(user_id=7, name=HISTORY_NAME)
    export_manager = cast(MagicMock, harness.histories.history_export_manager)
    export_manager.get_ready_jeha.return_value = jeha
    harness.history_manager.get_ready_history_export_file_path.return_value = str(archive)
    response = harness.client.get(f"{HISTORY_URL}/exports/{encoded(8)}")
    assert response.status_code == 200 and response.content == b"tarball"
    (event,) = audit_events
    assert outcomes([event]) == [("archive.download", "success", None, "respond")]
    assert event["object"]["type"] == "history_export"
    assert (event["object"]["id"], event["object"]["history_id"], event["object"]["owner_id"]) == (8, 3, 7)
    assert event["details"] == {"source": "job_export"}


def test_legacy_archive_not_ready_is_an_error(harness, audit_events):
    export_manager = cast(MagicMock, harness.histories.history_export_manager)
    export_manager.get_ready_jeha.side_effect = RequestParameterInvalidException("not ready")
    response = harness.client.get(f"{HISTORY_URL}/exports/latest")
    assert response.status_code == 400
    (event,) = audit_events
    assert outcomes([event]) == [("archive.download", "error", "invalid_request", "authorize")]
    assert (event["object"]["type"], event["object"]["id"], event["object"]["history_id"]) == (
        "history_export",
        None,
        3,
    )


# -- History contents export -----------------------------------------------------------


def test_dataset_export_is_a_dataset_export(harness, audit_events):
    response = harness.client.post(f"{DATASET_URL}/prepare_store_download", json={"include_files": False})
    assert response.status_code == 200
    (event,) = audit_events
    assert (event["action"], event["outcome"]) == ("dataset.export", "success")
    assert (event["object"]["type"], event["object"]["id"]) == ("hda", 42)
    assert event["object"]["uuid"] == "12345678-1234-5678-1234-567812345678"
    assert event["details"]["include_files"] is False
    assert event["details"]["storage_request_id"] == response.json()["storage_request_id"]


def test_collection_remote_export_is_a_collection_export(harness, audit_events):
    # The service builds its task request with a misspelled field (contents_type for content_type),
    # so this route fails before queueing anything, for datasets and collections alike; the attempt
    # is still on record, as an error.
    with pytest.raises(ValidationError):
        harness.client.post(f"{COLLECTION_URL}/write_store", json={"target_uri": "s3://bucket/reads.tgz"})
    (event,) = audit_events
    assert outcomes([event]) == [("collection.export", "error", "internal_error", "prepare")]
    assert (event["object"]["type"], event["object"]["id"], event["object"]["owner_id"]) == ("hdca", 21, 7)
    assert (event["details"]["destination"], event["details"]["target"]) == ("remote", "s3://bucket/reads.tgz")


def test_content_export_denied(harness, audit_events):
    cast(MagicMock, harness.contents.hda_manager).get_accessible.side_effect = ItemAccessibilityException("nope")
    response = harness.client.post(f"{DATASET_URL}/write_store", json={"target_uri": "s3://bucket/key"})
    assert response.status_code == 403
    assert outcomes(audit_events) == [("dataset.export", "denied", "not_accessible", "authorize")]
    assert audit_events[0]["object"]["id"] == 42


def test_unnamed_content_export_is_an_invalid_request(harness, audit_events):
    harness.hda.name = None
    response = harness.client.post(f"{DATASET_URL}/prepare_store_download", json={})
    assert response.status_code == 400
    assert outcomes(audit_events) == [("dataset.export", "error", "invalid_request", "prepare")]


# -- Collection archives ---------------------------------------------------------------


@pytest.mark.parametrize("url", [f"{COLLECTION_URL}/download", f"/api/dataset_collections/{encoded(21)}/download"])
def test_collection_zip_is_one_download_event(harness, audit_events, url):
    response = harness.client.get(url)
    assert response.status_code == 200 and response.content == b"PK archive bytes"
    (event,) = audit_events
    assert outcomes([event]) == [("collection.download", "success", None, "respond")]
    assert (event["object"]["type"], event["object"]["id"], event["object"]["history_id"]) == ("hdca", 21, 3)


def test_collection_zip_denied(harness, audit_events):
    harness.collection_manager.get_dataset_collection_instance.side_effect = ItemAccessibilityException("nope")
    response = harness.client.get(f"{COLLECTION_URL}/download")
    assert response.status_code == 403
    assert outcomes(audit_events) == [("collection.download", "denied", "not_accessible", "authorize")]
    assert audit_events[0]["object"]["id"] == 21


def test_async_collection_zip_is_a_collection_export(harness, audit_events):
    response = harness.client.post(f"/api/dataset_collections/{encoded(21)}/prepare_download")
    assert response.status_code == 200
    (event,) = audit_events
    assert (event["action"], event["outcome"]) == ("collection.export", "success")
    assert event["details"] == {
        "destination": "download",
        "format": "zip",
        "task_id": "task-prepare_dataset_collection_download",
        "storage_request_id": response.json()["storage_request_id"],
    }


def test_async_collection_zip_without_celery_is_an_error(harness, audit_events):
    harness.app_config.enable_celery_tasks = False
    response = harness.client.post(f"/api/dataset_collections/{encoded(21)}/prepare_download")
    assert response.status_code == 403
    assert outcomes(audit_events) == [("collection.export", "error", "invalid_request", "authorize")]


def test_history_contents_archive_is_one_download_event(harness, audit_events):
    # The unnamed /contents/archive route is shadowed by /contents/{id}; the named one is reachable.
    response = harness.client.get(f"{HISTORY_URL}/contents/archive/{SECRET}.zip?dry_run=false")
    assert response.status_code == 200
    (event,) = audit_events
    assert outcomes([event]) == [("history.download", "success", None, "respond")]
    assert (event["object"]["type"], event["object"]["id"]) == ("history", 3)
    # The archive name is user chosen, so it is a name and left out by default.
    assert event["details"] == {}


def test_history_contents_archive_dry_run_records_nothing(harness, audit_events):
    response = harness.client.get(f"{HISTORY_URL}/contents/archive/run.zip")
    assert response.status_code == 200
    assert audit_events == []


def test_history_contents_archive_names_with_include_names(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": True, "include_names": True})
    harness.client.get(f"{HISTORY_URL}/contents/archive/run.zip?dry_run=false")
    (event,) = audit_events
    assert event["details"] == {"filename": "run"}
    assert event["object"]["history_name"] == HISTORY_NAME


# -- Invocation export -----------------------------------------------------------------


def test_invocation_export(harness, audit_events):
    response = harness.client.post(
        f"{INVOCATION_URL}/prepare_store_download", json={"model_store_format": "rocrate.zip"}
    )
    assert response.status_code == 200
    (event,) = audit_events
    assert (event["action"], event["outcome"]) == ("invocation.export", "success")
    assert (event["object"]["type"], event["object"]["id"], event["object"]["owner_id"]) == ("invocation", 13, 7)
    assert event["object"]["uuid"] == "87654321-4321-8765-4321-876543218765"
    assert event["details"]["format"] == "rocrate.zip"
    assert event["details"]["task_id"] == "task-prepare_invocation_download"


def test_invocation_remote_export_denied(harness, audit_events):
    harness.workflows_manager.get_invocation.side_effect = ItemAccessibilityException("nope")
    response = harness.client.post(f"{INVOCATION_URL}/write_store", json={"target_uri": "s3://bucket/inv.tgz"})
    assert response.status_code == 403
    assert outcomes(audit_events) == [("invocation.export", "denied", "not_accessible", "authorize")]
    assert audit_events[0]["object"]["id"] == 13


# -- Prepared downloads ----------------------------------------------------------------


def test_prepared_download_joins_the_export_that_asked_for_it(harness, audit_events):
    prepared = harness.client.post(f"{HISTORY_URL}/prepare_store_download", json={}).json()
    storage_request_id = prepared["storage_request_id"]
    # What the task does once the archive is built.
    target = harness.storage.recover_target(uuid.UUID(storage_request_id))
    target.path.write_bytes(b"history archive")
    harness.storage.finalize(target)

    response = harness.client.get(f"/api/short_term_storage/{storage_request_id}")
    assert response.status_code == 200 and response.content == b"history archive"
    export_event, download_event = audit_events
    assert outcomes([download_event]) == [("archive.download", "success", None, "respond")]
    assert download_event["object"]["type"] == "short_term_storage"
    assert download_event["object"]["uuid"] == storage_request_id
    assert download_event["details"] == {"source": "short_term_storage", "storage_request_id": storage_request_id}
    assert export_event["details"]["storage_request_id"] == download_event["details"]["storage_request_id"]


def test_prepared_download_that_failed_is_an_archive_failure(harness, audit_events):
    target = harness.storage.new_target("history.tgz", "application/x-gzip")
    harness.storage.cancel(target, exception=RequestParameterInvalidException("bad export"))
    response = harness.client.get(f"/api/short_term_storage/{target.request_id}")
    assert response.status_code == 400
    assert outcomes(audit_events) == [("archive.download", "error", "archive_failed", "prepare")]


def test_unknown_prepared_download_is_not_found(harness, audit_events):
    response = harness.client.get(f"/api/short_term_storage/{uuid.uuid4()}")
    assert response.status_code == 404
    assert outcomes(audit_events) == [("archive.download", "error", "not_found", "authorize")]


def test_prepared_download_not_yet_written_is_an_error_at_respond(harness, audit_events):
    target = harness.storage.new_target("history.tgz", "application/x-gzip")
    with pytest.raises(RuntimeError):
        harness.client.get(f"/api/short_term_storage/{target.request_id}")
    assert outcomes(audit_events) == [("archive.download", "error", "internal_error", "respond")]


# -- The pieces on their own -----------------------------------------------------------


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("s3://bucket/key.tgz", "s3://bucket/key.tgz"),
        (f"https://alice:{SECRET}@host.example.org:8443/p/x?token={SECRET}#frag", "https://host.example.org:8443/p/x"),
        ("gxuserfiles://MyS3/exports/f.tgz", "gxuserfiles://MyS3/exports/f.tgz"),
        (f"ftp://alice:pa/{SECRET}@host/x", "ftp:"),
        (f"ftp://alice:2024/{SECRET}@host/x", "ftp:"),
        (f"ftp://alice:1234?{SECRET}@host/x", "ftp:"),
        (f"ftp://alice:99#{SECRET}@host/", "ftp:"),
        (f"https://{SECRET}/x@host/", "https:"),
        (f"http://host/p;jsessionid={SECRET}", "http://host/p"),
        (f"alice:{SECRET}@host/path", None),
        ("ftp://[::1]:21/x", "ftp://[::1]:21/x"),
        ("http://[broken/x", "http:"),
        ("http://[::1]x/y", "http:"),
        (None, None),
        # urlsplit() drops tabs and newlines, so it finds an authority that isn't in the original text.
        (f"ftp:/\t/alice:2024/{SECRET}@host//x", "ftp:"),
        ("ftp:/\n/host/path", "ftp:"),
        (f"ftp://alice:{SECRET}\t@host/x", "ftp:"),
        (f"alice:{SECRET}\n@host/x", None),
        (f"ftp://alice\\{SECRET}@host/x", "ftp:"),
        (f"ftp://alice:{SECRET}\uff20host/x", "ftp:"),
        (f"ftp://alice:{SECRET}\u200b@host/x", "ftp:"),
        ("FTP://Host/Dir", "ftp://Host/Dir"),
        ("not a uri", None),
        ("", None),
    ],
)
def test_target_uri_never_keeps_credentials(uri, expected):
    assert sanitize_target_uri(uri) == expected
    assert sanitize_target_uri(expected) == expected


PASSWORD = "Zq9Xw7Kv"
HOSTILE_CHARACTERS = ["/", "?", "#", "@", ":", ";", "[", "]", "\\", "%", " ", "\t", "\n", "\r", "\x00", "\x7f"] + [
    "\u00a0",  # no-break space
    "\u200b",  # zero-width space
    "\u202e",  # right-to-left override
    "\uff0f",  # fullwidth solidus
    "\uff20",  # fullwidth commercial at
    "\uff1a",  # fullwidth colon
]
CREDENTIALED_URIS = [
    f"ftp://alice:{PASSWORD}@files.example.org:21/dir/file?token={PASSWORD}#{PASSWORD}",
    f"https://{PASSWORD}@files.example.org/dir/file",
    f"gxftp://alice:{PASSWORD}@MyFTP/dir/file",
]


def assert_no_credentials(sanitized):
    if sanitized is None:
        return
    assert "@" not in sanitized
    assert "alice" not in sanitized
    assert not any(PASSWORD[i : i + 4] in sanitized for i in range(len(PASSWORD) - 3))
    assert not any(char.isspace() or not char.isprintable() for char in sanitized)
    assert sanitize_target_uri(sanitized) == sanitized


@pytest.mark.parametrize("uri", CREDENTIALED_URIS)
def test_target_uri_with_a_hostile_character_anywhere_keeps_no_credentials(uri):
    for position in range(len(uri) + 1):
        for char in HOSTILE_CHARACTERS:
            assert_no_credentials(sanitize_target_uri(uri[:position] + char + uri[position:]))


def test_target_uri_sanitizer_never_raises_or_lets_credentials_through():
    rng = random.Random(20261003)
    alphabet = list("ftp:/@?#;[]%.") + HOSTILE_CHARACTERS + ["alice", PASSWORD, "host", "21", "//"]
    for _ in range(5000):
        uri = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        sanitized = sanitize_target_uri(uri)
        if sanitized is not None:
            assert "@" not in sanitized
            assert not any(char.isspace() or not char.isprintable() for char in sanitized)


def test_details_strip_credentials_whoever_builds_them():
    details = ExportDetails(destination="remote", target=f"s3://key:{SECRET}@bucket/x")
    assert details.target == "s3://bucket/x"


def test_export_audit_settles_once(harness, audit_events):
    export = ExportAudit(
        harness.audit, "history.export", AuditObject(type="history", id=3), ExportDetails(destination="download")
    )
    with export.guard():
        export.queued(task_id="a")
        export.queued(task_id="b")
    assert [event["details"]["task_id"] for event in audit_events] == ["a"]


def test_export_audit_that_never_says_how_it_ended_is_an_error(harness, audit_events):
    export = ExportAudit(
        harness.audit, "history.export", AuditObject(type="history", id=3), ExportDetails(destination="download")
    )
    with export.guard():
        pass
    assert outcomes(audit_events) == [("history.export", "error", "internal_error", "authorize")]
