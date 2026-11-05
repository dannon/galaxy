"""The route-level scope check that FrameworkRouter attaches to every Galaxy route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from galaxy.managers.api_tokens import (
    Credential,
    current_credential,
    UNSCOPED_LEGACY_KEY,
)
from galaxy.webapps.base.api import add_exception_handler
from galaxy.webapps.galaxy.api import (
    get_api_credential,
    get_session,
    Router,
)

router = Router(tags=["test"])
STATE: dict = {"credential": None, "seen": None}


def fake_api_credential():
    return None, STATE["credential"]


def fake_session():
    return None


@router.get("/annotated", scope="histories:read")
def annotated():
    STATE["seen"] = current_credential()
    return {"ok": True}


@router.get("/annotated_async", scope="histories:write")
async def annotated_async():
    STATE["seen"] = current_credential()
    return {"ok": True}


@router.get("/unannotated")
def unannotated():
    return {"ok": True}


@router.get("/open", public=True)
def open_route():
    return {"ok": True}


@router.get("/admin", require_admin=True)
def admin_route():
    return {"ok": True}


app = FastAPI()
add_exception_handler(app)
app.include_router(router)
app.dependency_overrides[get_api_credential] = fake_api_credential
app.dependency_overrides[get_session] = fake_session
client = TestClient(app)


def _as(credential):
    STATE["credential"] = credential
    STATE["seen"] = "unset"


def test_unscoped_credentials_pass_everywhere():
    for credential in (None, UNSCOPED_LEGACY_KEY):
        _as(credential)
        for path in ("/annotated", "/annotated_async", "/unannotated"):
            assert client.get(path).status_code == 200, path


def test_scoped_credential_needs_the_named_scope():
    _as(Credential(kind="token", scopes=frozenset({"histories:read"})))
    assert client.get("/annotated").status_code == 200
    response = client.get("/annotated_async")
    assert response.status_code == 403
    assert "histories:write" in response.json()["err_msg"]
    assert "GET /annotated_async" in response.json()["err_msg"]


def test_scoped_credential_is_refused_on_unannotated_routes():
    _as(Credential(kind="token", scopes=frozenset({"*:*"})))
    response = client.get("/unannotated")
    assert response.status_code == 403
    assert "not available to scoped access tokens" in response.json()["err_msg"]
    assert client.get("/open").status_code == 200


def test_contextvar_reaches_sync_and_async_endpoints():
    credential = Credential(kind="token", scopes=frozenset({"histories:write"}))
    _as(credential)
    client.get("/annotated")
    assert STATE["seen"] == credential
    client.get("/annotated_async")
    assert STATE["seen"] == credential
    assert current_credential() is None
