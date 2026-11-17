import json
from types import SimpleNamespace
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import jwt
import pytest

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.login import (
    DirectRasLogin,
    LOGIN_REQUEST_LIFETIME,
    LOGIN_REQUEST_VAULT_KEY,
    PassportLoginError,
)
from galaxy.authnz.ras.sources import DirectRasPassportSource
from galaxy.authnz.ras.store import (
    InMemoryVault,
    PassportStatus,
    VaultPassportStore,
)
from galaxy.security.vault import UserVaultWrapper
from .util import (
    CLIENT_ID,
    CLIENT_SECRET,
    CountingFetcher,
    DISCOVERY,
    FakeHttp,
    id_token,
    ISSUER,
    issuer_config,
    jwks_doc,
    KEY,
    KID,
    make_passport,
    NOW,
    OTHER_KEY,
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
    http = http or FakeHttp(userinfo={"sub": SUBJECT, "passport_jwt_v11": make_passport([permission()])})
    vault = InMemoryVault()
    clock = Clock()
    jwks = JwksClient(DISCOVERY["jwks_uri"], fetcher=CountingFetcher(jwks_doc((KEY, KID))))
    source = DirectRasPassportSource(
        ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, http=http, jwks=jwks, clock=lambda: NOW
    )
    store = VaultPassportStore(vault, issuer_config(), clock=lambda: NOW)
    login = DirectRasLogin(source, store, vault, clock=clock)
    return SimpleNamespace(login=login, store=store, vault=vault, clock=clock, user=SimpleNamespace(id=7), http=http)


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


def test_finish_stores_passport():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    passport = env.login.finish(env.user, _callback(saved["state"]))
    assert passport.subject == SUBJECT
    assert env.store.load(env.user).status == PassportStatus.OK
    assert not UserVaultWrapper(env.vault, env.user).read_secret(LOGIN_REQUEST_VAULT_KEY)


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
    assert env.store.load(env.user).status == PassportStatus.MISSING
    assert not UserVaultWrapper(env.vault, env.user).read_secret(LOGIN_REQUEST_VAULT_KEY)


def test_finish_rejects_expired_request():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    env.clock.now += LOGIN_REQUEST_LIFETIME + 1
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback(saved["state"]))
    assert env.store.load(env.user).status == PassportStatus.MISSING


def test_finish_rejects_bare_code():
    env = _setup()
    saved = _start(env)
    _nonce_token(env, saved)
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, "code-1")
    assert env.store.load(env.user).status == PassportStatus.MISSING


def test_finish_without_start():
    env = _setup()
    with pytest.raises(PassportLoginError):
        env.login.finish(env.user, _callback("whatever"))


def test_finish_reports_ras_error():
    env = _setup()
    saved = _start(env)
    with pytest.raises(PassportLoginError, match="access_denied"):
        env.login.finish(env.user, f"{CALLBACK}?error=access_denied&state={saved['state']}")


def test_finish_hides_the_passport_when_verification_fails():
    bad = make_passport([permission()])
    # Same claims, signed by a key the JWKS doesn't have.
    claims = jwt.decode(bad, options={"verify_signature": False})
    forged = jwt.encode(claims, OTHER_KEY, algorithm="RS256", headers={"kid": KID})
    env = _setup(FakeHttp(userinfo={"sub": SUBJECT, "passport_jwt_v11": forged}))
    saved = _start(env)
    _nonce_token(env, saved)
    with pytest.raises(PassportLoginError) as excinfo:
        env.login.finish(env.user, _callback(saved["state"]))
    assert forged not in str(excinfo.value)
    assert env.store.load(env.user).status == PassportStatus.MISSING
