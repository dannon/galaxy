"""Connect Galaxy users to a GA4GH Passport broker and keep their Passports.

Galaxy is an OAuth 2.0 client of the broker (authorization code + PKCE):

1. ``connect_url``: the browser is sent to the broker. ``state`` and the PKCE
   verifier are stored with ``FlowState``, bound to the Galaxy session.
2. ``complete_connect``: the broker sends the browser back with a one-time
   code. Galaxy exchanges it (back channel, client secret + PKCE verifier) for
   the Passport, verifies the Passport itself, checks the Passport identity
   against the one bound to the user, and stores the Passport in the user's
   vault (encrypted at rest with the database vault).
3. ``current``: the user's stored Passport, verified again on every read. This
   is the interface the rest of Galaxy uses.

The broker returns the Passport untouched; all decoding happens here, through
``galaxy.security.ga4gh_passport``.
"""

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pkce import generate_pkce_pair
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from galaxy import exceptions
from galaxy.authnz.flow_state import (
    authentication_id,
    FlowState,
    InvalidFlow,
)
from galaxy.config import GalaxyAppConfiguration
from galaxy.model import (
    User,
    UserGA4GHIdentity,
)
from galaxy.model.scoped_session import galaxy_scoped_session
from galaxy.security.ga4gh_passport import (
    InvalidPassport,
    PassportVerifier,
    VerifiedPassport,
)
from galaxy.security.vault import (
    is_vault_configured,
    UserVaultWrapper,
    Vault,
)
from galaxy.util import (
    listify,
    requests,
)
from galaxy.work.context import SessionRequestContext

log = logging.getLogger(__name__)

PASSPORT_VAULT_KEY = "ga4gh_passport"
FLOW_PROVIDER = "ga4gh_passport"
CONNECT_PATH = "ga4gh_passport/connect"
CALLBACK_PATH = "ga4gh_passport/callback"
TOKEN_REQUEST_TIMEOUT = 10
# OAuth errors a broker may send back (RFC 6749 4.1.2.1), passed on as they are.
BROKER_ERRORS = {
    "access_denied",
    "invalid_request",
    "unauthorized_client",
    "unsupported_response_type",
    "invalid_scope",
    "server_error",
    "temporarily_unavailable",
}


@dataclass(frozen=True)
class PassportBrokerConfig:
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: str
    trusted_issuers: tuple[str, ...]

    @staticmethod
    def from_config(config: GalaxyAppConfiguration) -> "PassportBrokerConfig | None":
        authorize_url = config.ga4gh_passport_broker_authorize_url
        if not authorize_url:
            return None
        settings = {
            "ga4gh_passport_broker_token_url": config.ga4gh_passport_broker_token_url,
            "ga4gh_passport_broker_client_id": config.ga4gh_passport_broker_client_id,
            "ga4gh_passport_broker_client_secret": config.ga4gh_passport_broker_client_secret,
            "ga4gh_passport_trusted_issuers": config.ga4gh_passport_trusted_issuers,
        }
        if missing := [name for name, value in settings.items() if not value]:
            raise exceptions.ConfigurationError(
                f"ga4gh_passport_broker_authorize_url is set, but these options are not: {', '.join(missing)}"
            )
        return PassportBrokerConfig(
            authorize_url=authorize_url,
            token_url=str(settings["ga4gh_passport_broker_token_url"]),
            client_id=str(settings["ga4gh_passport_broker_client_id"]),
            client_secret=str(settings["ga4gh_passport_broker_client_secret"]),
            trusted_issuers=tuple(listify(settings["ga4gh_passport_trusted_issuers"], do_strip=True)),
        )


@dataclass(frozen=True)
class ConnectResult:
    """Where to send the browser after the broker returns, and why if it failed."""

    next_path: str
    error: str | None = None


