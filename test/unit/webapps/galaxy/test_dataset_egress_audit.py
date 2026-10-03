"""Audit events from the routes besides /display that hand out dataset content.

Each test drives a real FastAPI app: the routes, the auth dependencies,
DatasetsService and Galaxy's response classes are real; the managers behind the
service and the app container are stand-ins.
"""

import inspect
import json
import logging
import time
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
from sqlalchemy.orm import make_transient_to_detached

from galaxy import (
    app as galaxy_app,
    model,
)
from galaxy.config import GalaxyAppConfiguration
from galaxy.datatypes.binary import Binary
from galaxy.exceptions import (
    ItemAccessibilityException,
    ObjectNotFound,
    RequestParameterInvalidException,
)
from galaxy.managers.audit import (
    AUDIT_LOGGER_NAME,
    AuditService,
)
from galaxy.schema.fields import Security as IdSecurity
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.webapps.base.api import (
    add_exception_handler,
    add_raw_context_middlewares,
)
from galaxy.webapps.galaxy.api import (
    get_api_user,
    get_session,
)
from galaxy.webapps.galaxy.api.datasets import router as datasets_router
from galaxy.webapps.galaxy.api.drs import router as drs_router
from galaxy.webapps.galaxy.services.datasets import (
    DatasetsService,
    signed_url_facts,
)

SERVICE_MODULE = "galaxy.webapps.galaxy.services.datasets"
SECURITY = IdEncodingHelper(id_secret="egress-audit-test")
SESSION_COOKIE = "secret-session-cookie-0123456789"
CONTENT = b"0123456789"
SIGNATURE = "c2VjcmV0LXNpZ25hdHVyZQ"
PRESIGNED = (
    "https://bucket.s3.example.org/000/dataset_11.dat?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    f"&X-Amz-Credential=AKIDEXAMPLE&X-Amz-Expires=3600&X-Amz-Signature={SIGNATURE}"
)


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


ALICE = detached(model.User(email="alice@example.org"), 7)
ADMIN = detached(model.User(email="admin@example.org"), 1)


def plain_session(galaxysession: str = Depends(APIKeyCookie(name="galaxysession", auto_error=False))):
    if galaxysession != SESSION_COOKIE:
        return None
    return detached(model.GalaxySession(user=ALICE, impersonated_by_user_id=None, current_history=None), 99)


def impersonated_session(galaxysession: str = Depends(APIKeyCookie(name="galaxysession", auto_error=False))):
    if galaxysession != SESSION_COOKIE:
        return None
    session = model.GalaxySession(user=ALICE, impersonated_by_user_id=ADMIN.id, current_history=None)
    return detached(session, 99)


def make_hda(tmp_path, datatype=None):
    path = tmp_path / "dataset_11.dat"
    path.write_bytes(CONTENT)
    extra_files = tmp_path / "dataset_11_files"
    extra_files.mkdir()
    (extra_files / "index.html").write_bytes(b"<html/>")
    metadata = tmp_path / "metadata_5.dat"
    metadata.write_bytes(b"index-bytes")
    hda = MagicMock(spec=model.HistoryDatasetAssociation)
    hda.id, hda.dataset_id, hda.history_id, hda.name, hda.hid = 42, 11, 3, "reads.bam", 1
    hda.history = SimpleNamespace(user_id=7, name="Alice's history")
    hda.dataset = SimpleNamespace(
        id=11,
        uuid=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        object_store_id="s3_store",
        extra_files_path_name="dataset_11_files",
        hashes=[SimpleNamespace(extra_files_path=None, hash_function="MD5", hash_value="abc")],
        file_size=len(CONTENT),
    )
    hda.extension = "bam"
    hda.is_pending = False
    hda.extra_files_path = str(extra_files)
    hda.extra_files_path_exists.return_value = True
    hda.get_file_name.return_value = str(path)
    hda.create_time = datetime(2026, 1, 2, 3, 4, 5)
    if datatype is not None:
        hda.datatype = datatype
    hda.datatype.is_archive_download.return_value = False
    hda.datatype.content_disposition.return_value = 'attachment; filename="reads.bam"'
    hda.datatype.display_data.side_effect = lambda *args, **kwargs: (open(path, "rb"), {})
    hda.metadata.spec.get.return_value = {"file_ext": "bai"}
    hda.metadata.get.return_value = SimpleNamespace(get_file_name=lambda auth=None: str(metadata))
    return hda


