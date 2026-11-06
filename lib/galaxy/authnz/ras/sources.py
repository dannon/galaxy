"""Ways for Galaxy to obtain a user's raw passport JWT.

In the CWIC design a TACC-built bridge application runs the RAS login and makes
the passport available to Galaxy; how it hands it over isn't settled, so that
part is :class:`BridgeAppPassportSource`, a stub. :class:`DirectRasPassportSource`
talks to RAS itself and stands in for the bridge during development.

Whatever the source, the JWT it returns is NIH's original signed passport and
still goes through ``verify_passport`` (via the store) before Galaxy relies on it.
"""

import abc
import hmac
import logging
import secrets
import time
from collections.abc import (
    Callable,
    Mapping,
)
from dataclasses import dataclass
from typing import (
    Any,
    Protocol,
)
from urllib.parse import (
    quote,
    urlencode,
)

import jwt
import requests

from galaxy.authnz.ras.jwks import (
    DEFAULT_HTTP_TIMEOUT,
    JwksClient,
)
from galaxy.authnz.ras.verify import (
    DEFAULT_LEEWAY,
    IssuerConfig,
    PassportVerificationError,
    verify_jwt,
)

log = logging.getLogger(__name__)

DEFAULT_SCOPES = "openid profile email ga4gh_passport_v1"
# RAS only returns the passport from the v1.1 userinfo endpoint; discovery
# advertises the v1 one, which has no passport in it.
USERINFO_V11_PATH = "/openid/connect/v1.1/userinfo"
PASSPORT_USERINFO_CLAIM = "passport_jwt_v11"


class PassportSourceError(Exception):
    pass


class HttpClient(Protocol):
    def get_json(self, url: str, bearer: str | None = None) -> tuple[int, Any]: ...

    def post_form(self, url: str, data: Mapping[str, str], basic_auth: tuple[str, str]) -> tuple[int, Any]: ...


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:2000]}


class RequestsHttpClient:
    def __init__(self, timeout: float = DEFAULT_HTTP_TIMEOUT):
        self.timeout = timeout

    def get_json(self, url: str, bearer: str | None = None) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        response = requests.get(url, headers=headers, timeout=self.timeout)
        return response.status_code, _json_or_text(response)

    def post_form(self, url: str, data: Mapping[str, str], basic_auth: tuple[str, str]) -> tuple[int, Any]:
        response = requests.post(
            url, data=dict(data), auth=basic_auth, headers={"Accept": "application/json"}, timeout=self.timeout
        )
        return response.status_code, _json_or_text(response)


class PassportSource(abc.ABC):
    @abc.abstractmethod
    def obtain(self, user: Any, handoff: Mapping[str, Any]) -> str:
        """Turn whatever the bridge (or RAS) handed back for ``user`` into a raw passport JWT."""


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str


