"""Scoped, expiring access tokens: minting under /api/users/{id}/tokens and what they can reach."""

from typing import Any

from galaxy_test.base.decorators import requires_new_user
from galaxy_test.base.populators import DatasetPopulator
from ._framework import ApiTestCase

# Stand-ins for the two tool ids BRC's service runs (kmindex and random_lines in production).
ALLOWED_TOOL = "cat"
OTHER_TOOL = "cat_data_and_sleep"


class TestApiTokens(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)

    def _current_user_id(self) -> str:
        return self._get("users/current").json()["id"]

    def _mint(self, payload: dict[str, Any], status: int = 200) -> dict[str, Any]:
        response = self._post(f"users/{self._current_user_id()}/tokens", data=payload, json=True)
        self._assert_status_code_is(response, status)
        return response.json()

    def _as_token(self, token: str):
        """Send requests with ``token`` in x-api-key instead of the test user's key."""
        return _TokenContext(self, token)

    # ---- the token management routes

    def test_mint_list_revoke(self):
        created = self._mint({"name": "bioblend", "preset": "run-jobs", "expires_in": 3600})
        assert created["token"].startswith("gxy_")
        assert created["token_hint"] == created["token"][4:12]
        assert "histories:write" in created["scopes"] and "account:read" in created["scopes"]
        assert created["active"] and created["expires_at"]

        listed = self._get(f"users/{self._current_user_id()}/tokens").json()
        mine = [t for t in listed if t["id"] == created["id"]]
        assert mine and "token" not in mine[0]

        revoked = self._delete(f"users/{self._current_user_id()}/tokens/{created['id']}").json()
        assert revoked["revoked_at"] and not revoked["active"]
        assert created["id"] not in [t["id"] for t in self._get(f"users/{self._current_user_id()}/tokens").json()]
        inactive = self._get(f"users/{self._current_user_id()}/tokens?include_inactive=true").json()
        assert created["id"] in [t["id"] for t in inactive]

        with self._as_token(created["token"]):
            response = self._get("users/current")
        self._assert_status_code_is(response, 401)
        assert "revoked" in response.json()["err_msg"]

    def test_mint_validates_input(self):
        self._mint({"name": "x", "scopes": "spaceships:fly"}, status=400)
        self._mint({"name": "x", "preset": "nope"}, status=400)
        self._mint({"name": "x"}, status=400)
        self._mint({"name": "x", "scopes": "*:read", "expires_in": 0}, status=400)
        self._mint({"name": "x", "scopes": "*:read", "constraints": {"surfaces": ["ftp"]}}, status=400)
        self._mint({"name": "x", "scopes": "admin:*"}, status=400)

    @requires_new_user
    def test_other_users_tokens_are_not_mine(self):
        created = self._mint({"name": "mine", "scopes": "*:read"})
        with self._different_user("tokens-other@bx.psu.edu"):
            other_id = self._current_user_id()
            response = self._delete(f"users/{other_id}/tokens/{created['id']}")
            self._assert_status_code_is(response, 404)

    def test_revoking_a_token_leaves_the_rest_alone(self):
        first = self._mint({"name": "first", "scopes": "account:read"})
        second = self._mint({"name": "second", "scopes": "account:read"})
        self._delete(f"users/{self._current_user_id()}/tokens/{first['id']}")
        with self._as_token(second["token"]):
            self._assert_status_code_is(self._get("users/current"), 200)
        # and the legacy key keeps working exactly as before
        self._assert_status_code_is(self._get("users/current"), 200)
        self._assert_status_code_is(self._get("histories"), 200)

    def test_scoped_tokens_cannot_mint_tokens_or_run_as(self):
        created = self._mint({"name": "narrow", "scopes": "*:*"})
        with self._as_token(created["token"]):
            response = self._post(
                f"users/{self._current_user_id()}/tokens", data={"name": "x", "scopes": "*:read"}, json=True
            )
            self._assert_status_code_is(response, 403)
            assert "not available to scoped access tokens" in response.json()["err_msg"]
            response = self._get("users/current", headers={"run-as": self._current_user_id()})
            self._assert_status_code_is(response, 403)

    def test_token_works_as_bearer_and_query_key(self):
        created = self._mint({"name": "anywhere", "scopes": "account:read"})
        email = self._get("users/current").json()["email"]
        for headers in ({"Authorization": f"Bearer {created['token']}"}, {"x-api-key": created["token"]}):
            response = self.galaxy_interactor._get("users/current", headers=headers, anon=True)
            self._assert_status_code_is(response, 200)
            assert response.json()["email"] == email
        response = self.galaxy_interactor._get(f"users/current?key={created['token']}", anon=True)
        self._assert_status_code_is(response, 200)
        assert response.json()["email"] == email

    def test_wrong_scope_is_a_403_that_says_why(self):
        created = self._mint({"name": "ro", "preset": "read-only"})
        with self._as_token(created["token"]):
            response = self._post("histories", data={"name": "nope"}, json=True)
        self._assert_status_code_is(response, 403)
        assert "histories:write" in response.json()["err_msg"]
        assert "POST /api/histories" in response.json()["err_msg"]

    def test_legacy_route_scope(self):
        created = self._mint({"name": "ro", "preset": "read-only"})
        history_id = self.dataset_populator.new_history()
        with self._as_token(created["token"]):
            build = self._get(f"tools/{ALLOWED_TOOL}/build", data={"history_id": history_id})
            self._assert_status_code_is(build, 200)
            # an unannotated legacy route refuses the token with the same message as FastAPI routes
            response = self._get(f"tools/{ALLOWED_TOOL}/citations")
        self._assert_status_code_is(response, 403)
        assert "not available to scoped access tokens" in response.json()["err_msg"]
        assert f"GET /api/tools/{ALLOWED_TOOL}/citations" in response.json()["err_msg"]

    # ---- use case 2: BRC's Logan search service account

    def test_run_jobs_service_account(self):
        """Walk the calls BRC's backend makes, with the token the design worked out for it."""
        created = self._mint(
            {
                "name": "logan search",
                "preset": "run-jobs",
                "constraints": {"histories": {"created_by_token": True}, "tools": {"ids": [ALLOWED_TOOL]}},
                "expires_in": 90 * 24 * 3600,
            }
        )
        token = created["token"]
        # A history the account already has, which the service must not see.
        outside_history = self.dataset_populator.new_history(name="not for the service")
        outside_hda = self.dataset_populator.new_dataset(outside_history, content="secret\n", wait=True)
        outside_job = self._get("jobs", data={"history_id": outside_history}).json()[0]["id"]

        with self._as_token(token):
            # users.get_current_user
            me = self._get("users/current")
            self._assert_status_code_is(me, 200)
            assert me.json()["email"]
            # histories.create_history
            history_id = self.dataset_populator.new_history(name="logan job")
            # tools.paste_content -> /api/tools/fetch (polling the job through /api/jobs, as bioblend does)
            upload = self._post(
                "tools/fetch", data=self.dataset_populator.fetch_payload(history_id, content="1 2 3\n"), json=True
            )
            self._assert_status_code_is(upload, 200)
            hda = upload.json()["outputs"][0]
            self.dataset_populator.wait_for_job(upload.json()["jobs"][0]["id"], assert_ok=True)
            # tools.build
            self._assert_status_code_is(self._get(f"tools/{ALLOWED_TOOL}/build", data={"history_id": history_id}), 200)
            # tools.run_tool with the allowed tool
            run = self.dataset_populator.run_tool(ALLOWED_TOOL, {"input1": {"src": "hda", "id": hda["id"]}}, history_id)
            job_id = run["jobs"][0]["id"]
            # jobs.show_job
            self.dataset_populator.wait_for_job(job_id, assert_ok=True)
            self._assert_status_code_is(self._get(f"jobs/{job_id}"), 200)
            # datasets.show_dataset and datasets.download_dataset
            output_id = run["outputs"][0]["id"]
            shown = self._get(f"datasets/{output_id}")
            self._assert_status_code_is(shown, 200)
            download = self._get(shown.json()["download_url"].replace("/api/", "", 1) + "?to_ext=txt")
            self._assert_status_code_is(download, 200)
            assert download.text == "1 2 3\n"
            # histories.get_histories lists only what the token created
            listed = self._get("histories").json()
            assert [h["id"] for h in listed] == [history_id]
            assert outside_history not in [h["id"] for h in listed]
            # and so does the job list
            job_ids = [j["id"] for j in self._get("jobs").json()]
            assert job_id in job_ids and outside_job not in job_ids

            # negative cases
            other_tool = self.dataset_populator.run_tool_raw(
                OTHER_TOOL, {"input1": {"src": "hda", "id": hda["id"]}}, history_id
            )
            self._assert_status_code_is(other_tool, 403)
            assert OTHER_TOOL in other_tool.json()["err_msg"]
            # histories outside the constraint don't exist as far as the token is concerned; things
            # inside them are forbidden
            self._assert_status_code_is(self._get(f"histories/{outside_history}"), 404)
            self._assert_status_code_is(self._get(f"datasets/{outside_hda['id']}"), 403)
            self._assert_status_code_is(self._get(f"jobs/{outside_job}"), 403)
            # uploading into the outside history fails too
            response = self._post(
                "tools/fetch",
                data=self.dataset_populator.fetch_payload(outside_history, content="x\n"),
                json=True,
            )
            self._assert_status_code_is(response, 404)
            # account and sharing routes are refused outright
            self._assert_status_code_is(
                self._put(f"users/{me.json()['id']}", data={"username": "nope"}, json=True), 403
            )
            self._assert_status_code_is(self._put(f"histories/{history_id}/publish"), 403)
            self._assert_status_code_is(self._get(f"users/{me.json()['id']}/api_key"), 403)

        # the account's own key still sees everything, including the service's history
        all_ids = [h["id"] for h in self._get("histories").json()]
        assert history_id in all_ids and outside_history in all_ids


class _TokenContext:
    def __init__(self, test_case: TestApiTokens, token: str):
        self.test_case = test_case
        self.token = token

    def __enter__(self):
        interactor = self.test_case.galaxy_interactor
        self.original = (self.test_case.user_api_key, interactor.api_key)
        self.test_case.user_api_key = self.token
        interactor.api_key = self.token
        return self

    def __exit__(self, *args):
        self.test_case.user_api_key, self.test_case.galaxy_interactor.api_key = self.original
