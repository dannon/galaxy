"""Scoped API token minting, resolution and the scope/constraint grammar."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from galaxy import (
    exceptions,
    model,
)
from galaxy.managers.api_tokens import (
    ApiTokenManager,
    Credential,
    credential_permits_history,
    credential_permits_tool,
    current_credential,
    parse_scopes,
    PRESETS,
    require_scope,
    reset_current_credential,
    scoped_credential_refuses_tool,
    scopes_allow,
    set_current_credential,
    TOKEN_PREFIX,
    validate_constraints,
)
from galaxy.model.orm.now import now
from .base import BaseTestCase

user2_data = dict(email="user2@user2.user2", username="user2", password="123456")


def FakeTool(tool_id: str, old_id: str | None = None) -> Any:
    return SimpleNamespace(id=tool_id, old_id=old_id or tool_id)


def test_parse_scopes_accepts_the_presets():
    for text in PRESETS.values():
        assert parse_scopes(text)


@pytest.mark.parametrize("bad", ["histories", "histories:fly", "spaceships:read", "", ":read", "histories:"])
def test_parse_scopes_rejects_malformed_scopes(bad):
    with pytest.raises(exceptions.RequestParameterInvalidException):
        parse_scopes(bad)


def test_scopes_allow_wildcards_and_write_implies_read():
    assert scopes_allow({"histories:write"}, "histories:read")
    assert not scopes_allow({"histories:read"}, "histories:write")
    assert scopes_allow({"*:read"}, "datasets:read")
    assert not scopes_allow({"*:read"}, "datasets:write")
    assert scopes_allow({"tools:*"}, "tools:run")
    assert not scopes_allow({"tools:run"}, "tools:read")
    assert not scopes_allow({"histories:read"}, "datasets:read")


def test_scopes_allow_never_reaches_admin_through_a_wildcard():
    assert not scopes_allow({"*:*"}, "admin:*")
    assert scopes_allow({"admin:*"}, "admin:*")


def test_require_scope():
    require_scope(None, None)
    require_scope(Credential(kind="session"), None)
    scoped = Credential(kind="token", scopes=frozenset({"histories:read"}))
    require_scope(scoped, "histories:read")
    with pytest.raises(exceptions.InsufficientPermissionsException, match="not available to scoped"):
        require_scope(scoped, None)
    with pytest.raises(exceptions.InsufficientPermissionsException, match="histories:write"):
        require_scope(scoped, "histories:write")


def test_validate_constraints():
    assert validate_constraints(None) is None
    assert validate_constraints({"histories": {}}) is None
    cleaned = validate_constraints(
        {"histories": {"created_by_token": True, "ids": [3, 1, 3]}, "tools": {"ids": ["b", "a"]}, "surfaces": ["mcp"]}
    )
    assert cleaned == {
        "histories": {"created_by_token": True, "ids": [1, 3]},
        "tools": {"ids": ["a", "b"]},
        "surfaces": ["mcp"],
    }
    with pytest.raises(exceptions.RequestParameterInvalidException):
        validate_constraints({"spaceships": {"ids": [1]}})
    with pytest.raises(exceptions.RequestParameterInvalidException):
        validate_constraints({"histories": {"ids": ["not-an-int"]}})
    with pytest.raises(exceptions.RequestParameterInvalidException):
        validate_constraints({"surfaces": ["ftp"]})


def test_validate_constraints_rejects_empty_allowlists():
    empties: list[dict[str, Any]] = [{"tools": {"ids": []}}, {"workflows": {"ids": []}}, {"surfaces": []}]
    for bad in empties:
        with pytest.raises(exceptions.RequestParameterInvalidException, match="empty"):
            validate_constraints(bad)


def test_short_tool_ids_do_not_match_tool_shed_tools():
    cred = Credential(kind="token", scopes=frozenset(), constraints={"tools": {"ids": ["kmindex"]}})
    assert credential_permits_tool(FakeTool("kmindex"), cred)
    assert not credential_permits_tool(FakeTool("toolshed/repos/x/kmindex/0.1.0", "kmindex"), cred)


def test_scoped_credential_refuses_tools_that_carry_wider_credentials():
    scoped = Credential(kind="token", scopes=frozenset({"tools:run"}))
    assert scoped_credential_refuses_tool(FakeTool("cat"), scoped) is None
    for inject in ("api_key", "oidc_keycloak"):
        tool = FakeTool("gxit")
        tool.environment_variables = [{"name": "X", "inject": inject}]
        assert scoped_credential_refuses_tool(tool, scoped)
        assert scoped_credential_refuses_tool(tool, None) is None
    user_defined = FakeTool("udt")
    user_defined.dynamic_tool = object()
    assert scoped_credential_refuses_tool(user_defined, scoped)
    assert scoped_credential_refuses_tool(user_defined, Credential(kind="legacy_key")) is None


def test_tool_constraint_matches_versioned_and_upload_ids():
    cred = Credential(kind="token", scopes=frozenset(), constraints={"tools": {"ids": ["toolshed/repos/x/kmindex"]}})
    assert credential_permits_tool(FakeTool("toolshed/repos/x/kmindex/0.1.0", "kmindex"), cred)
    assert credential_permits_tool(FakeTool("__DATA_FETCH__"), cred)
    assert not credential_permits_tool(FakeTool("cat1"), cred)
    assert credential_permits_tool(FakeTool("cat1"), Credential(kind="token", scopes=frozenset()))
    assert credential_permits_tool(FakeTool("cat1"), None)


def test_current_credential_is_request_local():
    assert current_credential() is None
    token = set_current_credential(Credential(kind="token", scopes=frozenset({"tools:read"})))
    try:
        credential = current_credential()
        assert credential is not None and credential.allows("tools:read")
    finally:
        reset_current_credential(token)
    assert current_credential() is None


class TestApiTokenManager(BaseTestCase):
    def set_up_managers(self):
        super().set_up_managers()
        self.manager = ApiTokenManager(self.app)
        self.user = self.user_manager.create(**user2_data)

    def test_mint_and_resolve(self):
        token, secret = self.manager.mint_token(self.user, "bioblend", PRESETS["run-jobs"], expires_in=3600)
        assert secret.startswith(TOKEN_PREFIX) and len(secret) == len(TOKEN_PREFIX) + 40
        assert token.token_hint == secret[4:12]
        assert secret not in token.token_hash
        assert token.source == "ui"
        assert token.expires_at and token.expires_at - now() < timedelta(hours=1, seconds=5)
        user, credential = self.manager.resolve(secret)
        assert user == self.user
        assert credential.scoped and credential.token_id == token.id
        assert credential.allows("histories:read") and not credential.allows("histories:share")
        assert token.last_used_at is not None

    def test_mint_caps_the_lifetime(self):
        with patch.object(self.app.config, "api_token_max_lifetime", 2, create=True):
            token, _ = self.manager.mint_token(self.user, "long", "*:read", expires_in=10 * 24 * 3600)
            assert token.expires_at and token.expires_at - now() <= timedelta(days=2)
            token, _ = self.manager.mint_token(self.user, "default", "*:read")
            assert token.expires_at and token.expires_at - now() <= timedelta(days=2)
        with patch.object(self.app.config, "api_token_max_lifetime", 0, create=True):
            token, _ = self.manager.mint_token(self.user, "forever", "*:read")
            assert token.expires_at is None

    def test_mint_rejects_bad_input(self):
        with pytest.raises(exceptions.RequestParameterInvalidException):
            self.manager.mint_token(self.user, "  ", "*:read")
        with pytest.raises(exceptions.RequestParameterInvalidException):
            self.manager.mint_token(self.user, "x", "nope:read")
        with pytest.raises(exceptions.RequestParameterInvalidException):
            self.manager.mint_token(self.user, "x", "*:read", expires_in=0)
        with pytest.raises(exceptions.RequestParameterInvalidException, match="administrators"):
            self.manager.mint_token(self.user, "x", "admin:*")
        with pytest.raises(exceptions.RequestParameterInvalidException):
            self.manager.mint_token(self.user, "x", "*:read", source="carrier-pigeon")
        with pytest.raises(exceptions.RequestParameterInvalidException, match="workflows:run"):
            self.manager.mint_token(self.user, "x", "tools:run workflows:run", constraints={"tools": {"ids": ["cat"]}})
        with pytest.raises(exceptions.RequestParameterInvalidException, match="workflows:run"):
            self.manager.mint_token(self.user, "x", PRESETS["agent"], constraints={"tools": {"ids": ["cat"]}})
        assert self.manager.mint_token(self.user, "ok", PRESETS["run-jobs"], constraints={"tools": {"ids": ["cat"]}})

    def test_admins_may_mint_admin_scopes(self):
        token, _ = self.manager.mint_token(self.admin_user, "root", "admin:*")
        assert self.manager.credential_for(token).allows("admin:*")

    def test_resolve_refuses_unknown_expired_and_revoked(self):
        with pytest.raises(exceptions.AuthenticationFailed, match="not valid"):
            self.manager.resolve(TOKEN_PREFIX + "x" * 40)
        token, secret = self.manager.mint_token(self.user, "old", "*:read", expires_in=60)
        token.expires_at = now() - timedelta(seconds=1)
        self.trans.sa_session.commit()
        with pytest.raises(exceptions.AuthenticationFailed, match="expired"):
            self.manager.resolve(secret)
        token, secret = self.manager.mint_token(self.user, "gone", "*:read")
        self.manager.revoke(self.user, token.id)
        with pytest.raises(exceptions.AuthenticationFailed, match="revoked"):
            self.manager.resolve(secret)

    def test_resolve_refuses_deactivated_users(self):
        _, secret = self.manager.mint_token(self.user, "t", "*:read")
        self.user.deleted = True
        self.trans.sa_session.commit()
        with pytest.raises(exceptions.AuthenticationFailed, match="deactivated"):
            self.manager.resolve(secret)

    def test_surface_restriction(self):
        _, secret = self.manager.mint_token(self.user, "mcp only", PRESETS["agent"], constraints={"surfaces": ["mcp"]})
        with pytest.raises(exceptions.InsufficientPermissionsException, match="restricted to the mcp surface"):
            self.manager.resolve(secret, surface="api")
        user, credential = self.manager.resolve(secret, surface="mcp")
        assert user == self.user and credential.surfaces == frozenset({"mcp"})
        _, plain = self.manager.mint_token(self.user, "anywhere", PRESETS["agent"])
        assert self.manager.resolve(plain, surface="api")[1].surfaces is None

    def test_revoke_only_own_tokens(self):
        token, _ = self.manager.mint_token(self.user, "t", "*:read")
        with pytest.raises(exceptions.ObjectNotFound):
            self.manager.revoke(self.admin_user, token.id)
        assert token.revoked_at is None
        revoked = self.manager.revoke(self.user, token.id)
        assert revoked.revoked_at is not None
        assert self.manager.tokens_for(self.user) == []
        assert self.manager.tokens_for(self.user, include_inactive=True) == [token]

    def test_last_used_is_written_sparingly(self):
        token, secret = self.manager.mint_token(self.user, "t", "*:read")
        self.manager.resolve(secret)
        first = token.last_used_at
        assert first is not None
        self.manager.resolve(secret)
        assert token.last_used_at == first
        token.last_used_at = now() - timedelta(minutes=6)
        self.trans.sa_session.commit()
        self.manager.resolve(secret)
        assert token.last_used_at > first

    def test_history_constraint(self):
        token, _ = self.manager.mint_token(
            self.user, "t", "*:read", constraints={"histories": {"created_by_token": True}}
        )
        credential = self.manager.credential_for(token)
        mine = model.History(user=self.user, name="mine")
        mine.api_token_id = token.id
        theirs = model.History(user=self.user, name="theirs")
        self.trans.sa_session.add_all([mine, theirs])
        self.trans.sa_session.commit()
        assert credential_permits_history(mine, credential)
        assert not credential_permits_history(theirs, credential)
        assert not credential_permits_history(None, credential)
        listed = Credential(kind="token", scopes=frozenset(), constraints={"histories": {"ids": [theirs.id]}})
        assert credential_permits_history(theirs, listed)
        assert not credential_permits_history(mine, listed)
        assert credential_permits_history(theirs, Credential(kind="token", scopes=frozenset()))
        assert credential_permits_history(theirs, None)

    def test_user_manager_resolves_tokens_wherever_a_key_goes(self):
        token, secret = self.manager.mint_token(self.user, "t", "*:read")
        for api_key, access_token in ((secret, None), (None, secret)):
            user, credential = self.user_manager.resolve_credential(api_key, access_token)
            assert user == self.user and credential is not None and credential.token_id == token.id
        assert self.user_manager.by_credentials(secret, None) == self.user

    def test_user_manager_keeps_the_legacy_key_unscoped(self):
        from galaxy.managers.api_keys import ApiKeyManager

        key = ApiKeyManager(self.app).create_api_key(self.user).key
        user, credential = self.user_manager.resolve_credential(key, None)
        assert user == self.user
        assert credential is not None and credential.kind == "legacy_key" and not credential.scoped
        assert self.user_manager.resolve_credential(None, None) == (None, None)


def test_every_agent_operation_declares_a_scope():
    """Widening an MCP op's scope is a reviewed diff: this pins the map."""
    import inspect

    from galaxy.agents.operations import AgentOperationsManager
    from galaxy.managers.api_tokens import PUBLIC_SCOPE

    expected = {
        "connect": "account:read",
        "search_tools": "tools:read",
        "get_tool_details": "tools:read",
        "list_histories": "histories:read",
        "run_tool": "tools:run",
        "get_job_status": "jobs:read",
        "create_history": "histories:write",
        "get_history_details": "histories:read",
        "get_history_contents": "histories:read",
        "get_history_graph": "histories:read",
        "get_dataset_details": "datasets:read",
        "get_collection_details": "datasets:read",
        "upload_file_from_url": "datasets:write",
        "list_workflows": "workflows:read",
        "get_workflow_details": "workflows:read",
        "invoke_workflow": "workflows:run",
        "get_invocations": "invocations:read",
        "get_invocation_details": "invocations:read",
        "cancel_workflow_invocation": "invocations:write",
        "get_tool_panel": "tools:read",
        "get_tool_run_examples": "tools:read",
        "get_tool_citations": "tools:read",
        "search_tools_by_keywords": "tools:read",
        "list_history_ids": "histories:read",
        "get_job_details": "jobs:read",
        "get_job_errors": "jobs:read",
        "peek_dataset_content": "datasets:read",
        "download_dataset": "datasets:read",
        "get_server_info": PUBLIC_SCOPE,
        "search_iwc_workflows": "workflows:read",
        "get_iwc_workflow_details": "workflows:read",
        "import_workflow_from_iwc": "workflows:write",
        "get_user": "account:read",
        "list_user_tools": None,
        "create_user_tool": None,
        "delete_user_tool": None,
        "run_user_tool": None,
        "list_file_source_templates": None,
        "list_user_file_sources": None,
        "list_pages": "pages:read",
        "get_page": "pages:read",
        "create_page": "pages:write",
        "update_page": "pages:write",
        "list_page_revisions": "pages:read",
        "get_page_revision": "pages:read",
        "revert_page_revision": "pages:write",
    }
    actual = {}
    for name, member in inspect.getmembers(AgentOperationsManager):
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        assert hasattr(member, "galaxy_scope"), f"AgentOperationsManager.{name} declares no scope"
        actual[name] = member.galaxy_scope
    assert actual == expected


