"""A scoped token restricted to the MCP surface opens /api/mcp and nothing else."""

import json
from urllib.parse import urljoin

import requests

from galaxy_test.base.mcp_util import mcp_request
from galaxy_test.base.populators import (
    DatasetPopulator,
    WorkflowPopulator,
)
from galaxy_test.base.workflow_fixtures import WORKFLOW_SIMPLE_CAT_TWICE
from galaxy_test.driver.integration_util import IntegrationTestCase

AGENT_PRESET = "agent"

WORKFLOW_WITH_API_KEY_TOOL = """
class: GalaxyWorkflow
steps:
  inject:
    tool_id: environment_variables_inject
    state:
      inttest: 1
"""


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

    def _mint(self, constraints: dict | None, preset: str = AGENT_PRESET, scopes: str | None = None) -> str:
        user_id = self._get("users/current").json()["id"]
        payload: dict = {"name": "claude code", "preset": preset}
        if constraints:
            payload["constraints"] = constraints
        if scopes:
            payload["scopes"] = scopes
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

    def test_insufficient_scope_names_the_missing_scope(self):
        token = self._mint(None, preset="read-only")
        assert self._result(self._call("list_histories", {}, token)) is not None
        error = self._error(self._call("create_history", {"name": "nope"}, token))
        assert "histories:write" in error, error
        assert "create_history" in error, error

    def test_api_only_token_is_refused_on_mcp(self):
        token = self._mint({"surfaces": ["api"]})
        assert self._rest("api/users/current", token).status_code == 200
        error = self._error(self._call("get_user", {}, token))
        assert "restricted to the api surface" in error, error

    def test_tool_allowlist_filters_listings(self):
        token = self._mint({"tools": {"ids": ["cat"]}}, preset="run-jobs")
        found = self._result(self._call("search_tools", {"query": "cat"}, token))["tools"]
        assert {t["id"] for t in found} == {"cat"}, found
        found = self._result(self._call("search_tools_by_keywords", {"keywords": ["cat"]}, token))["tools"]
        assert {t["id"] for t in found} == {"cat"}, found
        panel = json.dumps(self._result(self._call("get_tool_panel", {}, token)))
        assert '"cat"' in panel and "cat_data_and_sleep" not in panel
        assert self._result(self._call("get_tool_details", {"tool_id": "cat"}, token))["id"] == "cat"
        error = self._error(self._call("get_tool_details", {"tool_id": "cat_data_and_sleep"}, token))
        assert "cat_data_and_sleep" in error, error

    def test_workflow_allowlist(self):
        workflow_populator = WorkflowPopulator(self.galaxy_interactor)
        allowed = workflow_populator.upload_yaml_workflow(WORKFLOW_SIMPLE_CAT_TWICE)
        other = workflow_populator.upload_yaml_workflow(WORKFLOW_SIMPLE_CAT_TWICE)
        token = self._mint({"workflows": {"ids": [allowed]}})
        history_id = self._result(self._call("create_history", {"name": "wf"}, token))["id"]

        listed = self._result(self._call("list_workflows", {}, token))["workflows"]
        assert [w["id"] for w in listed] == [allowed], listed
        assert self._result(self._call("get_workflow_details", {"workflow_id": allowed}, token))
        error = self._error(self._call("get_workflow_details", {"workflow_id": other}, token))
        assert "access token" in error, error
        error = self._error(
            self._call("invoke_workflow", {"workflow_id": other, "history_id": history_id, "inputs": {}}, token)
        )
        assert "access token" in error, error
        # the account's own key still lists both
        assert {w["id"] for w in self._get("workflows").json()} >= {allowed, other}

    def test_workflow_invocations_respect_the_token(self):
        workflow_populator = WorkflowPopulator(self.galaxy_interactor)
        cat_workflow = workflow_populator.upload_yaml_workflow(WORKFLOW_SIMPLE_CAT_TWICE)
        inject_workflow = workflow_populator.upload_yaml_workflow(WORKFLOW_WITH_API_KEY_TOOL)
        outside_history = self.populator.new_history(name="outside")
        outside_hda = self.populator.new_dataset(outside_history, content="secret\n", wait=True)
        outside_invocation = workflow_populator.invoke_workflow_and_wait(
            cat_workflow, history_id=outside_history, inputs={"0": {"src": "hda", "id": outside_hda["id"]}}
        ).json()["id"]
        token = self._mint({"histories": {"created_by_token": True}})

        own_history = self._result(self._call("create_history", {"name": "mine"}, token))["id"]
        self._result(
            self._call(
                "upload_file_from_url",
                {"history_id": own_history, "url": "base64://MSAyIDMK", "file_name": "numbers"},
                token,
            )
        )
        self.populator.wait_for_history(own_history, assert_ok=True)
        own_hda = self._result(self._call("get_history_contents", {"history_id": own_history}, token))["contents"][0][
            "id"
        ]

        # a dataset from a history the token can't see is not a workflow input
        error = self._error(
            self._call(
                "invoke_workflow",
                {
                    "workflow_id": cat_workflow,
                    "history_id": own_history,
                    "inputs": {"0": {"src": "hda", "id": outside_hda["id"]}},
                },
                token,
            )
        )
        assert "access token" in error, error
        # a step that would hand the job the user's API key is refused before anything runs
        error = self._error(
            self._call("invoke_workflow", {"workflow_id": inject_workflow, "history_id": own_history}, token)
        )
        assert "own credentials" in error, error

        # a run into a new history leaves that history, and its invocation, visible to the token
        run = self._result(
            self._call(
                "invoke_workflow",
                {
                    "workflow_id": cat_workflow,
                    "history_name": "from the workflow",
                    "inputs": {"0": {"src": "hda", "id": own_hda}},
                },
                token,
            )
        )
        new_history = run["history_id"]
        self.populator.wait_for_history(new_history, assert_ok=True)
        listed = self._result(self._call("list_histories", {}, token))["histories"]
        listed_ids = [h["id"] for h in listed]
        assert new_history in listed_ids and own_history in listed_ids
        assert outside_history not in listed_ids
        invocations = self._result(self._call("get_invocations", {}, token))["invocations"]
        invocation_ids = [i["id"] for i in invocations]
        assert run["id"] in invocation_ids
        assert outside_invocation not in invocation_ids
        assert self._result(self._call("get_invocation_details", {"invocation_id": run["id"]}, token))
        for op in ("get_invocation_details", "cancel_workflow_invocation"):
            error = self._error(self._call(op, {"invocation_id": outside_invocation}, token))
            assert "access token" in error, (op, error)

    def test_agent_token_without_surface_works_on_both(self):
        token = self._mint(None)
        assert self._result(self._call("get_user", {}, token))["email"]
        assert self._rest("api/users/current", token).status_code == 200

    def test_unscoped_key_still_works_everywhere(self):
        key = self.galaxy_interactor.api_key
        assert key
        assert self._result(self._call("get_user", {}, key))["email"]
        assert self._rest("api/users/current", key).status_code == 200
        # an unannotated legacy route, which a scoped token could never open
        assert self._rest("api/tool_panels", key).status_code == 200
