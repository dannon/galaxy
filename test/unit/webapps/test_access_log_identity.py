import inspect
import logging
from types import SimpleNamespace

import pytest
from fastapi import (
    Depends,
    FastAPI,
)
from fastapi.security import APIKeyCookie
from fastapi.testclient import TestClient
from sqlalchemy.orm import make_transient_to_detached

from galaxy import model
from galaxy.schema.fields import Security as IdSecurity
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.webapps.base.api import (
    add_exception_handler,
    add_raw_context_middlewares,
    RequestIdentity,
)
from galaxy.webapps.galaxy.api import (
    get_api_user,
    get_required_user,
    get_session,
    get_user,
)

API_KEY = "secret-api-key-0123456789"
BEARER_TOKEN = "secret-bearer-token-0123456789"
SESSION_COOKIE = "secret-session-cookie-0123456789"
ANONYMOUS_SESSION_COOKIE = "secret-anonymous-session-0123456789"
SECRETS = (API_KEY, BEARER_TOKEN, SESSION_COOKIE, ANONYMOUS_SESSION_COOKIE)
ACCESS_LOGGER = "galaxy.webapps.base.api"


def detached_user(user_id):
    user = model.User(email=f"user{user_id}@example.org", password="password")
    user.id = user_id
    make_transient_to_detached(user)
    return user


SESSION_USER = detached_user(3)
API_USER = detached_user(1)
TARGET_USER = detached_user(2)

security = IdEncodingHelper(id_secret="access-log-test")


class FakeUserManager:
    allow_run_as = True

    def by_api_key(self, api_key):
        assert api_key == API_KEY
        return API_USER

    def by_oidc_access_token(self, access_token):
        return API_USER if access_token == BEARER_TOKEN else None

    def user_can_do_run_as(self, user):
        return self.allow_run_as

    def by_id(self, user_id):
        return {2: TARGET_USER}.get(user_id)


user_manager = FakeUserManager()


def fake_get_session(galaxysession: str = Depends(APIKeyCookie(name="galaxysession", auto_error=False))):
    if galaxysession == SESSION_COOKIE:
        return SimpleNamespace(user=SESSION_USER)
    if galaxysession == ANONYMOUS_SESSION_COOKIE:
        return SimpleNamespace(user=None)
    return None


app = FastAPI()
add_raw_context_middlewares(app)
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


@app.get("/async-user")
async def read_user_async(user=Depends(get_user)):
    return {"user": user.id if user else None}


@app.get("/user-then-required")
def read_user_then_required(user=Depends(get_user), required_user=Depends(get_required_user)):
    return {"user": user.id if user else None, "required": required_user.id}


@app.get("/required-then-user")
def read_required_then_user(required_user=Depends(get_required_user), user=Depends(get_user)):
    return {"user": user.id if user else None, "required": required_user.id}


@app.get("/static")
def read_static():
    return {}


@pytest.fixture
def client(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(IdSecurity, "security", security, raising=False)
    user_manager.allow_run_as = True
    with TestClient(app) as test_client:
        yield test_client


def _identity_fields(caplog):
    """Return what follows the status on the request's completion line."""
    lines = [r.getMessage() for r in caplog.records if r.name == ACCESS_LOGGER]
    start, end = lines
    assert end.startswith(f"{start} ")
    return end[len(start) + 1 :].partition(" ")[2]


def _assert_no_secrets(caplog):
    # The test client's own httpx logger records what it sent; only Galaxy's lines matter.
    for record in caplog.records:
        if record.name.startswith("galaxy"):
            assert not any(secret in record.getMessage() for secret in SECRETS)


@pytest.mark.parametrize(
    "path,kwargs,expected",
    [
        ("/user", {"cookies": {"galaxysession": SESSION_COOKIE}}, "auth=session user=3"),
        (
            "/user",
            {"cookies": {"galaxysession": SESSION_COOKIE}, "headers": {"x-api-key": API_KEY}},
            "auth=session user=3",
        ),
        ("/user", {"cookies": {"galaxysession": ANONYMOUS_SESSION_COOKIE}}, "auth=anonymous"),
        ("/user", {"headers": {"x-api-key": API_KEY}}, "auth=api_key user=1"),
        ("/async-user", {"headers": {"x-api-key": API_KEY}}, "auth=api_key user=1"),
        ("/required", {"headers": {"Authorization": f"Bearer {BEARER_TOKEN}"}}, "auth=bearer user=1"),
        ("/user", {"headers": {"Authorization": "Bearer unknown-token"}}, "auth=anonymous"),
        ("/user", {}, "auth=anonymous"),
        ("/required", {}, "auth=anonymous"),
        ("/static", {"headers": {"x-api-key": API_KEY}}, ""),
    ],
)
def test_identity_fields(client, caplog, path, kwargs, expected):
    client.get(path, **kwargs)
    assert _identity_fields(caplog) == expected
    _assert_no_secrets(caplog)


@pytest.mark.parametrize(
    "path,run_as,cookie,expected",
    [
        ("/required", 2, None, "auth=api_key user=2 real_user=1"),
        ("/user", 99, None, "auth=api_key user=anonymous real_user=1"),
        ("/user", 2, SESSION_COOKIE, "auth=session user=3"),
        ("/required", 2, ANONYMOUS_SESSION_COOKIE, "auth=api_key user=2 real_user=1"),
    ],
)
def test_run_as_identity(client, caplog, path, run_as, cookie, expected):
    cookies = {"galaxysession": cookie} if cookie else None
    client.get(path, headers={"x-api-key": API_KEY, "run-as": security.encode_id(run_as)}, cookies=cookies)
    assert _identity_fields(caplog) == expected
    _assert_no_secrets(caplog)


@pytest.mark.parametrize("path", ["/user-then-required", "/required-then-user"])
def test_api_user_wins_over_anonymous_session_on_a_route_using_both_dependencies(client, caplog, path):
    # get_user is anonymous here but get_required_user acts as the API user, so the
    # line names the API user whichever dependency resolves last.
    response = client.get(path, headers={"x-api-key": API_KEY}, cookies={"galaxysession": ANONYMOUS_SESSION_COOKIE})
    assert response.json() == {"user": None, "required": 1}
    assert _identity_fields(caplog) == "auth=api_key user=1"


def test_refused_run_as_carries_no_identity(client, caplog):
    user_manager.allow_run_as = False
    response = client.get("/required", headers={"x-api-key": API_KEY, "run-as": security.encode_id(2)})
    assert response.status_code == 403
    assert _identity_fields(caplog) == ""


@pytest.mark.parametrize(
    "identity,expected",
    [
        (RequestIdentity("anonymous"), "auth=anonymous"),
        (RequestIdentity("session", user="5"), "auth=session user=5"),
        (RequestIdentity("api_key", user="7", real_user="5"), "auth=api_key user=7 real_user=5"),
    ],
)
def test_access_log_fields(identity, expected):
    assert identity.access_log_fields() == expected
