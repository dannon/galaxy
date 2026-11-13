import base64
import hashlib
import json
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import (
    parse_qs,
    urlparse,
)

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy import (
    create_engine,
    select,
)
from sqlalchemy.orm import Session

from galaxy import (
    exceptions,
    model,
)
from galaxy.managers.ga4gh_passports import (
    PASSPORT_VAULT_KEY,
    PassportManager,
)
from galaxy.security.ga4gh_passport import (
    PassportVerifier,
    RAS_DBGAP_VISA_TYPE,
)
from galaxy.security.vault import (
    DatabaseVault,
    NullVault,
    UserVaultWrapper,
)

ISSUER = "https://ras.example.org"
CALLBACK = "https://galaxy.example.org/ga4gh_passport/callback"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ROGUE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticKeys:
    def signing_key(self, issuer: str, token: str) -> Any:
        return KEY.public_key()


def make_passport(sub="sub-alice", phs_id="phs000001", key=KEY) -> str:
    now = int(time.time())
    visa = jwt.encode(
        {
            "iss": ISSUER,
            "sub": sub,
            "iat": now,
            "exp": now + 3600,
            "ga4gh_visa_v1": {"type": RAS_DBGAP_VISA_TYPE, "asserted": now, "value": "v", "source": "s"},
            "ras_dbgap_permissions": [
                {
                    "phs_id": phs_id,
                    "version": "v1",
                    "participant_set": "p1",
                    "consent_group": "c1",
                    "consent_name": "GRU",
                    "role": "pi",
                    "expiration": now + 86400,
                }
            ],
        },
        key,
        algorithm="RS256",
    )
    claims = {"iss": ISSUER, "sub": sub, "iat": now, "exp": now + 3600, "txn": "txn-1", "ga4gh_passport_v1": [visa]}
    return jwt.encode(claims, key, algorithm="RS256")


def config(**overrides) -> SimpleNamespace:
    settings = {
        "ga4gh_passport_broker_authorize_url": "https://bridge.example.org/connect",
        "ga4gh_passport_broker_token_url": "https://bridge.example.org/token",
        "ga4gh_passport_broker_client_id": "galaxy",
        "ga4gh_passport_broker_client_secret": "secret",
        "ga4gh_passport_trusted_issuers": ISSUER,
        **overrides,
    }
    return SimpleNamespace(**settings)


class FakeBroker:
    """Stands in for the broker's token endpoint; checks what Galaxy sends it."""

    def __init__(self):
        self.challenges: dict[str, str] = {}  # code -> PKCE challenge it was issued for
        self.passport: str = make_passport()
        self.status = 200
        self.body: Any = None
        self.raise_error: Exception | None = None
        self.requests: list[dict[str, Any]] = []

    def post(self, url, auth, data, timeout):
        self.requests.append({"url": url, "auth": auth, "data": data})
        if self.raise_error:
            raise self.raise_error
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=")
        assert self.challenges.get(data["code"]) == challenge.decode(), "PKCE verifier does not match"
        body = self.body if self.body is not None else {"passport": self.passport, "preferred_username": "alice@nih"}
        return SimpleNamespace(status_code=self.status, json=lambda: body)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'galaxy.sqlite'}")
    model.mapper_registry.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def vault(session):
    return DatabaseVault(session, {"encryption_keys": [Fernet.generate_key().decode()]})


@pytest.fixture
def broker(monkeypatch):
    fake = FakeBroker()
    monkeypatch.setattr("galaxy.managers.ga4gh_passports.requests.post", fake.post)
    return fake


@pytest.fixture
def manager(session, vault, broker):
    manager = PassportManager(config(), session, vault)  # type: ignore[arg-type]
    manager._verifier = PassportVerifier([ISSUER], keys=StaticKeys())
    return manager


def new_user(session, email):
    user = model.User(email=email, password="x")
    session.add(user)
    session.commit()
    return user