class Harness:
    def __init__(self, tmp_path, monkeypatch, audit_settings, session=plain_session, datatype=None):
        self.hda = make_hda(tmp_path, datatype)
        config = SimpleNamespace(audit_log=audit_settings, server_name="main.web.1", galaxy_infrastructure_url=None)
        self.audit = AuditService(config, SECURITY, MagicMock())  # type: ignore[arg-type]
        self.service = DatasetsService(SECURITY, *(MagicMock() for _ in range(9)))
        self.hda_manager = cast(MagicMock, self.service.hda_manager)
        self.hda_manager.get_accessible.return_value = self.hda
        self.hda_manager.error_if_uploading.side_effect = lambda hda: hda
        self.hda_manager.text_data.return_value = (False, "0123456789")
        registry = {DatasetsService: self.service, AuditService: self.audit, GalaxyAppConfiguration: MagicMock()}
        model_mapping = SimpleNamespace(
            set_request_id=lambda request_id: None,
            unset_request_id=lambda request_id: None,
            scoped_registry=SimpleNamespace(registry={}),
            request_scopefunc=lambda: None,
        )
        self.stub_app = SimpleNamespace(
            resolve=registry.__getitem__,
            model=model_mapping,
            install_model=model_mapping,
            config=SimpleNamespace(),
            object_store=MagicMock(),
            datatypes_registry=MagicMock(),
            security_agent=MagicMock(),
            security=SECURITY,
        )
        self.stub_app.object_store.get_data_stream.return_value = None
        self.stub_app.object_store.get_direct_download_url.return_value = None
        self.stub_app.object_store.size.return_value = len(CONTENT)
        self.stub_app.object_store.get_filename.return_value = str(tmp_path / "dataset_11_files" / "index.html")
        monkeypatch.setattr(galaxy_app, "app", self.stub_app, raising=False)
        monkeypatch.setattr(IdSecurity, "security", SECURITY, raising=False)
        # Builds a link to the legacy dataset controller, which this app doesn't mount.
        monkeypatch.setattr(f"{SERVICE_MODULE}.web.url_for", lambda **kwargs: "/datasets/x", raising=False)

        app = FastAPI()
        add_raw_context_middlewares(app)
        add_exception_handler(app)
        app.include_router(datasets_router)
        app.include_router(drs_router)
        app.dependency_overrides[get_session] = session
        app.dependency_overrides[inspect.signature(get_api_user).parameters["user_manager"].default.dependency] = (
            lambda: MagicMock()
        )
        self.client = TestClient(app, cookies={"galaxysession": SESSION_COOKIE})

    def deny(self):
        self.hda_manager.get_accessible.side_effect = ItemAccessibilityException("nope")


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


ENCODED = SECURITY.encode_id(42)


def outcomes(events):
    return [(event["action"], event["outcome"], event["reason"], event["stage"]) for event in events]


def assert_denied_names_request(event, action, object_type="hda", object_id=42):
    assert (event["action"], event["outcome"], event["reason"], event["stage"]) == (
        action,
        "denied",
        "not_accessible",
        "authorize",
    )
    assert (event["object"]["type"], event["object"]["id"]) == (object_type, object_id)
    assert event["object"]["uuid"] is None


# -- Presigned / direct object-store downloads ------------------------------------


