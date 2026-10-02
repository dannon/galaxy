import inspect
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import (
    Depends,
    FastAPI,
)
from fastapi.security import APIKeyCookie
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import make_transient_to_detached

from galaxy import model
from galaxy.app_unittest_utils.galaxy_mock import MockApp
from galaxy.exceptions import MalformedId
from galaxy.schema.fields import Security as IdSecurity
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.web.framework.decorators import (
    expose_api,
    legacy_expose_api,
    user_log_id,
)
from galaxy.web.framework.request_scope import (
    current_request_identity,
    request_scope,
    RequestIdentity,
    set_request_identity,
)
from galaxy.webapps.base.api import (
    add_exception_handler,
    add_request_id_middleware,
)
from galaxy.webapps.galaxy.api import (
    get_api_user,
    get_required_user,
    get_session,
    get_user,
)

API_KEY = "secret-api-key-0123456789"
SESSION_COOKIE = "secret-session-cookie"
ANONYMOUS_SESSION_COOKIE = "secret-anonymous-session"
RUN_AS_LOGGER = "galaxy.web.framework.decorators"

security = IdEncodingHelper(id_secret="run-as-logging-test-secret")


def detached_user(user_id):
    # A real mapped user with an identity key, as the API sees one, but no database.
    user = model.User(email=f"user{user_id}@example.org", password="password")
    user.id = user_id
    make_transient_to_detached(user)
    return user


ADMIN = detached_user(1)
TARGET = detached_user(2)
SESSION_USER = detached_user(3)


def _messages(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == RUN_AS_LOGGER]


class FakeUserManager:
    allow_run_as = True
    principal: Any = ADMIN

    def by_api_key(self, api_key):
        assert api_key == API_KEY
        return self.principal

    def user_can_do_run_as(self, user):
        return self.allow_run_as

    def by_id(self, user_id):
        return {2: TARGET}.get(user_id)


user_manager = FakeUserManager()


def fake_get_session(galaxysession: str = Depends(APIKeyCookie(name="galaxysession", auto_error=False))):
    if galaxysession == SESSION_COOKIE:
        return SimpleNamespace(user=SESSION_USER, impersonated_by_user_id=None)
    if galaxysession == ANONYMOUS_SESSION_COOKIE:
        return SimpleNamespace(user=None, impersonated_by_user_id=None)
    return None


app = FastAPI()
add_request_id_middleware(app)
add_exception_handler(app)
app.dependency_overrides[inspect.signature(get_api_user).parameters["user_manager"].default.dependency] = (
    lambda: user_manager
)
app.dependency_overrides[get_session] = fake_get_session


@app.get("/user")
def read_user(user=Depends(get_user)):
    return {"user": user.id if user else None}


@app.get("/required")
def read_required_user(user=Depends(get_required_user)):
    return {"user": user.id}


@app.get("/both")
def read_both(user=Depends(get_user), required_user=Depends(get_required_user)):
    return {"user": user.id if user else None, "required": required_user.id}


@pytest.fixture
def client(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger=RUN_AS_LOGGER)
    monkeypatch.setattr(IdSecurity, "security", security, raising=False)
    user_manager.allow_run_as = True
    user_manager.principal = ADMIN
    with TestClient(app) as test_client:
        yield test_client


def _get(client, path, run_as=2, cookie=None):
    headers = {"x-api-key": API_KEY}
    if run_as is not None:
        headers["run-as"] = security.encode_id(run_as)
    cookies = {"galaxysession": cookie} if cookie else None
    return client.get(path, headers=headers, cookies=cookies)


@pytest.mark.parametrize("path", ["/user", "/required", "/both"])
def test_run_as_switch_is_logged_once(client, caplog, path):
    response = _get(client, path)
    assert response.json()["user"] == 2
    assert _messages(caplog) == [(logging.INFO, "User 1 is running as user 2 via run_as")]


def test_run_as_refused(client, caplog):
    user_manager.allow_run_as = False
    response = _get(client, "/user")
    assert response.status_code == 403
    assert _messages(caplog) == [(logging.WARNING, "Refused run_as by user 1 for user 2: not permitted")]


def test_run_as_by_bootstrap_admin(client, caplog):
    user_manager.principal = SimpleNamespace(id=0, bootstrap_admin_user=True)
    _get(client, "/user")
    assert _messages(caplog) == [(logging.INFO, "User bootstrap-admin is running as user 2 via run_as")]


