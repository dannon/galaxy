"""Verify GA4GH Passports and decode their Visas.

A Passport is a signed JWT whose ``ga4gh_passport_v1`` claim is a list of Visas,
each one a signed JWT of its own (GA4GH Passport and AAI specifications,
https://ga4gh.github.io/aai/). This module checks a Passport and its Visas
against a set of trusted issuers and turns the Visas Galaxy understands into
plain data. It has no dependency on the Galaxy app or database.

Trust and keys: signing keys are only ever taken from the published key set
(OIDC discovery -> ``jwks_uri``) of an issuer listed as trusted. A ``jku``
header in a Visa is never followed; the specification allows a Passport
Clearinghouse to use keys obtained out of band for a trusted issuer instead.
This also covers Visas that have no ``jku`` at all, such as the NIH RAS ones.

Failure handling follows the specification: a Passport that fails its checks
is rejected as a whole; a single Visa that fails its checks, or comes from an
untrusted issuer, is dropped and the rest are kept; a Visa of a type Galaxy
does not understand is kept but carries no grants.
"""

import logging
import threading
from collections.abc import (
    Callable,
    Iterable,
)
from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    Protocol,
)

import jwt

from galaxy.util import requests

log = logging.getLogger(__name__)

# Signature algorithms allowed by the GA4GH AAI profile.
ALGORITHMS = ["RS256", "ES256"]
PASSPORT_CLAIM = "ga4gh_passport_v1"
VISA_CLAIM = "ga4gh_visa_v1"
REQUIRED_CLAIMS = ["iss", "sub", "iat", "exp"]
REQUIRED_VISA_FIELDS = ("type", "asserted", "value", "source")
# NIH RAS puts dbGaP authorizations in a custom Visa type (RAS Partner
# Developer Guide), not in GA4GH ControlledAccessGrants Visas.
RAS_DBGAP_VISA_TYPE = "https://ras.nih.gov/visas/v1.1"
DEFAULT_LEEWAY = 60  # seconds of clock skew tolerated on exp/iat
DEFAULT_TIMEOUT = 10


class InvalidPassport(Exception):
    """The Passport as a whole failed verification."""


class InvalidVisa(Exception):
    """A single Visa failed verification or could not be decoded."""


@dataclass(frozen=True)
class DbgapGrant:
    """One dbGaP authorization from a RAS Visa (an entry of ``ras_dbgap_permissions``)."""

    phs_id: str
    version: str
    participant_set: str
    consent_group: str
    consent_name: str
    role: str
    expiration: int  # the grant's (DAR's) expiration, not the Visa's

    @property
    def authorization(self) -> str:
        """Study + consent group, e.g. ``phs000001.c1``: what data access depends on."""
        return f"{self.phs_id}.{self.consent_group}"


@dataclass(frozen=True)
class Visa:
    issuer: str
    sub: str
    type: str
    value: str
    source: str
    by: str | None
    asserted: int
    issued_at: int
    expires_at: int
    recognized: bool  # False: a type no handler understands; kept, but grants nothing
    grants: tuple[DbgapGrant, ...] = ()


@dataclass(frozen=True)
class VerifiedPassport:
    issuer: str
    sub: str
    txn: str | None  # NIH RAS login transaction id, for audit; not a GA4GH claim
    issued_at: int
    expires_at: int
    visas: tuple[Visa, ...]
    rejected_visas: tuple[str, ...]  # one reason per dropped Visa (never the token)
    token: str = field(repr=False)

    @property
    def grants(self) -> tuple[DbgapGrant, ...]:
        return tuple(grant for visa in self.visas for grant in visa.grants)

    @property
    def visas_expire_at(self) -> int | None:
        """Earliest Visa expiry; the specification says to use the minimum across Visas."""
        return min((visa.expires_at for visa in self.visas), default=None)


VisaHandler = Callable[[dict[str, Any]], tuple[DbgapGrant, ...]]


