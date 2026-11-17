"""Verify NIH RAS passport and visa JWTs and parse them into typed objects.

Policy decisions, all enforced here:

- Only RS256 is accepted, for the passport and every visa. ``alg=none`` and the
  HMAC algorithms are refused before any key lookup, so a public key can never be
  used as an HMAC secret.
- The passport and every visa must be signed by a key in the configured issuer's
  JWKS and carry that issuer in ``iss``. RAS signs both with the same key; visas
  from any other issuer are refused.
- ``exp`` and ``iat`` are required and checked against ``now`` with
  ``leeway`` seconds of slack. Time checks are done here rather than by PyJWT so
  ``now`` can be pinned.
- A passport that fails any of this is rejected as a whole.
- A visa that fails on its own (signature, issuer, algorithm, lifetime, a ``sub``
  that differs from the passport's, no ``ga4gh_visa_v1`` object) is dropped and
  recorded in ``Passport.dropped_visas``; the rest are kept. This is what the
  GA4GH passport specification asks of a clearinghouse, and visas can be
  shorter-lived than the passport carrying them. Dropped visas grant nothing.
- A visa that couldn't be checked because the issuer's keys couldn't be fetched
  is not dropped: the whole verification is reported as unavailable.
"""

import time
from dataclasses import dataclass
from typing import Any

import jwt

from galaxy.authnz.ras.jwks import (
    JwksClient,
    JwksError,
    JwksUnavailableError,
)
from galaxy.authnz.ras.models import (
    DbgapPermission,
    DroppedVisa,
    Passport,
    Visa,
)

ALLOWED_ALGORITHMS = ["RS256"]
DEFAULT_LEEWAY = 60
KNOWN_PERMISSION_FIELDS = {
    "phs_id",
    "version",
    "participant_set",
    "consent_group",
    "consent_name",
    "role",
    "expiration",
}


class PassportVerificationError(Exception):
    pass


class TokenExpiredError(PassportVerificationError):
    pass


class VerificationUnavailableError(PassportVerificationError):
    """The token couldn't be checked because the issuer's keys couldn't be fetched."""


@dataclass
class IssuerConfig:
    issuer: str
    jwks: JwksClient
    leeway: int = DEFAULT_LEEWAY