class TestConstraintsThroughManagers(BaseTestCase):
    """Constraints hold where objects are loaded, whatever route got there."""

    def set_up_managers(self):
        super().set_up_managers()
        from galaxy.managers.hdas import HDAManager
        from galaxy.managers.histories import HistoryManager
        from galaxy.schema.history import HistoryIndexQueryPayload

        self.HistoryIndexQueryPayload = HistoryIndexQueryPayload
        self.history_manager = self.app[HistoryManager]
        self.hda_manager = self.app[HDAManager]
        self.manager = ApiTokenManager(self.app)
        self.user = self.user_manager.create(**user2_data)
        token, _ = self.manager.mint_token(
            self.user, "sandbox", PRESETS["run-jobs"], constraints={"histories": {"created_by_token": True}}
        )
        self.credential = self.manager.credential_for(token)
        self.mine = self.history_manager.create(user=self.user, name="mine")
        self.mine.api_token_id = token.id
        self.theirs = self.history_manager.create(user=self.user, name="theirs")
        self.trans.sa_session.commit()

    def set_up_trans(self):
        super().set_up_trans()
        self.trans.set_user(self.user)
        self.mock_trans.user_is_admin = False

    def _with_credential(self, credential):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            token = set_current_credential(credential)
            try:
                yield
            finally:
                reset_current_credential(token)

        return scope()

    def test_history_ownership_and_access_follow_the_constraint(self):
        with self._with_credential(self.credential):
            assert self.history_manager.is_owner(self.mine, self.user)
            assert not self.history_manager.is_owner(self.theirs, self.user)
            assert self.history_manager.is_accessible(self.mine, self.user)
            assert not self.history_manager.is_accessible(self.theirs, self.user)
            # loading by id goes through the filtered query, so the history doesn't exist for the token
            with pytest.raises(exceptions.ObjectNotFound):
                self.history_manager.get_accessible(self.theirs.id, self.user)
        # the same calls with no credential are unchanged
        assert self.history_manager.is_owner(self.theirs, self.user)

    def test_admin_users_are_constrained_too(self):
        self.trans.set_user(self.admin_user)
        with self._with_credential(self.credential):
            assert not self.history_manager.is_owner(self.theirs, self.admin_user)
            assert not self.history_manager.is_accessible(self.theirs, self.admin_user)

    def test_hdas_inherit_their_history(self):

        own_hda = self.hda_manager.create(history=self.mine, dataset=None)
        other_hda = self.hda_manager.create(history=self.theirs, dataset=None)
        with self._with_credential(self.credential):
            assert self.hda_manager.is_owner(own_hda, self.user)
            assert not self.hda_manager.is_owner(other_hda, self.user)
            assert not self.hda_manager.is_accessible(other_hda, self.user)

    def test_history_listing_is_filtered(self):
        payload = self.HistoryIndexQueryPayload(show_own=True)
        with self._with_credential(self.credential):
            listed, _ = self.history_manager.index_query(self.trans, payload)
            assert [h.id for h in listed] == [self.mine.id]
        listed, _ = self.history_manager.index_query(self.trans, payload)
        assert {h.id for h in listed} == {self.mine.id, self.theirs.id}

    def test_invocations_honour_the_history_constraint(self):
        from galaxy.managers.workflows import WorkflowsManager

        stored = model.StoredWorkflow(user=self.user, name="wf")
        workflow = model.Workflow()
        workflow.stored_workflow = stored
        stored.latest_workflow = workflow
        inside = model.WorkflowInvocation()
        inside.workflow = workflow
        inside.history = self.mine
        outside = model.WorkflowInvocation()
        outside.workflow = workflow
        outside.history = self.theirs
        self.trans.sa_session.add_all([stored, workflow, inside, outside])
        self.trans.sa_session.commit()
        manager = self.app[WorkflowsManager]
        with self._with_credential(self.credential):
            assert manager.get_invocation(self.trans, inside.id) == inside
            with pytest.raises(exceptions.ItemAccessibilityException):
                manager.get_invocation(self.trans, outside.id)
            listed, total = manager.build_invocations_query(self.trans, user_id=self.user.id)
            assert [i.id for i in listed] == [inside.id]
            assert total == 1
        listed, total = manager.build_invocations_query(self.trans, user_id=self.user.id)
        assert {i.id for i in listed} == {inside.id, outside.id}
        assert total == 2

    def test_workflow_inputs_outside_the_constraint_are_refused(self):
        from galaxy.tool_util_models.parameters import DataRequestHda
        from galaxy.workflow.run_request import _get_input_or_not_found

        own_hda = self.hda_manager.create(history=self.mine, dataset=None)
        other_hda = self.hda_manager.create(history=self.theirs, dataset=None)
        step = model.WorkflowStep()
        step.label = "input1"
        with self._with_credential(self.credential):
            request = DataRequestHda(src="hda", id=self.trans.security.encode_id(own_hda.id))
            assert _get_input_or_not_found(self.trans, model.HistoryDatasetAssociation, request, step) == own_hda
            request = DataRequestHda(src="hda", id=self.trans.security.encode_id(other_hda.id))
            with pytest.raises(exceptions.ItemAccessibilityException):
                _get_input_or_not_found(self.trans, model.HistoryDatasetAssociation, request, step)
        assert _get_input_or_not_found(self.trans, model.HistoryDatasetAssociation, request, step) == other_hda

    def test_histories_made_for_a_workflow_run_belong_to_the_token(self):
        from galaxy.workflow.run_request import _get_target_history

        workflow = model.Workflow()
        workflow.name = "wf"
        with self._with_credential(self.credential):
            history = _get_target_history(self.trans, workflow, {"new_history_name": "from the run"}, [[]])
        assert history.api_token_id == self.credential.token_id
        assert _get_target_history(self.trans, workflow, {"new_history_name": "plain"}, [[]]).api_token_id is None

    def test_workflow_constraint_through_the_manager(self):
        from galaxy.managers.workflows import WorkflowsManager

        allowed = model.StoredWorkflow(user=self.user, name="allowed")
        other = model.StoredWorkflow(user=self.user, name="other")
        self.trans.sa_session.add_all([allowed, other])
        self.trans.sa_session.commit()
        token, _ = self.manager.mint_token(
            self.user, "wf", "workflows:run", constraints={"workflows": {"ids": [allowed.id]}}
        )
        manager = self.app[WorkflowsManager]
        with self._with_credential(self.manager.credential_for(token)):
            assert manager.get_stored_workflow(self.trans, allowed.id) == allowed
            with pytest.raises(exceptions.ItemAccessibilityException):
                manager.get_stored_workflow(self.trans, other.id)
        assert manager.get_stored_workflow(self.trans, other.id) == other

    def test_undeclared_agent_operation_is_rejected_at_class_creation(self):
        from galaxy.agents.operations import _assert_every_operation_declares_a_scope

        class Sloppy:
            def do_it(self):
                pass

        with pytest.raises(TypeError, match="do_it"):
            _assert_every_operation_declares_a_scope(Sloppy)

    def test_scoped_credential_without_admin_scope_is_not_admin(self):
        from galaxy.work.context import WorkRequestContext

        admin_token, _ = self.manager.mint_token(self.admin_user, "root", "admin:*")
        plain_token, _ = self.manager.mint_token(self.admin_user, "plain", "*:*")
        for token, expected in ((admin_token, True), (plain_token, False)):
            trans = WorkRequestContext(
                app=self.app, user=self.admin_user, credential=self.manager.credential_for(token)
            )
            assert trans.user_is_admin is expected
        assert WorkRequestContext(app=self.app, user=self.admin_user).user_is_admin
