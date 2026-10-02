"""Legacy WSGI requests record the same RequestIdentity the FastAPI dependencies do."""

from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import make_transient_to_detached

from galaxy import model
from galaxy.schema import BootstrapAdminUser
from galaxy.web.framework.middleware.request_id import RequestIDMiddleware
from galaxy.web.framework.request_scope import (
    current_request_identity,
    request_scope,
    RequestIdentity,
)
from galaxy.webapps.base.webapp import GalaxyWebTransaction

API_KEY = "secret-api-key-0123456789"


def detached(instance, instance_id):
    instance.id = instance_id
    make_transient_to_detached(instance)
    return instance


def user_with_key(user_id, key_id):
    key = model.APIKeys(key=API_KEY)
    user = model.User(email=f"user{user_id}@example.org")
    user.api_keys = [key]
    detached(key, key_id)
    return detached(user, user_id)


API_USER = user_with_key(1, 50)
TARGET_USER = detached(model.User(email="target@example.org"), 2)


def legacy_trans(user_for_key: Any = API_USER, params=None, headers=None, use_remote_user=False) -> Any:
    """A GalaxyWebTransaction with only the state _authenticate_api reads; its methods are the real ones."""
    trans: Any = object.__new__(GalaxyWebTransaction)
    trans._app = SimpleNamespace(
        config=SimpleNamespace(use_remote_user=use_remote_user, remote_user_header="HTTP_REMOTE_USER")
    )
    trans.environ = {"is_api_request": True}
    trans.request = SimpleNamespace(params=params or {}, headers=headers or {})
    trans.response = SimpleNamespace(cookies={})
    trans.galaxy_session = None
    trans._GalaxyWebTransaction__user = None
    trans.user_manager = SimpleNamespace(
        by_api_key=lambda api_key: user_for_key, by_oidc_access_token=lambda token: user_for_key
    )
    trans.get_cookie = lambda name="galaxysession": None
    return trans


def test_api_key_request_records_the_key_owner_and_key():
    trans = legacy_trans(params={"key": API_KEY})
    with request_scope():
        assert trans._authenticate_api("galaxysession") is None
        assert current_request_identity() == RequestIdentity("api_key", 1, actor_id=1, credential_id=50)


def test_bearer_request_records_the_token_owner():
    trans = legacy_trans(headers={"Authorization": "Bearer some-token"})
    with request_scope():
        trans._authenticate_api("galaxysession")
        assert current_request_identity() == RequestIdentity("bearer", 1, actor_id=1)


def test_bootstrap_admin_key_has_no_user_id():
    trans = legacy_trans(user_for_key=BootstrapAdminUser(), params={"key": API_KEY})
    with request_scope():
        trans._authenticate_api("galaxysession")
        assert current_request_identity() == RequestIdentity("bootstrap_api_key")


def test_request_without_credentials_is_anonymous():
    trans = legacy_trans()
    with request_scope():
        trans._authenticate_api("galaxysession")
        assert current_request_identity() == RequestIdentity("anonymous")


def test_clearing_the_user_is_not_a_run_as_switch():
    # OIDC reauthentication and session expiry clear the user; only the run_as
    # decorators switch identity.
    trans = legacy_trans(params={"key": API_KEY})
    with request_scope():
        trans._authenticate_api("galaxysession")
        trans.set_user(None)
        assert current_request_identity() == RequestIdentity("api_key", 1, actor_id=1, credential_id=50)


def test_session_identity_says_remote_user_when_configured():
    trans = legacy_trans(use_remote_user=True)
    galaxy_session = model.GalaxySession(user=TARGET_USER, impersonated_by_user_id=None)
    trans.galaxy_session = detached(galaxy_session, 9)
    with request_scope():
        trans._note_session_identity()
        assert current_request_identity() == RequestIdentity("remote_user", 2, actor_id=2, credential_id=9)


def test_identity_is_not_recorded_outside_a_request_scope():
    trans = legacy_trans(params={"key": API_KEY})
    trans._authenticate_api("galaxysession")
    assert current_request_identity() is None


def _environ_request_id(environ, start_response):
    return [environ["request_id"]]


def test_legacy_request_id_is_the_asgi_request_id():
    middleware = RequestIDMiddleware(_environ_request_id)
    with request_scope(request_id="0123456789abcdef0123456789abcdef"):
        assert middleware({}, None) == ["0123456789abcdef0123456789abcdef"]


def test_legacy_request_id_without_asgi_is_fresh():
    middleware = RequestIDMiddleware(_environ_request_id)
    first, second = middleware({}, None)[0], middleware({}, None)[0]
    assert first != second
    assert len(first) == 32