def test_run_as_ignored_when_session_has_a_user(client, caplog):
    response = _get(client, "/required", cookie=SESSION_COOKIE)
    assert response.json() == {"user": 3}
    assert _messages(caplog) == [
        (logging.INFO, "Ignored run_as by user 1 for user 2: the request's session takes precedence (user 3)")
    ]


def test_run_as_with_anonymous_session_follows_each_dependency(client, caplog):
    # get_user prefers any session, get_required_user only one with a user;
    # the log has to report whichever one the route actually used.
    response = _get(client, "/user", cookie=ANONYMOUS_SESSION_COOKIE)
    assert response.json() == {"user": None}
    assert _messages(caplog) == [
        (
            logging.INFO,
            "Ignored run_as by user 1 for user 2: the request's session takes precedence (user anonymous)",
        )
    ]
    caplog.clear()
    response = _get(client, "/required", cookie=ANONYMOUS_SESSION_COOKIE)
    assert response.json() == {"user": 2}
    assert _messages(caplog) == [(logging.INFO, "User 1 is running as user 2 via run_as")]


@pytest.mark.parametrize("path,status", [("/user", 200), ("/required", 403)])
def test_run_as_missing_target(client, caplog, path, status):
    response = _get(client, path, run_as=99)
    assert response.status_code == status
    assert _messages(caplog) == [(logging.WARNING, "run_as by user 1 named user 99, which does not exist")]


def test_run_as_with_anonymous_session_on_a_route_using_both_dependencies(client, caplog):
    response = _get(client, "/both", cookie=ANONYMOUS_SESSION_COOKIE)
    assert response.json() == {"user": None, "required": 2}
    assert sorted(_messages(caplog)) == [
        (
            logging.INFO,
            "Ignored run_as by user 1 for user 2: the request's session takes precedence (user anonymous)",
        ),
        (logging.INFO, "User 1 is running as user 2 via run_as"),
    ]


def test_run_as_ignored_once_on_a_route_using_both_dependencies(client, caplog):
    _get(client, "/both", cookie=SESSION_COOKIE)
    assert len(_messages(caplog)) == 1


def test_no_run_as_logs_nothing(client, caplog):
    assert _get(client, "/user", run_as=None).json() == {"user": 1}
    assert _messages(caplog) == []


def test_get_api_user_without_request_context():
    user = get_api_user(
        user_manager=user_manager,  # type: ignore[arg-type]
        key=API_KEY,
        x_api_key=None,  # type: ignore[arg-type]
        bearer_token=None,  # type: ignore[arg-type]
        run_as=2,
    )
    assert user is TARGET


class FakeQuery:
    def __init__(self, users):
        self.users = users

    def get(self, user_id):
        if user_id == 7:
            raise Exception("lookup failed")
        return self.users.get(user_id)


class FakeTrans:
    def __init__(self, payload: Any, can_run_as: bool = True, users=None, user=ADMIN):
        self.error_message = None
        self.anonymous = user is None
        self.galaxy_session = None
        self.debug = False
        self.user = user
        self.user_can_do_run_as = can_run_as
        self.security = security
        self.request = SimpleNamespace(
            is_body_readable=True,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode(),
        )
        self.response = SimpleNamespace(headers={}, status=200, set_content_type=lambda content_type: None)
        self.sa_session = SimpleNamespace(query=lambda model_class: FakeQuery(users or {}))
        self.app = SimpleNamespace(model=SimpleNamespace(User=object))

    def set_user(self, user):
        self.user = user


def _endpoint(self, trans, payload=None, **kwargs):
    return {}


LEGACY_DECORATORS = [
    pytest.param(legacy_expose_api, id="legacy_expose_api"),
    pytest.param(expose_api, id="expose_api"),
]


