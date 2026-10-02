import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import make_transient_to_detached
from sqlalchemy.orm.exc import DetachedInstanceError

from galaxy import model
from galaxy.web.framework.request_scope import (
    api_key_id,
    current_request_identity,
    current_request_scope,
    MAX_USER_AGENT_LENGTH,
    model_id,
    request_scope,
    RequestIdentity,
    session_identity,
    set_request_identity,
)
from galaxy.webapps.base.api import (
    add_raw_context_middlewares,
    add_request_id_middleware,
)


def detached(instance, instance_id):
    instance.id = instance_id
    make_transient_to_detached(instance)
    return instance


def detached_session(session_id, user, impersonated_by_user_id=None):
    galaxy_session = model.GalaxySession(user=user, impersonated_by_user_id=impersonated_by_user_id)
    return detached(galaxy_session, session_id)


def test_session_identity_names_the_session_user():
    user = detached(model.User(email="alice@example.org"), 7)
    assert session_identity(detached_session(40, user)) == RequestIdentity("session", 7, actor_id=7, credential_id=40)


def test_session_identity_names_the_impersonating_admin_as_actor():
    user = detached(model.User(email="alice@example.org"), 7)
    assert session_identity(detached_session(41, user, impersonated_by_user_id=1)) == RequestIdentity(
        "session", 7, actor_id=1, switch="impersonation", credential_id=41
    )


def test_session_identity_for_an_anonymous_session_keeps_the_session_id():
    assert session_identity(detached_session(42, None)) == RequestIdentity("anonymous", credential_id=42)


def test_session_identity_can_say_remote_user():
    user = detached(model.User(email="alice@example.org"), 7)
    identity = session_identity(detached_session(43, user), auth_method="remote_user")
    assert identity.auth_method == "remote_user"


def test_model_id_reads_the_identity_key_without_loading():
    user = detached(model.User(email="alice@example.org"), 7)
    state = sa_inspect(user)
    state._expire(state.dict, set())
    # Expired and detached: reading user.id would need the database.
    with pytest.raises(DetachedInstanceError):
        _ = user.id
    assert model_id(user) == 7
    assert model_id(model.User(email="new@example.org")) is None
    assert model_id(None) is None
    assert model_id(object()) is None


def test_api_key_id_uses_the_loaded_newest_key():
    user = detached(model.User(email="alice@example.org"), 7)
    assert api_key_id(user) is None
    key = model.APIKeys(key="never-logged")
    with_key = model.User(email="bob@example.org")
    with_key.api_keys = [key]
    detached(key, 99)
    detached(with_key, 8)
    assert api_key_id(with_key) == 99
    assert api_key_id(object()) is None


def test_identity_needs_an_open_scope():
    set_request_identity(RequestIdentity("session", 1, actor_id=1))
    assert current_request_identity() is None


def test_identity_never_replaces_a_user_with_nobody():
    with request_scope():
        set_request_identity(RequestIdentity("api_key", 1, actor_id=1))
        set_request_identity(RequestIdentity("anonymous"))
        assert current_request_identity() == RequestIdentity("api_key", 1, actor_id=1)
        set_request_identity(RequestIdentity("session", 3, actor_id=3))
        assert current_request_identity() == RequestIdentity("session", 3, actor_id=3)
    assert current_request_scope() is None


def _scope_app(add_middleware):
    app = FastAPI()
    add_middleware(app)

    @app.get("/scope")
    def read_scope():
        scope = current_request_scope()
        assert scope is not None
        return {"request_id": scope.request_id, "remote_addr": scope.remote_addr, "user_agent": scope.user_agent}

    return app


def test_scope_shares_the_request_id_with_the_response_header():
    for add_middleware in (add_raw_context_middlewares, add_request_id_middleware):
        with TestClient(_scope_app(add_middleware)) as client:
            response = client.get("/scope", headers={"user-agent": "x" * (MAX_USER_AGENT_LENGTH + 10)})
        body = response.json()
        assert body["request_id"] == response.headers["X-Request-ID"]
        assert body["remote_addr"] == "testclient"
        assert body["user_agent"] == "x" * MAX_USER_AGENT_LENGTH