class DirectRasPassportSource(PassportSource):
    """Runs the RAS authorization code flow itself, using ``client_secret_basic``.

    For development and testing only: RAS redirects back to the client's
    registered callbacks, which for the CWIC client are Tapis URLs, so the
    callback has to be carried back to Galaxy by hand (see ``ras_passport_smoke``).

    ``obtain`` expects a handoff with the ``code`` and ``state`` from the callback
    plus the ``expected_state`` and ``nonce`` that went into the authorization URL.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str = DEFAULT_SCOPES,
        http: HttpClient | None = None,
        jwks: JwksClient | None = None,
        discovery: Mapping[str, Any] | None = None,
        userinfo_endpoint: str | None = None,
        leeway: int = DEFAULT_LEEWAY,
        clock: Callable[[], float] = time.time,
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.http: HttpClient = http or RequestsHttpClient()
        self._discovery = dict(discovery) if discovery else None
        self.userinfo_endpoint = userinfo_endpoint or f"{self.issuer}{USERINFO_V11_PATH}"
        self.leeway = leeway
        self.clock = clock
        self.jwks = jwks or JwksClient(lambda: self.discovery()["jwks_uri"])

    def discovery(self) -> dict[str, Any]:
        if self._discovery is None:
            status, doc = self.http.get_json(f"{self.issuer}/.well-known/openid-configuration")
            if status != 200 or not isinstance(doc, dict):
                raise PassportSourceError(f"RAS discovery failed with HTTP {status}")
            if doc.get("issuer", "").rstrip("/") != self.issuer:
                raise PassportSourceError(f"RAS discovery issuer {doc.get('issuer')!r} != {self.issuer!r}")
            self._discovery = doc
        return self._discovery

    def new_authorization_request(self) -> AuthorizationRequest:
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        return AuthorizationRequest(url=self.authorization_url(state, nonce), state=state, nonce=nonce)

    def authorization_url(self, state: str, nonce: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            "prompt": "login",
        }
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params, quote_via=quote)}"

    def obtain(self, user: Any, handoff: Mapping[str, Any]) -> str:
        if handoff.get("error"):
            raise PassportSourceError(f"RAS returned an error: {handoff.get('error')}")
        code, state, expected_state, nonce = (handoff.get(key) for key in ("code", "state", "expected_state", "nonce"))
        if not code:
            raise PassportSourceError("No authorization code in the callback")
        if not expected_state or not nonce:
            raise PassportSourceError("No saved state/nonce for this login attempt")
        if not isinstance(state, str) or not hmac.compare_digest(state, expected_state):
            raise PassportSourceError("State in the callback does not match this login attempt")
        return self.exchange_code(code, nonce)

    def exchange_code(self, code: str, nonce: str) -> str:
        """Redeem ``code``, check the ID token, and fetch the passport from v1.1 userinfo."""
        status, tokens = self.http.post_form(
            self.discovery()["token_endpoint"],
            {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri},
            basic_auth=(self.client_id, self.client_secret),
        )
        if status != 200 or not isinstance(tokens, dict):
            detail = tokens if isinstance(tokens, dict) else {}
            raise PassportSourceError(
                f"RAS token endpoint returned HTTP {status}: {detail.get('error')} {detail.get('error_description', '')}".strip()
            )
        access_token = tokens.get("access_token")
        id_token = tokens.get("id_token")
        if not access_token or not id_token:
            raise PassportSourceError("RAS token response is missing access_token or id_token")
        id_claims = self.verify_id_token(id_token, nonce)

        status, userinfo = self.http.get_json(self.userinfo_endpoint, bearer=access_token)
        if status != 200 or not isinstance(userinfo, dict):
            raise PassportSourceError(f"RAS userinfo returned HTTP {status}")
        if userinfo.get("sub") != id_claims["sub"]:
            raise PassportSourceError("RAS userinfo subject does not match the ID token")
        passport = userinfo.get(PASSPORT_USERINFO_CLAIM)
        if not passport or not isinstance(passport, str):
            raise PassportSourceError(f"RAS userinfo has no {PASSPORT_USERINFO_CLAIM}")
        try:
            passport_sub = jwt.decode(passport, options={"verify_signature": False}).get("sub")
        except jwt.PyJWTError as e:
            raise PassportSourceError(f"RAS returned an unreadable passport: {e}") from e
        # Full verification happens when the passport is stored; this only ties it
        # to the person who just logged in.
        if passport_sub != id_claims["sub"]:
            raise PassportSourceError("Passport subject does not match the ID token")
        return passport

    def verify_id_token(self, id_token: str, nonce: str, now: float | None = None) -> dict[str, Any]:
        config = IssuerConfig(issuer=self.issuer, jwks=self.jwks, leeway=self.leeway)
        try:
            claims = verify_jwt(
                id_token, config, now=self.clock() if now is None else now, audience=self.client_id, what="ID token"
            )
        except PassportVerificationError as e:
            raise PassportSourceError(str(e)) from e
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.client_id:
            raise PassportSourceError("ID token azp does not match the client")
        claimed_nonce = claims.get("nonce")
        if not isinstance(claimed_nonce, str) or not hmac.compare_digest(claimed_nonce, nonce):
            raise PassportSourceError("ID token nonce does not match this login attempt")
        return claims


class BridgeAppPassportSource(PassportSource):
    """Placeholder for receiving the passport from the TACC bridge application.

    The bridge fronts the ``cfderas.tapis.io`` tenant, runs the RAS login and makes
    the passport available to Galaxy, which sent the user there with a return
    address. Open questions, all of which shape ``obtain``'s handoff:

    - Does Galaxy get the passport on the return redirect (a code or one-time
      reference it redeems), by reading a Tapis vault secret, or via a Tapis API
      call? Whichever it is, it must be NIH's original signed passport -- RAS
      visas may not be repackaged by another broker.
    - How does Galaxy authenticate to the bridge, and how is the returned passport
      bound to the Galaxy user who started the flow (state/nonce on the redirect)?
    - Who owns refresh, and how often must Galaxy recheck (the "once an hour" vs
      "at most once an hour" question from 10/27)?
    """

    def __init__(self, bridge_url: str):
        self.bridge_url = bridge_url

    def obtain(self, user: Any, handoff: Mapping[str, Any]) -> str:
        raise NotImplementedError("The bridge application handoff is not defined yet")