def verify_jwt(
    token: str,
    issuer_config: IssuerConfig,
    now: float | None = None,
    audience: str | None = None,
    what: str = "token",
    check_times: bool = True,
) -> dict[str, Any]:
    """Check signature, ``iss``, ``exp`` and ``iat`` of ``token`` and return its claims."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise PassportVerificationError(f"{what} is not a compact JWS")
    now = time.time() if now is None else now
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise PassportVerificationError(f"{what} has an unreadable header: {e}") from e
    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise PassportVerificationError(f"{what} uses disallowed algorithm {alg!r}")
    try:
        key = issuer_config.jwks.get_signing_key(header.get("kid"))
    except JwksUnavailableError as e:
        raise VerificationUnavailableError(f"{what}: {e}") from e
    except JwksError as e:
        raise PassportVerificationError(f"{what}: {e}") from e
    options: dict[str, Any] = {
        "verify_signature": True,
        "verify_exp": False,
        "verify_iat": False,
        "verify_nbf": False,
        "verify_iss": True,
        "verify_aud": audience is not None,
        "require": ["exp", "iat", "iss", "sub"],
    }
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=ALLOWED_ALGORITHMS,
            issuer=issuer_config.issuer,
            audience=audience,
            options=options,  # type: ignore[arg-type]
        )
    except jwt.InvalidIssuerError as e:
        raise PassportVerificationError(f"{what} has the wrong issuer") from e
    except jwt.PyJWTError as e:
        raise PassportVerificationError(f"{what} failed verification: {e}") from e
    if check_times:
        check_times_valid(claims, now, issuer_config.leeway, what)
    return claims


def _int_claim(claims: dict[str, Any], name: str, what: str) -> int:
    value = claims.get(name)
    # bool is an int subclass; a JSON true is not a timestamp.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PassportVerificationError(f"{what} has a non-numeric {name}")
    return int(value)


def check_times_valid(claims: dict[str, Any], now: float, leeway: int, what: str) -> None:
    exp = _int_claim(claims, "exp", what)
    iat = _int_claim(claims, "iat", what)
    if exp <= now - leeway:
        raise TokenExpiredError(f"{what} expired at {exp}")
    if iat > now + leeway:
        raise PassportVerificationError(f"{what} was issued in the future ({iat})")
    if "nbf" in claims and _int_claim(claims, "nbf", what) > now + leeway:
        raise PassportVerificationError(f"{what} is not valid yet")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_dbgap_permissions(value: Any) -> tuple[DbgapPermission, ...]:
    """Parse ``ras_dbgap_permissions``, skipping entries with no usable ``phs_id``.

    An entry whose ``expiration`` is present but unreadable is skipped too: reading
    it as "no expiry" would let it outlive what the issuer granted.
    """
    if not isinstance(value, list):
        return ()
    permissions = []
    for entry in value:
        if not isinstance(entry, dict) or not entry.get("phs_id"):
            continue
        expiration = _optional_int(entry.get("expiration"))
        if expiration is None and entry.get("expiration") is not None:
            continue
        permissions.append(
            DbgapPermission(
                phs_id=str(entry["phs_id"]),
                version=_optional_str(entry.get("version")),
                participant_set=_optional_str(entry.get("participant_set")),
                consent_group=_optional_str(entry.get("consent_group")),
                consent_name=_optional_str(entry.get("consent_name")),
                role=_optional_str(entry.get("role")),
                expiration=expiration,
                extra={k: v for k, v in entry.items() if k not in KNOWN_PERMISSION_FIELDS},
            )
        )
    return tuple(permissions)


def _visa_from_claims(raw: str, claims: dict[str, Any]) -> Visa:
    body = claims.get("ga4gh_visa_v1")
    if not isinstance(body, dict):
        raise PassportVerificationError("visa has no ga4gh_visa_v1 object")
    return Visa(
        raw=raw,
        issuer=claims["iss"],
        subject=claims["sub"],
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
        jti=_optional_str(claims.get("jti")),
        visa_type=_optional_str(body.get("type")),
        value=_optional_str(body.get("value")),
        source=_optional_str(body.get("source")),
        by=_optional_str(body.get("by")),
        asserted=_optional_int(body.get("asserted")),
        dbgap_permissions=parse_dbgap_permissions(claims.get("ras_dbgap_permissions")),
        claims=claims,
    )


def verify_passport(raw_jwt: str, issuer_config: IssuerConfig, now: float | None = None) -> Passport:
    """Verify a RAS passport JWT and every visa in it. See the module docstring for the rules."""
    now = time.time() if now is None else now
    claims = verify_jwt(raw_jwt, issuer_config, now=now, what="passport")
    subject = claims["sub"]
    # Required even when empty: without it this could be any other token RAS signed.
    if "ga4gh_passport_v1" not in claims:
        raise PassportVerificationError("passport has no ga4gh_passport_v1 claim")
    raw_visas = claims["ga4gh_passport_v1"]
    if not isinstance(raw_visas, list):
        raise PassportVerificationError("passport ga4gh_passport_v1 is not a list")
    visas: list[Visa] = []
    dropped: list[DroppedVisa] = []
    for index, raw_visa in enumerate(raw_visas):
        what = f"visa {index}"
        try:
            visa_claims = verify_jwt(raw_visa, issuer_config, now=now, what=what, check_times=False)
        except VerificationUnavailableError:
            raise
        except PassportVerificationError as e:
            # Its claims can't be trusted, so nothing from them is recorded.
            dropped.append(DroppedVisa(jti=None, expires_at=None, reason=str(e)))
            continue
        try:
            if visa_claims["sub"] != subject:
                raise PassportVerificationError(f"{what} subject does not match the passport subject")
            check_times_valid(visa_claims, now, issuer_config.leeway, what)
            visas.append(_visa_from_claims(raw_visa, visa_claims))
        except PassportVerificationError as e:
            dropped.append(
                DroppedVisa(
                    jti=_optional_str(visa_claims.get("jti")),
                    expires_at=_optional_int(visa_claims.get("exp")),
                    reason=str(e),
                )
            )
    return Passport(
        raw=raw_jwt,
        issuer=claims["iss"],
        subject=subject,
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
        jti=_optional_str(claims.get("jti")),
        txn=_optional_str(claims.get("txn")),
        visas=tuple(visas),
        dropped_visas=tuple(dropped),
        claims=claims,
    )
