"""Scoped API token minting, resolution and the scope/constraint grammar."""

from datetime import timedelta
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
    scopes_allow,
    set_current_credential,
    TOKEN_PREFIX,
    validate_constraints,
)
from galaxy.model.orm.now import now
from .base import BaseTestCase

user2_data = dict(email="user2@user2.user2", username="user2", password="123456")


class FakeTool:
    def __init__(self, tool_id: str, old_id: str | None = None):
        self.id = tool_id
        self.old_id = old_id or tool_id


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
    assert validate_constraints({"histories": {}, "tools": {"ids": []}}) is None
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
        assert current_credential().allows("tools:read")
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
        assert token.expires_at - now() < timedelta(hours=1, seconds=5)
        user, credential = self.manager.resolve(secret)
        assert user == self.user
        assert credential.scoped and credential.token_id == token.id
        assert credential.allows("histories:read") and not credential.allows("histories:share")
        assert token.last_used_at is not None

    def test_mint_caps_the_lifetime(self):
        with patch.object(self.app.config, "api_token_max_lifetime", 2, create=True):
            token, _ = self.manager.mint_token(self.user, "long", "*:read", expires_in=10 * 24 * 3600)
            assert token.expires_at - now() <= timedelta(days=2)
            token, _ = self.manager.mint_token(self.user, "default", "*:read")
            assert token.expires_at - now() <= timedelta(days=2)
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
        assert user == self.user and credential.surfaces == {"mcp"}
        _, plain = self.manager.mint_token(self.user, "anywhere", PRESETS["agent"])
        assert self.manager.resolve(plain, surface="api")[1].surfaces is None

    def test_revoke_only_own_tokens(self):
        token, _ = self.manager.mint_token(self.user, "t", "*:read")
        with pytest.raises(exceptions.ObjectNotFound):
            self.manager.revoke(self.admin_user, token.id)
        assert not token.revoked
        self.manager.revoke(self.user, token.id)
        assert token.revoked
        assert self.manager.tokens_for(self.user) == []
        assert self.manager.tokens_for(self.user, include_inactive=True) == [token]

    def test_last_used_is_written_sparingly(self):
        token, secret = self.manager.mint_token(self.user, "t", "*:read")
        self.manager.resolve(secret)
        first = token.last_used_at
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
            assert user == self.user and credential.token_id == token.id
        assert self.user_manager.by_credentials(secret, None) == self.user

    def test_user_manager_keeps_the_legacy_key_unscoped(self):
        from galaxy.managers.api_keys import ApiKeyManager

        key = ApiKeyManager(self.app).create_api_key(self.user).key
        user, credential = self.user_manager.resolve_credential(key, None)
        assert user == self.user
        assert credential is not None and credential.kind == "legacy_key" and not credential.scoped
        assert self.user_manager.resolve_credential(None, None) == (None, None)
