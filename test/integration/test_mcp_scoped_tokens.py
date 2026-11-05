"""A scoped token restricted to the MCP surface opens /api/mcp and nothing else."""

import json
from urllib.parse import urljoin

import requests

from galaxy_test.base.mcp_util import mcp_request
from galaxy_test.base.populators import DatasetPopulator
from galaxy_test.driver.integration_util import IntegrationTestCase

AGENT_PRESET = "agent"


class TestMCPOnlyToken(IntegrationTestCase):
    mcp_server_path = "/api/mcp"

    @classmethod
    def handle_galaxy_config_kwds(cls, config) -> None:
        super().handle_galaxy_config_kwds(config)
        config["enable_mcp_server"] = True
        config["mcp_server_path"] = cls.mcp_server_path

    def setUp(self):
        super().setUp()
        self.populator = DatasetPopulator(self.galaxy_interactor)

    @property
    def _mcp_url(self) -> str:
        return urljoin(self.url, self.mcp_server_path.lstrip("/"))

    def _call(self, name: str, arguments: dict, token: str) -> dict:
        return mcp_request(self._mcp_url, "tools/call", {"name": name, "arguments": arguments}, {"x-api-key": token})

    def _result(self, result: dict) -> dict:
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    def _error(self, result: dict) -> str:
        assert result.get("isError"), result
        return result["content"][0]["text"]

    def _rest(self, path: str, token: str, method: str = "get", **kwargs) -> requests.Response:
        return requests.request(method, urljoin(self.url, path), headers={"x-api-key": token}, timeout=60, **kwargs)

    def _mint(self, constraints: dict | None, preset: str = AGENT_PRESET) -> str:
        user_id = self._get("users/current").json()["id"]
        payload: dict = {"name": "claude code", "preset": preset}
        if constraints:
            payload["constraints"] = constraints
        response = self._post(f"users/{user_id}/tokens", data=payload, json=True)
        self._assert_status_code_is(response, 200)
        return response.json()["token"]

    def test_mcp_only_token(self):
        token = self._mint({"surfaces": ["mcp"]})

        # the MCP server works end to end
        tools = self._result(self._call("search_tools", {"query": "cat"}, token))
        assert tools
        history = self._result(self._call("create_history", {"name": "from mcp"}, token))
        history_id = history["id"]
        uploaded = self._result(
            self._call(
                "upload_file_from_url",
                {"history_id": history_id, "url": "base64://MSAyIDMK", "file_name": "numbers"},
                token,
            )
        )
        self.populator.wait_for_history(history_id, assert_ok=True)
        assert uploaded
        contents = self._result(self._call("get_history_contents", {"history_id": history_id}, token))
        hda_id = contents["contents"][0]["id"]
        run = self._result(
            self._call(
                "run_tool",
                {"history_id": history_id, "tool_id": "cat", "inputs": {"input1": {"src": "hda", "id": hda_id}}},
                token,
            )
        )
        job_id = run["jobs"][0]["id"]
        self.populator.wait_for_job(job_id, assert_ok=True)
        status = self._result(self._call("get_job_status", {"job_id": job_id}, token))
        assert status["job"]["state"] == "ok"

        # the same credential is refused on every REST route those ops lean on, saying why
        for path in ("api/histories", "api/users/current", f"api/jobs/{job_id}", "api/tools/cat/build"):
            response = self._rest(path, token)
            assert response.status_code == 403, (path, response.text)
            assert "restricted to the mcp surface" in response.json()["err_msg"], response.text
        response = self._rest("api/tools/fetch", token, method="post", json={"history_id": history_id, "targets": []})
        assert response.status_code == 403

        # ops outside the agent preset's scopes are refused on the MCP side too
        error = self._error(self._call("create_user_tool", {"representation": {}}, token))
        assert "not available to scoped access tokens" in error

    def test_agent_token_without_surface_works_on_both(self):
        token = self._mint(None)
        assert self._result(self._call("get_user", {}, token))["email"]
        assert self._rest("api/users/current", token).status_code == 200

    def test_unscoped_key_still_works_everywhere(self):
        key = self.galaxy_interactor.api_key
        assert key
        assert self._result(self._call("get_user", {}, key))["email"]
        assert self._rest("api/users/current", key).status_code == 200
        assert self._rest("api/tools/error_stack", key).status_code in (200, 403)