def test_presigned_redirect_records_the_hand_off_without_the_url(harness, audit_events):
    harness.stub_app.object_store.get_direct_download_url.return_value = PRESIGNED
    response = harness.client.get(f"/api/datasets/{ENCODED}/download", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == PRESIGNED
    (event,) = audit_events
    assert outcomes([event]) == [("dataset.download_url", "success", None, "respond")]
    assert event["details"] == {
        "to_ext": "data",
        "object_store_id": "s3_store",
        "url_host": "bucket.s3.example.org",
        "expires_in": 3600,
    }
    assert event["object"]["uuid"] == "12345678-1234-5678-1234-567812345678"
    serialized = json.dumps(event)
    assert SIGNATURE not in serialized and "AKIDEXAMPLE" not in serialized and "dataset_11.dat" not in serialized


def test_history_contents_presigned_route_is_recorded_too(harness, audit_events):
    harness.stub_app.object_store.get_direct_download_url.return_value = PRESIGNED
    harness.client.get(f"/api/histories/{SECURITY.encode_id(3)}/contents/{ENCODED}/download", follow_redirects=False)
    assert [event["action"] for event in audit_events] == ["dataset.download_url"]


def test_redirect_to_display_leaves_the_event_to_display(harness, audit_events):
    response = harness.client.get(f"/api/datasets/{ENCODED}/download", follow_redirects=False)
    assert response.status_code == 302 and "/display" in response.headers["location"]
    assert audit_events == []
    # Following it, the streamed download is the one event.
    harness.client.get(f"/api/datasets/{ENCODED}/download")
    assert outcomes(audit_events) == [("dataset.download", "success", None, "respond")]


def test_denied_download_url_names_the_requested_dataset(harness, audit_events):
    harness.deny()
    response = harness.client.get(f"/api/datasets/{ENCODED}/download", follow_redirects=False)
    assert response.status_code == 403
    (event,) = audit_events
    assert_denied_names_request(event, "dataset.download_url")


def test_download_url_failure_after_access_is_an_error(harness, audit_events):
    harness.hda_manager.ensure_dataset_on_disk.side_effect = ObjectNotFound("gone")
    harness.client.get(f"/api/datasets/{ENCODED}/download", follow_redirects=False)
    assert outcomes(audit_events) == [("dataset.download_url", "error", "not_found", "prepare")]


def test_download_head_records_only_refusals(harness, audit_events):
    assert harness.client.head(f"/api/datasets/{ENCODED}/download").status_code == 200
    assert audit_events == []
    harness.deny()
    harness.client.head(f"/api/datasets/{ENCODED}/download")
    (event,) = audit_events
    assert_denied_names_request(event, "dataset.download")


def test_signed_url_facts_never_need_the_signature():
    assert signed_url_facts(PRESIGNED) == ("bucket.s3.example.org", 3600)
    expires = int(time.time()) + 600
    host, expires_in = signed_url_facts(f"https://store.example.org/k?Expires={expires}&Signature=x")
    assert host == "store.example.org" and expires_in is not None and 590 <= expires_in <= 600
    host, expires_in = signed_url_facts("https://acct.blob.example.net/c/b?se=2000-01-01T00:00:00Z&sig=x")
    assert expires_in is not None and expires_in < 0
    assert signed_url_facts("https://plain.example.org/file") == ("plain.example.org", None)
    assert signed_url_facts("https://h.example.org/f?X-Amz-Expires=soon") == ("h.example.org", None)


# -- Text, previews and datatype reads --------------------------------------------


def test_content_as_text_success(harness, audit_events):
    response = harness.client.get(f"/api/datasets/{ENCODED}/get_content_as_text")
    assert response.status_code == 200 and response.json()["item_data"] == "0123456789"
    (event,) = audit_events
    assert outcomes([event]) == [("dataset.read_text", "success", None, "prepare")]
    assert event["object"]["dataset_id"] == 11


def test_content_as_text_extra_file_name_stays_out_by_default(harness, audit_events):
    harness.hda_manager.text_data_truncated.return_value = (False, "<html/>")
    harness.client.get(f"/api/datasets/{ENCODED}/get_content_as_text?filename=secret_name.html")
    (event,) = audit_events
    assert event["outcome"] == "success" and "secret_name" not in json.dumps(event)


def test_content_as_text_denied(harness, audit_events):
    harness.deny()
    assert harness.client.get(f"/api/datasets/{ENCODED}/get_content_as_text").status_code == 403
    (event,) = audit_events
    assert_denied_names_request(event, "dataset.read_text")


def test_tool_report_reads_the_dataset(harness, audit_events, monkeypatch):
    harness.stub_app.object_store.get_filename.return_value = str(harness.hda.get_file_name())
    monkeypatch.setattr(f"{SERVICE_MODULE}.resolve_job_markdown", lambda trans, job, content: content)
    monkeypatch.setattr(f"{SERVICE_MODULE}.ready_galaxy_markdown_for_export", lambda trans, md: (md, md, {}))
    response = harness.client.get(f"/api/datasets/{ENCODED}/report")
    assert response.status_code == 200 and response.json()["content"] == "0123456789"
    harness.deny()
    harness.client.get(f"/api/datasets/{ENCODED}/report")
    assert outcomes(audit_events) == [
        ("dataset.read_text", "success", None, "prepare"),
        ("dataset.read_text", "denied", "not_accessible", "authorize"),
    ]


def test_raw_data_for_a_visualization_is_a_read(harness, audit_events, monkeypatch):
    monkeypatch.setattr(harness.service, "_raw_data", lambda trans, dataset, **kwargs: {"data": []})
    response = harness.client.get(f"/api/datasets/{ENCODED}?data_type=raw_data")
    assert response.status_code == 200
    (event,) = audit_events
    assert outcomes([event]) == [("dataset.read_data", "success", None, "prepare")]
    assert event["details"] == {"data_type": "raw_data"}


def test_raw_data_ignores_an_audit_attempt_query_parameter(harness, audit_events, monkeypatch):
    monkeypatch.setattr(harness.service, "_raw_data", lambda trans, dataset, **kwargs: {"data": []})
    assert harness.client.get(f"/api/datasets/{ENCODED}?data_type=raw_data&audit_attempt=x").status_code == 200
    assert [event["outcome"] for event in audit_events] == ["success"]


def test_include_names_adds_the_extra_file_name(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": True, "include_names": True})
    harness.client.get(f"/api/datasets/{ENCODED}/extra_files/raw/index.html")
    (event,) = audit_events
    assert event["details"] == {"raw": True, "filename": "index.html"}
    assert event["object"]["name"] == "reads.bam"


def test_unparseable_presigned_url_gives_no_facts_rather_than_failing():
    assert signed_url_facts("https://[::1/x?X-Amz-Expires=60&X-Amz-Signature=abc") == (None, None)


def test_dataset_state_is_not_a_read(harness, audit_events):
    harness.hda.dataset.state = "ok"
    harness.client.get(f"/api/datasets/{ENCODED}?data_type=state")
    assert audit_events == []


def test_raw_data_denied(harness, audit_events):
    harness.deny()
    harness.client.get(f"/api/datasets/{ENCODED}?data_type=raw_data")
    (event,) = audit_events
    assert_denied_names_request(event, "dataset.read_data")


def test_structured_content_success_and_denial(tmp_path, monkeypatch, audit_events):
    datatype = MagicMock(spec=Binary)
    harness = Harness(tmp_path, monkeypatch, {"enabled": True}, datatype=datatype)
    datatype.get_structured_content.return_value = (b"structured", {})
    response = harness.client.get(f"/api/datasets/{ENCODED}/content/stats?audit_attempt=x")
    assert response.status_code == 200 and response.content == b"structured"
    harness.deny()
    harness.client.get(f"/api/datasets/{ENCODED}/content/stats")
    assert outcomes(audit_events) == [
        ("dataset.read_data", "success", None, "respond"),
        ("dataset.read_data", "denied", "not_accessible", "authorize"),
    ]
    assert audit_events[0]["details"] == {"content_type": "stats"}


def test_structured_content_wrong_datatype_is_an_error_after_access(harness, audit_events):
    response = harness.client.get(f"/api/datasets/{ENCODED}/content/stats")
    # Galaxy answers a non-binary datatype with InvalidFileFormatError, a 500.
    assert response.status_code == 500
    assert outcomes(audit_events) == [("dataset.read_data", "error", "internal_error", "prepare")]


# -- Extra files and metadata files ----------------------------------------------


def test_extra_files_listing_success_and_denial(harness, audit_events):
    response = harness.client.get(f"/api/datasets/{ENCODED}/extra_files")
    assert response.json() == [{"class": "File", "path": "index.html"}]
    harness.client.get(f"/api/histories/{SECURITY.encode_id(3)}/contents/{ENCODED}/extra_files")
    harness.deny()
    harness.client.get(f"/api/datasets/{ENCODED}/extra_files")
    assert outcomes(audit_events) == [
        ("dataset.list_extra_files", "success", None, "prepare"),
        ("dataset.list_extra_files", "success", None, "prepare"),
        ("dataset.list_extra_files", "denied", "not_accessible", "authorize"),
    ]


def test_raw_extra_file_is_a_display(harness, audit_events):
    response = harness.client.get(f"/api/datasets/{ENCODED}/extra_files/raw/index.html")
    assert response.status_code == 200 and response.content == b"<html/>"
    (event,) = audit_events
    assert outcomes([event]) == [("dataset.display", "success", None, "respond")]
    # The extra file's name is user data, left out unless include_names is set.
    assert event["details"] == {"raw": True}


def test_raw_extra_file_denied(harness, audit_events):
    harness.deny()
    harness.client.get(f"/api/datasets/{ENCODED}/extra_files/raw/index.html")
    (event,) = audit_events
    assert_denied_names_request(event, "dataset.display")


def test_metadata_file_download(harness, audit_events):
    response = harness.client.get(f"/api/datasets/{ENCODED}/metadata_file?metadata_file=bam_index")
    assert response.status_code == 200 and response.content == b"index-bytes"
    (event,) = audit_events
    assert outcomes([event]) == [("dataset.download_metadata_file", "success", None, "respond")]
    assert event["details"] == {"metadata_file": "bam_index"}


def test_metadata_file_via_history_contents(harness, audit_events):
    harness.client.get(
        f"/api/histories/{SECURITY.encode_id(3)}/contents/{ENCODED}/metadata_file?metadata_file=bam_index"
    )
    assert [event["action"] for event in audit_events] == ["dataset.download_metadata_file"]


def test_metadata_file_head_and_denial(harness, audit_events):
    harness.client.head(f"/api/datasets/{ENCODED}/metadata_file?metadata_file=bam_index")
    assert audit_events == []
    harness.deny()
    harness.client.head(f"/api/datasets/{ENCODED}/metadata_file?metadata_file=bam_index")
    (event,) = audit_events
    assert_denied_names_request(event, "dataset.download_metadata_file")


def test_unknown_metadata_file_is_an_error_after_access(harness, audit_events):
    harness.hda.metadata.spec.get.return_value = None
    harness.client.get(f"/api/datasets/{ENCODED}/metadata_file?metadata_file=nope")
    assert outcomes(audit_events) == [("dataset.download_metadata_file", "error", "invalid_request", "prepare")]


# -- DRS --------------------------------------------------------------------------


DRS_ID = f"hda-{SECURITY.encode_id(42, kind='drs')}"


def test_drs_object_success(harness, audit_events):
    harness.stub_app.security_agent.dataset_is_public.return_value = True
    response = harness.client.get(f"/ga4gh/drs/v1/objects/{DRS_ID}")
    assert response.status_code == 200
    (event,) = audit_events
    assert outcomes([event]) == [("drs.object", "success", None, "prepare")]
    assert event["details"] == {"object_id": DRS_ID}
    assert event["object"]["uuid"] == "12345678-1234-5678-1234-567812345678"


def test_drs_object_that_is_not_public_is_a_denial_reported_as_missing(harness, audit_events):
    harness.stub_app.security_agent.dataset_is_public.return_value = False
    response = harness.client.get(f"/ga4gh/drs/v1/objects/{DRS_ID}")
    assert response.status_code == 404
    (event,) = audit_events
    assert_denied_names_request(event, "drs.object")


def test_drs_object_waiting_for_a_checksum_records_nothing(harness, audit_events, monkeypatch):
    harness.stub_app.security_agent.dataset_is_public.return_value = True
    harness.hda.dataset.hashes = []
    monkeypatch.setattr(f"{SERVICE_MODULE}.compute_dataset_hash", MagicMock())
    response = harness.client.get(f"/ga4gh/drs/v1/objects/{DRS_ID}")
    assert response.status_code == 202
    assert audit_events == []


def test_malformed_drs_id_records_nothing(harness, audit_events):
    assert harness.client.get("/ga4gh/drs/v1/objects/bogus").status_code == 400
    assert audit_events == []


def test_drs_download_success_and_denial(harness, audit_events):
    response = harness.client.get(f"/api/drs_download/{DRS_ID}")
    assert response.status_code == 200 and response.content == CONTENT
    harness.deny()
    harness.client.get(f"/api/drs_download/{DRS_ID}")
    assert outcomes(audit_events) == [
        ("drs.download", "success", None, "respond"),
        ("drs.download", "denied", "not_accessible", "authorize"),
    ]


def test_drs_download_missing_file_is_an_error(harness, audit_events):
    harness.hda.get_file_name.return_value = "/nonexistent/dataset_11.dat"
    harness.client.get(f"/api/drs_download/{DRS_ID}")
    assert outcomes(audit_events) == [("drs.download", "error", "internal_error", "prepare")]


# -- Identity and the disabled path ------------------------------------------------


def test_impersonating_admin_is_the_actor_on_a_presigned_download(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": True}, session=impersonated_session)
    harness.stub_app.object_store.get_direct_download_url.return_value = PRESIGNED
    harness.client.get(f"/api/datasets/{ENCODED}/download", follow_redirects=False)
    (event,) = audit_events
    assert (event["actor"]["id"], event["effective_user"]["id"]) == (ADMIN.id, ALICE.id)
    assert event["auth"]["switch"] == "impersonation"


def test_plain_session_names_one_user(harness, audit_events):
    harness.client.get(f"/api/datasets/{ENCODED}/get_content_as_text")
    (event,) = audit_events
    assert event["actor"]["id"] == event["effective_user"]["id"] == ALICE.id
    assert event["auth"] == {"method": "session", "credential_id": 99, "switch": None}


def test_family_filter_leaves_other_families_out(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": True, "actions": ["drs"]})
    harness.client.get(f"/api/datasets/{ENCODED}/get_content_as_text")
    harness.client.get(f"/api/drs_download/{DRS_ID}")
    assert [event["action"] for event in audit_events] == ["drs.download"]


def test_disabled_audit_records_nothing_and_changes_nothing(tmp_path, monkeypatch, audit_events):
    harness = Harness(tmp_path, monkeypatch, {"enabled": False})
    harness.stub_app.object_store.get_direct_download_url.return_value = PRESIGNED
    response = harness.client.get(f"/api/datasets/{ENCODED}/download", follow_redirects=False)
    assert response.headers["location"] == PRESIGNED
    assert harness.client.get(f"/api/datasets/{ENCODED}/metadata_file?metadata_file=bam_index").content == (
        b"index-bytes"
    )
    assert harness.client.get(f"/api/drs_download/{DRS_ID}").content == CONTENT
    assert audit_events == []


def test_unknown_metadata_spec_message_is_not_copied(harness, audit_events):
    harness.hda.metadata.spec.get.side_effect = RequestParameterInvalidException("/secret/path")
    harness.client.get(f"/api/datasets/{ENCODED}/metadata_file?metadata_file=x")
    assert "/secret/path" not in json.dumps(audit_events)
