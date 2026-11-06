"""Synthetic RAS-shaped passports signed with throwaway RSA keys."""

import json
from collections.abc import Sequence
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.verify import IssuerConfig

ISSUER = "https://sts.example.nih.gov"
KID = "TEST-SIGN-1"
OTHER_KID = "TEST-SIGN-2"
SUBJECT = "test-subject-1"
NOW = 1_800_000_000
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
    """A signed passport carrying one signed RAS visa with ``permissions``."""
    return sign(passport_claims([sign(visa_claims(permissions))], **overrides))