def browser(session, user):
    galaxy_session = model.GalaxySession(is_valid=True, user=user)
    session.add(galaxy_session)
    session.commit()
    return SimpleNamespace(sa_session=session, galaxy_session=galaxy_session, user=user)


@pytest.fixture
def alice(session):
    return new_user(session, "alice@example.org")


def start(manager, broker, trans, next_path="/"):
    """Galaxy's connect redirect, then the broker issuing a code for it."""
    url = manager.connect_url(trans, CALLBACK, next_path)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    code = f"code-{len(broker.challenges)}"
    broker.challenges[code] = query["code_challenge"]
    return query, code


def connect(manager, broker, trans, next_path="/"):
    query, code = start(manager, broker, trans, next_path)
    return manager.complete_connect(trans, CALLBACK, state=query["state"], code=code, error=None)


# --- the happy path ---------------------------------------------------------------------


def test_connect_stores_verified_passport_and_binds_identity(manager, broker, session, vault, alice):
    trans = browser(session, alice)
    query, code = start(manager, broker, trans, "/datasets/list")
    assert query["client_id"] == "galaxy" and query["redirect_uri"] == CALLBACK
    assert query["code_challenge_method"] == "S256"

    result = manager.complete_connect(trans, CALLBACK, state=query["state"], code=code, error=None)
    assert (result.next_path, result.error) == ("/datasets/list", None)
    sent = broker.requests[0]
    assert sent["auth"] == ("galaxy", "secret") and sent["data"]["redirect_uri"] == CALLBACK

    passport = manager.current(alice)
    assert passport is not None and [g.authorization for g in passport.grants] == ["phs000001.c1"]
    identity = manager.identity(alice)
    assert identity is not None
    assert (identity.issuer, identity.sub, identity.preferred_username) == (ISSUER, "sub-alice", "alice@nih")
    stored = json.loads(UserVaultWrapper(vault, alice).read_secret(PASSPORT_VAULT_KEY) or "")
    assert stored["passport"] == broker.passport and stored["preferred_username"] == "alice@nih"


def test_reconnect_replaces_passport_keeps_identity(manager, broker, session, alice):
    trans = browser(session, alice)
    assert connect(manager, broker, trans).error is None
    broker.passport = make_passport(phs_id="phs000002")
    assert connect(manager, broker, trans).error is None
    passport = manager.current(alice)
    assert passport is not None and [g.authorization for g in passport.grants] == ["phs000002.c1"]
    assert len(session.scalars(select(model.UserGA4GHIdentity)).all()) == 1


def test_passport_never_logged(manager, broker, session, alice, caplog):
    caplog.set_level("DEBUG")
    connect(manager, broker, browser(session, alice))
    assert broker.passport not in caplog.text
    assert "GA4GH passport connected" in caplog.text


# --- flow state ---------------------------------------------------------------------------


def test_callback_is_single_use(manager, broker, session, alice):
    trans = browser(session, alice)
    query, code = start(manager, broker, trans)
    assert manager.complete_connect(trans, CALLBACK, state=query["state"], code=code, error=None).error is None
    replay = manager.complete_connect(trans, CALLBACK, state=query["state"], code=code, error=None)
    assert replay.error == "invalid_flow"


@pytest.mark.parametrize("state", [None, "", "unknown-state"])
def test_unknown_state(manager, broker, session, alice, state):
    trans = browser(session, alice)
    start(manager, broker, trans)
    result = manager.complete_connect(trans, CALLBACK, state=state, code="code-0", error=None)
    assert (result.next_path, result.error) == ("/", "invalid_flow")


def test_callback_in_another_browser(manager, broker, session, alice):
    query, code = start(manager, broker, browser(session, alice))
    other_browser = browser(session, alice)
    assert manager.complete_connect(other_browser, CALLBACK, state=query["state"], code=code, error=None).error == (
        "invalid_flow"
    )


def test_callback_for_a_different_logged_in_user(manager, broker, session, alice):
    trans = browser(session, alice)
    query, code = start(manager, broker, trans)
    trans.user = new_user(session, "mallory@example.org")
    assert manager.complete_connect(trans, CALLBACK, state=query["state"], code=code, error=None).error == (
        "invalid_flow"
    )