def ras_dbgap_grants(claims: dict[str, Any]) -> tuple[DbgapGrant, ...]:
    permissions = claims.get("ras_dbgap_permissions")
    if not isinstance(permissions, list):
        raise InvalidVisa("ras_dbgap_permissions missing or not a list")
    grants = []
    for permission in permissions:
        try:
            grants.append(
                DbgapGrant(
                    phs_id=_str(permission["phs_id"]),
                    version=_str(permission["version"]),
                    participant_set=_str(permission["participant_set"]),
                    consent_group=_str(permission["consent_group"]),
                    consent_name=_str(permission["consent_name"]),
                    role=_str(permission["role"]),
                    expiration=_int(permission["expiration"]),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise InvalidVisa(f"malformed ras_dbgap_permissions entry: {e!r}") from e
    return tuple(grants)


DEFAULT_VISA_HANDLERS: dict[str, VisaHandler] = {RAS_DBGAP_VISA_TYPE: ras_dbgap_grants}


class IssuerKeys(Protocol):
    def signing_key(self, issuer: str, token: str) -> Any:
        """Return the key that verifies ``token``, from ``issuer``'s published keys."""


class OidcDiscoveryKeys:
    """Signing keys of trusted issuers, found through OIDC discovery.

    The discovery document is fetched once per issuer; keys are cached by
    ``jwt.PyJWKClient`` (it refetches the key set when it meets an unknown ``kid``).
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._clients: dict[str, jwt.PyJWKClient] = {}
        self._lock = threading.Lock()

    def signing_key(self, issuer: str, token: str) -> Any:
        return self._client(issuer).get_signing_key_from_jwt(token).key

    def _client(self, issuer: str) -> jwt.PyJWKClient:
        with self._lock:
            client = self._clients.get(issuer)
            if client is None:
                url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
                response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                jwks_uri = response.json()["jwks_uri"]
                client = jwt.PyJWKClient(jwks_uri, cache_keys=True, timeout=self.timeout)
                self._clients[issuer] = client
            return client


class PassportVerifier:
    def __init__(
        self,
        trusted_issuers: Iterable[str],
        keys: IssuerKeys | None = None,
        visa_handlers: dict[str, VisaHandler] | None = None,
        leeway: int = DEFAULT_LEEWAY,
    ) -> None:
        self.trusted_issuers = frozenset(trusted_issuers)
        self.keys = keys or OidcDiscoveryKeys()
        self.visa_handlers = DEFAULT_VISA_HANDLERS if visa_handlers is None else visa_handlers
        self.leeway = leeway

    def verify(self, token: str) -> VerifiedPassport:
        """Verify a Passport JWT and its Visas; raise ``InvalidPassport`` if the Passport fails."""
        try:
            claims = self._decode(token)
        except jwt.PyJWTError as e:
            raise InvalidPassport(f"passport rejected: {e}") from e
        visa_tokens = claims.get(PASSPORT_CLAIM)
        if not isinstance(visa_tokens, list):
            raise InvalidPassport(f"passport rejected: {PASSPORT_CLAIM} missing or not a list")

        visas, rejected = [], []
        for index, visa_token in enumerate(visa_tokens):
            try:
                visas.append(self._visa(visa_token))
            except (jwt.PyJWTError, InvalidVisa) as e:
                reason = f"visa {index}: {e}"
                log.info("GA4GH passport %s/%s: dropped %s", claims["iss"], claims["sub"], reason)
                rejected.append(reason)
        return VerifiedPassport(
            issuer=claims["iss"],
            sub=claims["sub"],
            txn=claims.get("txn"),
            issued_at=claims["iat"],
            expires_at=claims["exp"],
            visas=tuple(visas),
            rejected_visas=tuple(rejected),
            token=token,
        )

    def _decode(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str):
            raise jwt.InvalidTokenError("not a JWT string")
        # Check the issuer before touching the network, so a token can't make
        # Galaxy fetch keys from a URL of its choosing.
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        if issuer not in self.trusted_issuers:
            raise jwt.InvalidIssuerError(f"untrusted issuer {issuer!r}")
        return jwt.decode(
            token,
            self.keys.signing_key(issuer, token),
            algorithms=ALGORITHMS,
            issuer=issuer,
            leeway=self.leeway,
            # Galaxy holds the Passport for the user; it is not its audience.
            # Audience restrictions are for the services the Passport is sent to.
            options={"require": REQUIRED_CLAIMS, "verify_aud": False},
        )

    def _visa(self, token: Any) -> Visa:
        claims = self._decode(token)
        visa = claims.get(VISA_CLAIM)
        if not isinstance(visa, dict) or any(name not in visa for name in REQUIRED_VISA_FIELDS):
            raise InvalidVisa(f"{VISA_CLAIM} missing or incomplete")
        handler = self.visa_handlers.get(visa["type"])
        return Visa(
            issuer=claims["iss"],
            sub=claims["sub"],
            type=_str(visa["type"]),
            value=_str(visa["value"]),
            source=_str(visa["source"]),
            by=visa.get("by"),
            asserted=_int(visa["asserted"]),
            issued_at=claims["iat"],
            expires_at=claims["exp"],
            recognized=handler is not None,
            grants=handler(claims) if handler else (),
        )


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidVisa(f"expected a string, got {type(value).__name__}")
    return value


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidVisa(f"expected an integer, got {type(value).__name__}")
    return value
