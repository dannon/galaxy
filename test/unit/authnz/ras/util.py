"""Synthetic RAS-shaped passports signed with throwaway RSA keys."""

import json
from collections.abc import Sequence
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.verify import IssuerConfig

FIXTURES_ENV = "GALAXY_TEST_RAS_FIXTURES"
ISSUER = "https://sts.example.nih.gov"
KID = "TEST-SIGN-1"
OTHER_KID = "TEST-SIGN-2"
SUBJECT = "test-subject-1"
# In the past, so code that ignores its injected clock and reads time.time() sees expired tokens.
NOW = 1_700_000_000
PASSPORT_LIFETIME = 12 * 3600
RAS_VISA = {
    "type": "https://ras.nih.gov/visas/v1.1",
    "value": "https://sts.example.nih.gov/passport/dbgap/v1.1",
    "source": "https://ncbi.nlm.nih.gov/gap",
    "by": "dac",
}

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key, kid: str) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def jwks_doc(*pairs) -> dict[str, Any]:
    return {"keys": [public_jwk(key, kid) for key, kid in pairs]}


class CountingFetcher:
    def __init__(self, *docs: dict[str, Any]):
        self.docs = list(docs)
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        return self.docs[min(len(self.calls), len(self.docs)) - 1]


def issuer_config(fetcher=None, leeway: int = 60, **jwks_kwargs) -> IssuerConfig:
    fetcher = fetcher or CountingFetcher(jwks_doc((KEY, KID)))
    return IssuerConfig(
        issuer=ISSUER, jwks=JwksClient("https://jwks.example/jwks.json", fetcher=fetcher, **jwks_kwargs), leeway=leeway
    )


def sign(claims: dict[str, Any], key=KEY, kid: str | None = KID, algorithm: str = "RS256") -> str:
    headers = {"typ": "JWT"}
    if kid is not None:
        headers["kid"] = kid
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)


def permission(phs_id: str = "phs000123", **overrides) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "phs_id": phs_id,
        "version": "v2",
        "participant_set": "p1",
        "consent_group": "c1",
        "consent_name": "General Research Use",
        "role": "pi",
        "expiration": NOW + 30 * 86400,
    }
    entry.update(overrides)
    return entry


def visa_claims(
    permissions: Sequence[Any] = (), iat: int = NOW - 60, exp: int | None = None, **overrides
) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "iat": iat,
        "exp": exp if exp is not None else iat + PASSPORT_LIFETIME,
        "scope": "openid ga4gh_passport_v1",
        "jti": f"visa-{iat}-{len(permissions)}",
        "txn": "txn-1",
        "ga4gh_visa_v1": dict(RAS_VISA, asserted=iat),
        "ras_dbgap_permissions": list(permissions),
    }
    claims.update(overrides)
    return claims


def passport_claims(visas: Sequence[str] = (), iat: int = NOW - 60, exp: int | None = None, **overrides):
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "iat": iat,
        "exp": exp if exp is not None else iat + PASSPORT_LIFETIME,
        "scope": "openid ga4gh_passport_v1",
        "jti": "passport-1",
        "txn": "txn-1",
        "ga4gh_passport_v1": list(visas),
    }
    claims.update(overrides)
    return claims


def make_passport(permissions: Sequence[dict[str, Any]] = (), **overrides) -> str:
    """A signed passport carrying one signed RAS visa with ``permissions``, issued alongside it."""
    visa_iat = overrides.get("iat", NOW - 60)
    return sign(passport_claims([sign(visa_claims(permissions, iat=visa_iat))], **overrides))


CLIENT_ID = "test-client"
CLIENT_SECRET = "test-secret"
REDIRECT_URI = "https://callback.example/cb"
NONCE = "nonce-123"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/auth/oauth/v2/authorize",
    "token_endpoint": f"{ISSUER}/auth/oauth/v2/token",
    "userinfo_endpoint": f"{ISSUER}/openid/connect/v1/userinfo",
    "jwks_uri": f"{ISSUER}/openid/connect/jwks.json",
}


def id_token(**overrides) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "iat": NOW - 10,
        "exp": NOW + 3600,
        "nonce": NONCE,
        "acr": f"{ISSUER}/assurance/aal/2",
    }
    claims.update(overrides)
    key = claims.pop("_key", KEY)
    return sign(claims, key=key)


class FakeHttp:
    def __init__(self, token_response=None, userinfo=None, token_status=200, userinfo_status=200):
        self.token_response = token_response or {"access_token": "access-1", "id_token": id_token()}
        self.userinfo = userinfo if userinfo is not None else {"sub": SUBJECT, "passport_jwt_v11": make_passport()}
        self.token_status = token_status
        self.userinfo_status = userinfo_status
        self.calls: list[tuple[str, str, Any]] = []

    def get_json(self, url, bearer=None):
        self.calls.append(("GET", url, bearer))
        if url.endswith("/.well-known/openid-configuration"):
            return 200, DISCOVERY
        if url == f"{ISSUER}/openid/connect/v1.1/userinfo":
            return self.userinfo_status, self.userinfo
        return 404, {}

    def post_form(self, url, data, basic_auth):
        self.calls.append(("POST", url, (dict(data), basic_auth)))
        return self.token_status, self.token_response
