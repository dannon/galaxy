"""Swap a workflow step to a new revision of a user-defined tool and rerun with the job cache."""

import json
from typing import Any
from uuid import uuid4

from galaxy.tool_util_models import UserToolSource
from galaxy_test.base.populators import (
    DatasetPopulator,
    skip_without_tool,
    TOOL_WITH_SHELL_COMMAND,
    WorkflowPopulator,
)
from ._framework import ApiTestCase

REVISION_B_COMMAND = "echo 'revision b' > output.fastq"


class TestToolRevisionRerunApi(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)
        self.workflow_populator = WorkflowPopulator(self.galaxy_interactor)

    def _workflow_with_user_tool(self, tool_uuid: str) -> dict[str, Any]:
        # input -> cat1 -> user tool -> cat1, native format because format2 has no tool_uuid.
        def step(index: int, label: str, **kwds) -> dict[str, Any]:
            return {
                "id": index,
                "label": label,
                "uuid": str(uuid4()),
                "inputs": [],
                "outputs": [],
                "workflow_outputs": [],
                "post_job_actions": {},
                "input_connections": {},
                **kwds,
            }

        cat1_state = json.dumps({"input1": None, "queries": []})
        return {
            "a_galaxy_workflow": "true",
            "name": "user tool revision rerun",
            "annotation": "",
            "format-version": "0.1",
            "steps": {
                "0": step(0, "input", type="data_input", tool_state=json.dumps({"name": "input"})),
                "1": step(
                    1,
                    "upstream_cat",
                    type="tool",
                    tool_id="cat1",
                    tool_version="1.0.0",
                    tool_state=cat1_state,
                    input_connections={"input1": {"id": 0, "output_name": "output"}},
                ),
                "2": step(
                    2,
                    "user_tool",
                    type="tool",
                    content_id=TOOL_WITH_SHELL_COMMAND["id"],
                    tool_uuid=tool_uuid,
                    tool_state="{}",
                    input_connections={"input": {"id": 1, "output_name": "out_file1"}},
                ),
                "3": step(
                    3,
                    "downstream_cat",
                    type="tool",
                    tool_id="cat1",
                    tool_version="1.0.0",
                    tool_state=cat1_state,
                    input_connections={"input1": {"id": 2, "output_name": "output"}},
                ),
            },
        }

    def _invoke(self, workflow_id: str, request: dict[str, Any]) -> str:
        return self.workflow_populator.invoke_workflow_and_wait(workflow_id, request=request).json()["id"]

    def _jobs_by_label(self, invocation_id: str) -> dict[str, dict[str, Any]]:
        invocation = self.workflow_populator.get_invocation(invocation_id, step_details=True)
        by_label = {}
        for step in invocation["steps"]:
            if step["jobs"]:
                assert len(step["jobs"]) == 1, step
                job = self.dataset_populator.get_job_details(step["jobs"][0]["id"], full=True).json()
                assert job["state"] == "ok", job
                by_label[step["workflow_step_label"]] = job
        return by_label

    def _step_by_label(self, workflow: dict[str, Any], label: str) -> tuple[str, dict[str, Any]]:
        for index, step in workflow["steps"].items():
            if step["label"] == label:
                return index, step
        raise AssertionError(f"no step labelled {label} in {workflow['steps']}")

    def _output_content(self, history_id: str, job: dict[str, Any]) -> str:
        [output] = self.dataset_populator.job_outputs(job["id"])
        return self.dataset_populator.get_history_dataset_content(history_id, dataset_id=output["dataset"]["id"])

    @skip_without_tool("cat1")
    def test_swap_step_to_new_revision_and_rerun_with_cache(self):
        with (
            self.dataset_populator.test_history() as history_id,
            self.dataset_populator.user_tool_execute_permissions(),
        ):
            revision_a = self.dataset_populator.create_unprivileged_tool(UserToolSource(**TOOL_WITH_SHELL_COMMAND))
            workflow_id = self.workflow_populator.create_workflow(self._workflow_with_user_tool(revision_a["uuid"]))
            dataset = self.dataset_populator.new_dataset(history_id, content="abc", wait=True)
            first_invocation_id = self._invoke(
                workflow_id,
                {
                    "history": f"hist_id={history_id}",
                    "inputs": json.dumps({"input": {"src": "hda", "id": dataset["id"]}}),
                    "inputs_by": "name",
                },
            )
            first_jobs = self._jobs_by_label(first_invocation_id)
            assert set(first_jobs) == {"upstream_cat", "user_tool", "downstream_cat"}
            assert self._output_content(history_id, first_jobs["user_tool"]) == "abc\n"

            # Save revision B (same id and version, new command) and point the step at it, the way
            # the editor saves: the editor-style dict goes back through PUT /api/workflows/{id}.
            revision_b = self.dataset_populator.create_unprivileged_tool(
                UserToolSource(**{**TOOL_WITH_SHELL_COMMAND, "shell_command": REVISION_B_COMMAND})
            )
            editor_workflow = self.workflow_populator.download_workflow(workflow_id, style="editor")
            step_index, user_tool_step = self._step_by_label(editor_workflow, "user_tool")
            assert user_tool_step["tool_uuid"] == revision_a["uuid"]
            tool_state_before = user_tool_step["tool_state"]
            user_tool_step["tool_uuid"] = revision_b["uuid"]
            update_response = self.workflow_populator.update_workflow(workflow_id, editor_workflow)
            assert update_response.status_code == 200, update_response.text

            updated_workflow = self.workflow_populator.download_workflow(workflow_id, style="editor")
            updated_index, updated_step = self._step_by_label(updated_workflow, "user_tool")
            assert updated_index == step_index
            assert updated_step["tool_uuid"] == revision_b["uuid"]
            assert updated_step["tool_state"] == tool_state_before, (tool_state_before, updated_step["tool_state"])
            assert updated_step["input_connections"] == user_tool_step["input_connections"]
            assert updated_workflow["version"] == editor_workflow["version"] + 1

            # The exported request pins the workflow version that ran (instance=True plus that
            # version's own id), so replaying it unchanged reruns revision A and reuses everything.
            request = self.workflow_populator.invocation_to_request(first_invocation_id)
            assert request["instance"] is True
            pinned_request = {**request, "use_cached_job": True}
            pinned_invocation_id = self._invoke(request["workflow_id"], pinned_request)
            pinned_jobs = self._jobs_by_label(pinned_invocation_id)
            for label, job in pinned_jobs.items():
                assert job["copied_from_job_id"] == first_jobs[label]["id"], (label, job)

            # Pointing the same request at the stored workflow (latest version) is the rerun we want:
            # the upstream job is reused, the edited step and what follows it run again.
            latest_request = {**request, "instance": False, "use_cached_job": True}
            second_invocation_id = self._invoke(workflow_id, latest_request)
            second_jobs = self._jobs_by_label(second_invocation_id)
            assert set(second_jobs) == set(first_jobs)
            assert second_jobs["upstream_cat"]["copied_from_job_id"] == first_jobs["upstream_cat"]["id"]
            assert not second_jobs["user_tool"]["copied_from_job_id"], second_jobs["user_tool"]
            assert not second_jobs["downstream_cat"]["copied_from_job_id"], second_jobs["downstream_cat"]
            assert self._output_content(history_id, second_jobs["user_tool"]) == "revision b\n"
            assert self._output_content(history_id, second_jobs["downstream_cat"]) == "revision b\n"

            # Running the new version once more reuses the whole thing, revision B's job included.
            third_invocation_id = self._invoke(workflow_id, latest_request)
            third_jobs = self._jobs_by_label(third_invocation_id)
            for label, job in third_jobs.items():
                assert job["copied_from_job_id"] == second_jobs[label]["copied_from_job_id"] or (
                    job["copied_from_job_id"] == second_jobs[label]["id"]
                ), (label, job)
            assert third_jobs["user_tool"]["copied_from_job_id"] == second_jobs["user_tool"]["id"]