@pytest.fixture
def legacy_caplog(caplog):
    caplog.set_level(logging.DEBUG, logger=RUN_AS_LOGGER)
    return caplog


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_switch(decorator, legacy_caplog):
    trans = FakeTrans({"run_as": security.encode_id(2)}, users={2: TARGET})
    decorator(_endpoint)(None, trans)
    assert trans.user is TARGET
    assert _messages(legacy_caplog) == [(logging.INFO, "User 1 is running as user 2 via run_as")]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_header_switch(decorator, legacy_caplog):
    trans = FakeTrans({}, users={2: TARGET})
    trans.request.headers["run-as"] = security.encode_id(2)
    decorator(_endpoint)(None, trans)
    assert _messages(legacy_caplog) == [(logging.INFO, "User 1 is running as user 2 via run_as")]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_refused(decorator, legacy_caplog):
    trans = FakeTrans({"run_as": security.encode_id(2)}, can_run_as=False)
    decorator(_endpoint)(None, trans)
    assert trans.user is ADMIN
    assert _messages(legacy_caplog) == [(logging.WARNING, "Refused run_as by user 1: not permitted")]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_refused_never_logs_request_text(decorator, legacy_caplog):
    trans = FakeTrans({"run_as": API_KEY}, can_run_as=False)
    decorator(_endpoint)(None, trans)
    assert _messages(legacy_caplog) == [(logging.WARNING, "Refused run_as by user 1: not permitted")]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_malformed(decorator, legacy_caplog):
    trans = FakeTrans({"run_as": API_KEY + "\nforged"})
    with pytest.raises(MalformedId):
        decorator(_endpoint)(None, trans)
    assert trans.user is ADMIN
    assert _messages(legacy_caplog) == [(logging.WARNING, "Refused run_as by user 1: malformed target id")]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_lookup_failure(decorator, legacy_caplog):
    trans = FakeTrans({"run_as": security.encode_id(7)})
    decorator(_endpoint)(None, trans)
    assert trans.user is ADMIN
    assert _messages(legacy_caplog) == [
        (logging.WARNING, "Refused run_as by user 1 for user 7: could not switch to the target user")
    ]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_missing_target(decorator, legacy_caplog):
    trans = FakeTrans({"run_as": security.encode_id(99)})
    decorator(_endpoint)(None, trans)
    assert _messages(legacy_caplog) == [(logging.WARNING, "run_as by user 1 named user 99, which does not exist")]


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_without_run_as_logs_nothing(decorator, legacy_caplog):
    decorator(_endpoint)(None, FakeTrans({"name": "value"}))
    assert _messages(legacy_caplog) == []


@pytest.fixture
def mock_session():
    return MockApp().model.session


@pytest.fixture
def no_sql(mock_session):
    queries = []

    def record(*args, **kwargs):
        queries.append(args)

    engine = mock_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    yield queries
    event.remove(engine, "before_cursor_execute", record)


def _persisted_user(session):
    user = model.User(email="logged@example.org", password="password", username="logged")
    session.add(user)
    session.commit()
    return user


def test_user_log_id_reads_expired_user_without_sql(mock_session, no_sql):
    user = _persisted_user(mock_session)
    user_id = user.id
    mock_session.expire(user)
    no_sql.clear()
    assert user_log_id(user) == str(user_id)
    assert no_sql == []


def test_user_log_id_reads_detached_expired_user(mock_session):
    user = _persisted_user(mock_session)
    user_id = user.id
    mock_session.expire(user)
    mock_session.expunge(user)
    assert user_log_id(user) == str(user_id)


def test_user_log_id_special_cases():
    assert user_log_id(None) == "anonymous"
    assert user_log_id(SimpleNamespace(id=0, bootstrap_admin_user=True)) == "bootstrap-admin"
    assert user_log_id(model.User(email="new@example.org", password="password")) == "unknown"
    assert user_log_id(ADMIN) == "1"
    assert user_log_id(SimpleNamespace(id=5)) == "unknown"


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_switch_updates_the_request_identity(decorator):
    trans = FakeTrans({"run_as": security.encode_id(2)}, users={2: TARGET})
    with request_scope():
        set_request_identity(RequestIdentity("api_key", 1, actor_id=1, credential_id=50))
        decorator(_endpoint)(None, trans)
        assert current_request_identity() == RequestIdentity(
            "api_key", 2, actor_id=1, switch="run_as", credential_id=50
        )


@pytest.mark.parametrize("decorator", LEGACY_DECORATORS)
def test_legacy_run_as_by_the_bootstrap_key_keeps_the_target(decorator):
    trans = FakeTrans({"run_as": security.encode_id(2)}, users={2: TARGET})
    with request_scope():
        set_request_identity(RequestIdentity("bootstrap_api_key"))
        decorator(_endpoint)(None, trans)
        assert current_request_identity() == RequestIdentity("bootstrap_api_key", 2, switch="run_as")
