import json
from types import SimpleNamespace
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import pytest
import requests
import responses

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.login import (
    DirectRasLogin,
    LOGIN_REQUEST_LIFETIME,
    LOGIN_REQUEST_VAULT_KEY,
    PassportLoginError,
)
from galaxy.authnz.ras.sources import (
    DirectRasPassportSource,
    PassportSourceError,
)
from galaxy.authnz.ras.store import InMemoryVault
from galaxy.security.vault import UserVaultWrapper
from .util import (
    CLIENT_ID,
    CLIENT_SECRET,
    CountingFetcher,
    DISCOVERY,
    FakeHttp,
    id_token,
    ISSUER,
    jwks_doc,
    KEY,
    KID,
    make_passport,
    NOW,
    permission,
    REDIRECT_URI,
    SUBJECT,
)

CALLBACK = "https://callback.example/cb"


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _setup(http=None):
    passport = make_passport([permission()])
    http = http or FakeHttp(userinfo={"sub": SUBJECT, "passport_jwt_v11": passport})
    vault = InMemoryVault()
    clock = Clock()
    jwks = JwksClient(DISCOVERY["jwks_uri"], fetcher=CountingFetcher(jwks_doc((KEY, KID))))
    source = DirectRasPassportSource(
        ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, http=http, jwks=jwks, clock=lambda: NOW
    )
    login = DirectRasLogin(source, vault, clock=clock)
    return SimpleNamespace(
        login=login, vault=vault, clock=clock, user=SimpleNamespace(id=7), http=http, passport=passport
    )


def _saved(env) -> dict:
    raw = UserVaultWrapper(env.vault, env.user).read_secret(LOGIN_REQUEST_VAULT_KEY)
    assert raw is not None
    return json.loads(raw)


def _start(env) -> dict:
    env.login.start(env.user)
    return _saved(env)


def _callback(state: str, code: str = "code-1") -> str:
    return f"{CALLBACK}?code={code}&state={state}"


def _nonce_token(env, saved):
    # The fake token response is built before the nonce is known, so point it at the saved one.
    env.http.token_response = {"access_token": "access-1", "id_token": id_token(nonce=saved["nonce"])}


def test_start_saves_request_and_returns_authorize_url():
    env = _setup()
    url = env.login.start(env.user)
    saved = _saved(env)
    assert url.startswith(DISCOVERY["authorization_endpoint"])
    assert set(saved) == {"state", "nonce", "created"}
    assert saved["created"] == NOW
    assert parse_qs(urlsplit(url).query)["state"] == [saved["state"]]


def test_finish_returns_the_passport_without_storing_it():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    assert env.login.finish(env.user, _callback(saved["state"])) == env.passport
    # The pending login is consumed; the passport itself is the manager's to verify and store.
    assert not any(env.vault.secrets.values())  # the vault deletes by writing ""


def test_finish_is_single_use():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    url = _callback(saved["state"])
    env.login.finish(env.user, url)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, url)


def test_finish_rejects_state_mismatch():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback("not-the-state"))
    assert not UserVaultWrapper(env.vault, env.user).read_secret(LOGIN_REQUEST_VAULT_KEY)


def test_finish_rejects_expired_request():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    env.clock.now += LOGIN_REQUEST_LIFETIME + 1
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback(saved["state"]))


def test_finish_rejects_bare_code():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, "code-1")


def test_finish_without_start():
    env = _setup()
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback("whatever"))


def test_finish_reports_ras_error():
    env = _setup()
    saved = _start(env)
    with pytest.raises(PassportLoginError, match="access_denied"):
        env.login.finish(env.user, f"{CALLBACK}?error=access_denied&state={saved['state']}")


def test_finish_rejects_unparseable_url():
    env = _setup()
    _start(env)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, "https://[")


def test_finish_reports_ras_error_without_state():
    env = _setup()
    _start(env)
    with pytest.raises(PassportLoginError, match="access_denied"):
        env.login.finish(env.user, f"{CALLBACK}?error=access_denied")


@pytest.mark.parametrize(
    "raw_saved",
    ["not json", json.dumps({"state": "s", "nonce": "n"}), json.dumps(["s", "n"]), json.dumps({"created": "x"})],
)
def test_finish_rejects_corrupt_saved_request(raw_saved):
    env = _setup()
    UserVaultWrapper(env.vault, env.user).write_secret(LOGIN_REQUEST_VAULT_KEY, raw_saved)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback("s"))
    assert not UserVaultWrapper(env.vault, env.user).read_secret(LOGIN_REQUEST_VAULT_KEY)


class FailingDiscoveryHttp(FakeHttp):
    def get_json(self, url, bearer=None):
        if url.endswith("/.well-known/openid-configuration"):
            return 503, {}
        return super().get_json(url, bearer)


def test_start_reports_discovery_failure():
    env = _setup(FailingDiscoveryHttp())
    with pytest.raises(PassportLoginError):
        env.login.start(env.user)
    assert not UserVaultWrapper(env.vault, env.user).read_secret(LOGIN_REQUEST_VAULT_KEY)


def _requests_login() -> SimpleNamespace:
    # The real HTTP client, so network failures come from requests itself.
    vault = InMemoryVault()
    jwks = JwksClient(DISCOVERY["jwks_uri"], fetcher=CountingFetcher(jwks_doc((KEY, KID))))
    source = DirectRasPassportSource(ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, jwks=jwks, clock=lambda: NOW)
    login = DirectRasLogin(source, vault, clock=Clock())
    return SimpleNamespace(login=login, vault=vault, user=SimpleNamespace(id=7))


DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"


@responses.activate
def test_start_reports_network_failure():
    responses.add(responses.GET, DISCOVERY_URL, body=requests.ConnectionError("RAS is down"))
    env = _requests_login()
    with pytest.raises(PassportLoginError):
        env.login.start(env.user)


@responses.activate
def test_finish_reports_network_failure_during_exchange():
    responses.add(responses.GET, DISCOVERY_URL, json=DISCOVERY)
    responses.add(responses.POST, DISCOVERY["token_endpoint"], body=requests.Timeout("too slow"))
    env = _requests_login()
    env.login.start(env.user)
    saved = _saved(env)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback(saved["state"]))


def test_requests_client_wraps_network_errors():
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, DISCOVERY_URL, body=requests.ConnectionError("RAS is down"))
        source = DirectRasPassportSource(ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
        with pytest.raises(PassportSourceError):
            source.discovery()


def test_second_start_replaces_the_first():
    env = _setup()
    first = _start(env)
    second = _start(env)
    assert second["state"] != first["state"]
    assert _saved(env) == second
    _nonce_token(env, first)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback(first["state"]))
