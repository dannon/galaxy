"""Audit events from the legacy library dataset download controller.

The controller method is real; its model lookups and the app around it are stand-ins.
"""

import json
import logging
import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from galaxy import (
    exceptions,
    model,
)
from galaxy.managers.audit import (
    AUDIT_LOGGER_NAME,
    AuditService,
)
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.web.framework.request_scope import (
    request_scope,
    RequestIdentity,
)
from galaxy.webapps.galaxy.api.library_datasets import LibraryDatasetsController

SECURITY = IdEncodingHelper(id_secret="library-audit-test")


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


def make_library_dataset(tmp_path, ld_id, ldda_id, name):
    path = tmp_path / f"dataset_{ldda_id}.dat"
    path.write_bytes(f"content of {name}".encode())
    root_folder = SimpleNamespace(parent=None, library_root=[SimpleNamespace(name="Shared")])
    ldda = MagicMock(spec=model.LibraryDatasetDatasetAssociation)
    ldda.id, ldda.dataset_id, ldda.name, ldda.extension, ldda.user_id = ldda_id, ldda_id + 100, name, "txt", 7
    ldda.dataset = MagicMock()
    ldda.dataset.uuid = uuid.UUID(int=ldda_id)
    ldda.dataset.get_file_name.return_value = str(path)
    ldda.datatype.composite_type = None
    ldda.get_mime.return_value = "text/plain"
    ldda.library_dataset = SimpleNamespace(folder=root_folder)
    return SimpleNamespace(id=ld_id, library_dataset_dataset_association=ldda, deleted=False)


class Harness:
    def __init__(self, tmp_path, audit_settings):
        config = SimpleNamespace(audit_log=audit_settings, server_name="main.web.1", galaxy_infrastructure_url=None)
        self.audit = AuditService(config, SECURITY, MagicMock())  # type: ignore[arg-type]
        self.datasets = {
            SECURITY.encode_id(1): make_library_dataset(tmp_path, 1, 11, "first"),
            SECURITY.encode_id(2): make_library_dataset(tmp_path, 2, 12, "second"),
        }
        self.denied: set[str] = set()
        app = MagicMock()
        app.__getitem__.side_effect = {AuditService: self.audit}.__getitem__
        app.config.upstream_mod_zip = False
        app.config.upstream_gzip = False
        app.security = SECURITY
        # Bypass __init__: it builds managers this test doesn't need.
        controller = LibraryDatasetsController.__new__(LibraryDatasetsController)
        controller.app = app
        controller.folder_manager = MagicMock()
        controller.get_library_dataset = self.get_library_dataset  # type: ignore[method-assign]
        self.controller = controller
        self.trans = SimpleNamespace(
            response=SimpleNamespace(headers={}, set_content_type=lambda content_type: None),
            user_is_admin=True,
            get_current_user_roles=lambda: [],
        )

    def get_library_dataset(self, trans, id, check_ownership=False, check_accessible=True):
        if id in self.denied:
            raise exceptions.ItemAccessibilityException("LibraryDataset is not accessible to the current user")
        try:
            return self.datasets[id]
        except KeyError:
            raise exceptions.MessageException(f"Invalid LibraryDataset id ( {id} ) specified")

    def download(self, archive_format, **kwd):
        with request_scope(request_id="req-1") as scope:
            scope.identity = RequestIdentity("session", user_id=7, actor_id=7, credential_id=5)
            response = self.controller.download(self.trans, archive_format, **kwd)
            if archive_format == "zip":
                b"".join(response)
            return response


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path, {"enabled": True})


def summary(events):
    return [(e["action"], e["outcome"], e["reason"], e["object"] and e["object"]["type"]) for e in events]


def test_zip_of_two_datasets_is_one_event_each(harness, audit_events):
    ids = [SECURITY.encode_id(1), SECURITY.encode_id(2)]
    harness.download("zip", ld_ids=ids)
    assert summary(audit_events) == [("library_dataset.download", "success", None, "ldda")] * 2
    assert [e["object"]["id"] for e in audit_events] == [11, 12]
    assert [e["details"]["library_dataset_id"] for e in audit_events] == [1, 2]
    assert audit_events[0]["details"]["archive_format"] == "zip"
    assert audit_events[0]["request_id"] == audit_events[1]["request_id"] == "req-1"
    assert audit_events[0]["effective_user"]["id"] == 7
    assert "first" not in json.dumps(audit_events)