# --- broker outcomes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "error, expected", [("access_denied", "access_denied"), ("server_error", "server_error"), ("weird", "broker_error")]
)
def test_broker_error_is_passed_on(manager, broker, session, alice, error, expected):
    trans = browser(session, alice)
    query, _ = start(manager, broker, trans, "/here")
    result = manager.complete_connect(trans, CALLBACK, state=query["state"], code=None, error=error)
    assert (result.next_path, result.error) == ("/here", expected)
    assert broker.requests == []


@pytest.mark.parametrize(
    "status, body",
    [(400, {"error": "invalid_grant"}), (200, {"preferred_username": "x"}), (200, ["not", "a", "dict"])],
)
def test_bad_token_response(manager, broker, session, alice, status, body):
    broker.status, broker.body = status, body
    assert connect(manager, broker, browser(session, alice)).error == "broker_error"
    assert manager.current(alice) is None


def test_broker_unreachable(manager, broker, session, alice):
    broker.raise_error = RequestsConnectionError("down")
    assert connect(manager, broker, browser(session, alice)).error == "broker_error"


def test_invalid_passport_not_stored(manager, broker, session, alice):
    broker.passport = make_passport(key=ROGUE_KEY)
    assert connect(manager, broker, browser(session, alice)).error == "invalid_passport"
    assert manager.current(alice) is None and manager.identity(alice) is None


# --- identity binding -----------------------------------------------------------------------


def test_different_identity_for_same_user_refused(manager, broker, session, alice):
    trans = browser(session, alice)
    assert connect(manager, broker, trans).error is None
    broker.passport = make_passport(sub="sub-somebody-else", phs_id="phs000009")
    assert connect(manager, broker, trans).error == "identity_mismatch"
    passport = manager.current(alice)
    assert passport is not None and passport.sub == "sub-alice"  # the earlier Passport is untouched


def test_identity_bound_to_another_user_refused(manager, broker, session, alice):
    assert connect(manager, broker, browser(session, alice)).error is None
    bob = new_user(session, "bob@example.org")
    assert connect(manager, broker, browser(session, bob)).error == "identity_in_use"
    assert manager.current(bob) is None and manager.identity(bob) is None


# --- stored Passport --------------------------------------------------------------------------


def test_forget_keeps_identity(manager, broker, session, alice):
    connect(manager, broker, browser(session, alice))
    manager.forget(alice)
    assert manager.current(alice) is None
    assert manager.identity(alice) is not None
    manager.forget(alice)  # nothing stored: no error


# --- configuration ------------------------------------------------------------------------------


def test_disabled_without_authorize_url(session, vault, alice):
    manager = PassportManager(config(ga4gh_passport_broker_authorize_url=None), session, vault)  # type: ignore[arg-type]
    assert not manager.enabled
    with pytest.raises(exceptions.ConfigDoesNotAllowException):
        manager.connect_url(browser(session, alice), CALLBACK, "/")
    assert manager.current(alice) is None and manager.identity(alice) is None


def test_incomplete_config(session, vault):
    with pytest.raises(exceptions.ConfigurationError, match="ga4gh_passport_broker_client_secret"):
        PassportManager(config(ga4gh_passport_broker_client_secret=None), session, vault)  # type: ignore[arg-type]


def test_vault_required(session, alice):
    manager = PassportManager(config(), session, NullVault())  # type: ignore[arg-type]
    with pytest.raises(exceptions.ServerNotConfiguredForRequest):
        manager.connect_url(browser(session, alice), CALLBACK, "/")


def test_trusted_issuers_list(session, vault):
    manager = PassportManager(
        config(ga4gh_passport_trusted_issuers=f" {ISSUER} , https://other.example.org"),  # type: ignore[arg-type]
        session,
        vault,
    )
    assert manager._broker is not None
    assert manager._broker.trusted_issuers == (ISSUER, "https://other.example.org")
