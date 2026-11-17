"""A protected drs:// fetch runs as a real job and sends the user's RAS passport.

The DRS server is a local fake, so no network is needed.
"""

import hashlib
import json
import os
import threading
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from typing import (
    Any,
    ClassVar,
)

from galaxy_test.base.populators import DatasetPopulator
from galaxy_test.driver import integration_util
from ._ras_passport import ConfiguresRasPassport

RAS_PASSPORT_REQUIRED = 403009

OBJECT_PATH = "/ga4gh/drs/v1/objects/obj1"
ACCESS_PREFIX = f"{OBJECT_PATH}/access/"
ACCESS_PATH = f"{ACCESS_PREFIX}a1"
DATA_PATH = "/data/obj1"
CONTENT = "protected content\n"

RecordedRequest = tuple[str, str, list[str] | None]


class FakeDrsServer(ThreadingHTTPServer):
    """Serves one DRS object whose access URL is only handed out for the expected passports."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeDrsHandler)
        self.requests: list[RecordedRequest] = []
        # Raw headers of every request, to check no passport travels outside a POST body.
        self.request_headers: list[str] = []
        self.expected_passports: list[str] | None = None
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join()

    def reset(self) -> None:
        self.requests.clear()
        self.request_headers.clear()
        self.expected_passports = None


class FakeDrsHandler(BaseHTTPRequestHandler):
    server: FakeDrsServer

    def do_GET(self) -> None:
        self.server.request_headers.append(str(self.headers))
        self.server.requests.append(("GET", self.path, None))
        if self.path == OBJECT_PATH:
            self._send_json(200, self._drs_object())
        elif self.path == DATA_PATH:
            self._send(200, CONTENT.encode(), "text/plain")
        elif self.path.startswith(ACCESS_PREFIX):
            self._send_json(401, {"msg": "a passport is required", "status_code": 401})
        else:
            self._send_json(404, {"msg": "not found", "status_code": 404})

    def do_POST(self) -> None:
        self.server.request_headers.append(str(self.headers))
        length = int(self.headers.get("Content-Length") or 0)
        passports = None
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            body = None
        if isinstance(body, dict):
            passports = body.get("passports")
        self.server.requests.append(("POST", self.path, passports))
        expected = self.server.expected_passports
        if self.path == ACCESS_PATH and expected is not None and passports == expected:
            self._send_json(200, {"url": f"http://127.0.0.1:{self.server.server_port}{DATA_PATH}"})
        elif self.path.startswith(ACCESS_PREFIX):
            self._send_json(401, {"msg": "passport not accepted", "status_code": 401})
        else:
            self._send_json(404, {"msg": "not found", "status_code": 404})

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the test output quiet; requests are recorded on the server instead.
        pass

    def _drs_object(self) -> dict[str, Any]:
        return {
            "id": "obj1",
            "self_uri": f"drs://127.0.0.1:{self.server.server_port}/obj1",
            "size": len(CONTENT),
            "created_time": "2026-01-01T00:00:00Z",
            "checksums": [{"type": "sha-256", "checksum": hashlib.sha256(CONTENT.encode()).hexdigest()}],
            "access_methods": [{"type": "https", "access_id": "a1"}],
        }

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestRasPassportDrsFetchIntegration(integration_util.IntegrationTestCase, ConfiguresRasPassport):
    drs_server: ClassVar[FakeDrsServer]
    populator: DatasetPopulator

    @classmethod
    def setUpClass(cls):
        # Galaxy's config has to name the server's port, so it starts first.
        cls.drs_server = FakeDrsServer()
        cls.drs_server.start()
        try:
            super().setUpClass()
        except BaseException:
            cls.drs_server.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls.drs_server.stop()

    def setUp(self):
        super().setUp()
        self.populator = DatasetPopulator(self.galaxy_interactor)
        self.drs_server.reset()

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        port = cls.drs_server.server_port
        config_dir = cls._write_ras_config(
            config,
            f"protected_sources:\n  - url_regex: '^drs://127\\.0\\.0\\.1:{port}/'\n    phs_id: phs000123\n",
        )
        file_sources_config_file = os.path.join(config_dir, "file_sources_conf.yml")
        with open(file_sources_config_file, "w") as f:
            f.write(
                "- type: drs\n"
                "  id: protected_drs\n"
                "  url_regex: '^drs://127\\.0\\.0\\.1'\n"
                "  force_http: true\n"
                "  ras_passport: true\n"
            )
        config["file_sources_config_file"] = file_sources_config_file

    @classmethod
    def _configure_app(cls):
        cls._install_test_jwks()

    @property
    def drs_uri(self) -> str:
        return f"drs://127.0.0.1:{self.drs_server.server_port}/obj1"

    def _fetch(self, history_id, target=None):
        target = target or {"destination": {"type": "hdas"}, "elements": [{"src": "url", "url": self.drs_uri}]}
        return self.populator.fetch({"history_id": history_id, "targets": [target]}, assert_ok=False, wait=False)

    def _assert_refused(self, response, reason, history_id):
        self._assert_status_code_is(response, 403)
        body = response.json()
        assert body["err_code"] == RAS_PASSPORT_REQUIRED
        assert body["ras_passport_reason"] == reason
        assert not self.populator.get_history_contents(history_id)

    def test_protected_fetch_without_passport_never_reaches_the_server(self):
        with self._different_user("ras-drs-missing@test.gx"):
            history_id = self.populator.new_history()
            self._assert_refused(self._fetch(history_id), "missing", history_id)
        assert self.drs_server.requests == []

    def test_protected_url_in_nested_collection_is_refused(self):
        with self._different_user("ras-drs-nested@test.gx"):
            history_id = self.populator.new_history()
            elements = [
                {
                    "name": "outer",
                    "elements": [{"name": "inner", "elements": [{"name": "x", "src": "url", "url": self.drs_uri}]}],
                }
            ]
            target = {"destination": {"type": "hdca"}, "collection_type": "list:list:list", "elements": elements}
            self._assert_refused(self._fetch(history_id, target), "missing", history_id)
        assert self.drs_server.requests == []

    def test_protected_fetch_with_passport_for_another_study_is_refused(self):
        email = "ras-drs-other-study@test.gx"
        with self._different_user(email):
            self._store_passport(email, self._passport("phs000999"))
            history_id = self.populator.new_history()
            self._assert_refused(self._fetch(history_id), "no_access", history_id)
        assert self.drs_server.requests == []

    def test_protected_fetch_with_passport_delivers_the_data(self):
        email = "ras-drs-granted@test.gx"
        passport = self._passport("phs000123")
        self.drs_server.expected_passports = [passport]
        with self._different_user(email):
            self._store_passport(email, passport)
            history_id = self.populator.new_history()
            response = self._fetch(history_id)
            self._assert_status_code_is(response, 200)
            job = self.populator.check_run(response)
            # assert_ok fails as soon as the job errors, with its stderr in the message.
            self.populator.wait_for_job(job["id"], assert_ok=True)
            job_details = self.populator.get_job_details(job["id"], full=True).json()
            output = response.json()["outputs"][0]
            # A failed fetch leaves the job ok and puts the error on the dataset instead.
            details = self.populator.get_history_dataset_details(history_id, dataset=output, assert_ok=False)
            assert details["state"] == "ok", details["misc_info"]
            content = self.populator.get_history_dataset_content(history_id, dataset=output)
        assert content == CONTENT
        assert self.drs_server.requests == [
            ("GET", OBJECT_PATH, None),
            ("POST", ACCESS_PATH, [passport]),
            ("GET", DATA_PATH, None),
        ]
        assert not any(passport in headers for headers in self.drs_server.request_headers)
        assert passport not in json.dumps(job_details)

    def test_legacy_upload_of_protected_url_is_not_gated(self):
        # Only /api/tools/fetch checks protected_sources. upload1 takes the URL without a
        # study check, and the ras_passport source still sends whatever passport the user
        # has, so here it is the DRS server that refuses the other study's passport.
        email = "ras-drs-upload1@test.gx"
        passport = self._passport("phs000999")
        with self._different_user(email):
            self._store_passport(email, passport)
            history_id = self.populator.new_history()
            response = self.populator.new_dataset_request(history_id, content=self.drs_uri, fetch_data=False)
            self._assert_status_code_is(response, 200)
            job = self.populator.check_run(response)
            self.populator.wait_for_job(job["id"], assert_ok=False)
            output = response.json()["outputs"][0]
            details = self.populator.get_history_dataset_details(history_id, dataset=output, assert_ok=False)
            job_details = self.populator.get_job_details(job["id"], full=True).json()
        assert details["state"] == "error"
        assert "did not accept the NIH RAS passport" in details["misc_info"]
        assert self.drs_server.requests == [
            ("GET", OBJECT_PATH, None),
            ("POST", ACCESS_PATH, [passport]),
        ]
        assert passport not in json.dumps(job_details)