def test_uncompressed_single_dataset(harness, audit_events):
    response = harness.download("uncompressed", ld_ids=SECURITY.encode_id(1))
    assert response.read() == b"content of first"
    response.close()
    assert summary(audit_events) == [("library_dataset.download", "success", None, "ldda")]


def test_denied_dataset_is_recorded_as_a_denial_even_though_the_client_sees_a_500(harness, audit_events):
    harness.denied.add(SECURITY.encode_id(2))
    with pytest.raises(exceptions.InternalServerError):
        harness.download("zip", ld_ids=[SECURITY.encode_id(1), SECURITY.encode_id(2)])
    assert [(e["outcome"], e["reason"], e["stage"]) for e in audit_events] == [
        ("denied", "not_accessible", "authorize"),
        # The first was never sent: the request failed before any archive went out.
        ("error", "internal_error", "prepare"),
    ]
    denied = audit_events[0]
    assert (denied["object"]["type"], denied["object"]["id"]) == ("library_dataset", 2)


def test_two_datasets_uncompressed_is_an_invalid_request_for_both(harness, audit_events):
    with pytest.raises(exceptions.RequestParameterInvalidException):
        harness.download("uncompressed", ld_ids=[SECURITY.encode_id(1), SECURITY.encode_id(2)])
    assert [(e["outcome"], e["reason"]) for e in audit_events] == [("error", "invalid_request")] * 2


def test_folder_download_names_each_dataset_and_the_folder(harness, audit_events):
    folder = SimpleNamespace(active_folders=[], datasets=list(harness.datasets.values()))
    folder_manager = cast(MagicMock, harness.controller.folder_manager)
    folder_manager.cut_and_decode.return_value = 30
    folder_manager.get.return_value = folder
    harness.download("zip", folder_ids="F" + SECURITY.encode_id(30))
    assert summary(audit_events) == [("library_dataset.download", "success", None, "ldda")] * 2
    assert [e["details"] for e in audit_events] == [
        {"archive_format": "zip", "library_dataset_id": 1, "folder_id": 30},
        {"archive_format": "zip", "library_dataset_id": 2, "folder_id": 30},
    ]


def test_malformed_id_still_gets_an_event(harness, audit_events):
    with pytest.raises(exceptions.InternalServerError):
        harness.download("zip", ld_ids="not-an-id")
    (event,) = audit_events
    assert (event["outcome"], event["object"]) == ("error", {**event["object"], "type": "library_dataset", "id": None})


def test_disabled_audit_records_nothing(tmp_path, audit_events):
    harness = Harness(tmp_path, {"enabled": False})
    harness.download("zip", ld_ids=[SECURITY.encode_id(1)])
    assert audit_events == []


@pytest.mark.parametrize("enabled", [True, False])
def test_an_attempts_query_parameter_is_ignored_as_before(tmp_path, audit_events, enabled):
    harness = Harness(tmp_path, {"enabled": enabled})
    response = harness.download("uncompressed", ld_ids=SECURITY.encode_id(1), attempts="x")
    assert response.read() == b"content of first"
    response.close()
    assert len(audit_events) == (1 if enabled else 0)


def test_disabled_audit_does_no_work_per_dataset(tmp_path, audit_events, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("audit work with auditing off")

    module = "galaxy.webapps.galaxy.api.library_datasets"
    monkeypatch.setattr(f"{module}.begin_audit_attempt", unexpected)
    monkeypatch.setattr(f"{module}.LibraryDownloadDetails", unexpected)
    monkeypatch.setattr(LibraryDatasetsController, "_decode_for_audit", unexpected)
    harness = Harness(tmp_path, {"enabled": False})
    folder = SimpleNamespace(active_folders=[], datasets=list(harness.datasets.values()))
    folder_manager = cast(MagicMock, harness.controller.folder_manager)
    folder_manager.cut_and_decode.return_value = 30
    folder_manager.get.return_value = folder
    harness.download("zip", ld_ids=[SECURITY.encode_id(1)], folder_ids="F" + SECURITY.encode_id(30))
    assert audit_events == []