class PassportManager:
    def __init__(self, config: GalaxyAppConfiguration, sa_session: galaxy_scoped_session, vault: Vault) -> None:
        self._broker = PassportBrokerConfig.from_config(config)
        self._sa_session = sa_session
        self._vault = vault
        self._verifier = PassportVerifier(self._broker.trusted_issuers) if self._broker else None

    @property
    def enabled(self) -> bool:
        return self._broker is not None

    # --- connect flow ------------------------------------------------------------------

    def connect_url(self, trans: SessionRequestContext, redirect_uri: str, next_path: str) -> str:
        """Start a connect for the logged-in user; return the broker URL to send the browser to."""
        broker = self._require_broker()
        if not is_vault_configured(self._vault):
            raise exceptions.ServerNotConfiguredForRequest("GA4GH Passports need a configured vault.")
        user = trans.user
        if user is None:
            raise exceptions.AuthenticationRequired("Log in to Galaxy before connecting a GA4GH Passport.")
        verifier, challenge = generate_pkce_pair(96)
        state = secrets.token_urlsafe(32)
        flow = FlowState(trans.sa_session, trans.galaxy_session, FLOW_PROVIDER)
        flow.save(
            "authentication",
            {"state": state, "verifier": verifier, "next": next_path, "user_id": user.id},
            identifier=authentication_id(state),
        )
        query = {
            "client_id": broker.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        log.info("GA4GH passport connect started user_id=%s", user.id)
        return f"{broker.authorize_url}?{urlencode(query)}"

    def complete_connect(
        self,
        trans: SessionRequestContext,
        redirect_uri: str,
        state: str | None,
        code: str | None,
        error: str | None,
    ) -> ConnectResult:
        """Handle the broker's redirect back to Galaxy. Never raises for expected failures."""
        broker = self._require_broker()
        try:
            flow = FlowState(trans.sa_session, trans.galaxy_session, FLOW_PROVIDER)
            payload = flow.consume("authentication", authentication_id(state))
        except InvalidFlow:
            return ConnectResult("/", "invalid_flow")
        next_path = payload["next"]
        user = trans.user
        if payload.get("state") != state or user is None or user.id != payload.get("user_id"):
            return ConnectResult(next_path, "invalid_flow")
        if error:
            log.info("GA4GH passport connect: broker returned error=%s user_id=%s", error, user.id)
            return ConnectResult(next_path, error if error in BROKER_ERRORS else "broker_error")
        if not code:
            return ConnectResult(next_path, "invalid_request")

        token, preferred_username = self._exchange_code(broker, redirect_uri, code, payload["verifier"])
        if token is None:
            return ConnectResult(next_path, "broker_error")
        try:
            passport = self._get_verifier().verify(token)
        except InvalidPassport as e:
            log.warning("GA4GH passport connect: %s user_id=%s", e, user.id)
            return ConnectResult(next_path, "invalid_passport")
        if bind_error := self._bind_identity(user, passport, preferred_username):
            return ConnectResult(next_path, bind_error)

        record = {"passport": token, "preferred_username": preferred_username, "received_at": int(time.time())}
        UserVaultWrapper(self._vault, user).write_secret(PASSPORT_VAULT_KEY, json.dumps(record))
        log.info(
            "GA4GH passport connected user_id=%s iss=%s sub=%s txn=%s preferred_username=%s grants=%s",
            user.id,
            passport.issuer,
            passport.sub,
            passport.txn,
            preferred_username,
            ",".join(grant.authorization for grant in passport.grants) or "-",
        )
        return ConnectResult(next_path)

    def _exchange_code(
        self, broker: PassportBrokerConfig, redirect_uri: str, code: str, verifier: str
    ) -> tuple[str | None, str | None]:
        try:
            response = requests.post(
                broker.token_url,
                auth=(broker.client_id, broker.client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                timeout=TOKEN_REQUEST_TIMEOUT,
            )
            body = response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            log.warning("GA4GH passport broker token request failed: %s", e)
            return None, None
        if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("passport"), str):
            # Log the OAuth error only; the body of a successful response holds the Passport.
            oauth_error = body.get("error") if isinstance(body, dict) else None
            log.warning("GA4GH passport broker token request: HTTP %s error=%s", response.status_code, oauth_error)
            return None, None
        preferred_username = body.get("preferred_username")
        return body["passport"], preferred_username if isinstance(preferred_username, str) else None

    def _bind_identity(self, user: User, passport: VerifiedPassport, preferred_username: str | None) -> str | None:
        """Bind the Passport identity to the user on first connect; refuse a different one later."""
        session = self._sa_session
        bound = session.scalars(
            select(UserGA4GHIdentity).filter_by(issuer=passport.issuer, sub=passport.sub)
        ).one_or_none()
        if bound is not None and bound.user_id != user.id:
            log.warning(
                "GA4GH passport connect refused: identity %s/%s is bound to another user, user_id=%s",
                passport.issuer,
                passport.sub,
                user.id,
            )
            return "identity_in_use"
        mine = session.scalars(
            select(UserGA4GHIdentity).filter_by(user_id=user.id, issuer=passport.issuer)
        ).one_or_none()
        if mine is not None and mine.sub != passport.sub:
            log.warning(
                "GA4GH passport connect refused: user_id=%s is bound to %s/%s, got sub=%s",
                user.id,
                passport.issuer,
                mine.sub,
                passport.sub,
            )
            return "identity_mismatch"
        if mine is None:
            mine = UserGA4GHIdentity(user_id=user.id, issuer=passport.issuer, sub=passport.sub)
            log.info("GA4GH passport identity bound user_id=%s iss=%s sub=%s", user.id, passport.issuer, passport.sub)
        mine.preferred_username = preferred_username
        session.add(mine)
        try:
            session.commit()
        except IntegrityError:
            # A concurrent connect bound this identity (or this user) first.
            session.rollback()
            return "identity_in_use"
        return None

    # --- the stored Passport ------------------------------------------------------------

    def current(self, user: User) -> VerifiedPassport | None:
        """The user's stored Passport if it still verifies (signatures, expiry), else None."""
        record = self._read(user)
        if record is None:
            return None
        try:
            return self._get_verifier().verify(record["passport"])
        except InvalidPassport as e:
            log.info("GA4GH passport of user_id=%s no longer valid: %s", user.id, e)
            return None

    def identity(self, user: User) -> UserGA4GHIdentity | None:
        if self._broker is None:
            return None
        return self._sa_session.scalars(
            select(UserGA4GHIdentity).filter_by(user_id=user.id).order_by(UserGA4GHIdentity.id)
        ).first()

    def forget(self, user: User) -> None:
        """Delete the user's stored Passport. The identity binding stays."""
        if is_vault_configured(self._vault) and self._read(user) is not None:
            UserVaultWrapper(self._vault, user).delete_secret(PASSPORT_VAULT_KEY)
            log.info("GA4GH passport forgotten user_id=%s", user.id)

    def _read(self, user: User) -> dict[str, Any] | None:
        if self._broker is None or not is_vault_configured(self._vault):
            return None
        raw = UserVaultWrapper(self._vault, user).read_secret(PASSPORT_VAULT_KEY)
        if not raw:  # never written, or deleted (the vault deletes by writing "")
            return None
        record = json.loads(raw)
        return record if isinstance(record, dict) else None

    def _require_broker(self) -> PassportBrokerConfig:
        if self._broker is None:
            raise exceptions.ConfigDoesNotAllowException("GA4GH Passport connections are not enabled.")
        return self._broker

    def _get_verifier(self) -> PassportVerifier:
        assert self._verifier is not None
        return self._verifier
