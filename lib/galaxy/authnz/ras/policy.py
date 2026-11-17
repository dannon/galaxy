"""Decide what a verified passport grants. Pure functions, no I/O.

dbGaP accessions are written several ways (``phs000123``, ``PHS123``,
``phs000123.v2.p1.c1``); :func:`parse_accession` normalizes them so a permission
and a request can be compared field by field.
"""

import re
import time
from dataclasses import dataclass

from galaxy.authnz.ras.models import (
    DbgapPermission,
    Passport,
    Visa,
)

ACCESSION_RE = re.compile(
    r"^phs(?P<study>\d{1,6})(?:\.v(?P<version>\d+))?(?:\.p(?P<participant_set>\d+))?(?:\.c(?P<consent>\d+))?$",
    re.IGNORECASE,
)


class InvalidAccessionError(ValueError):
    pass


@dataclass(frozen=True)
class Accession:
    phs_id: str
    version: str | None = None
    participant_set: str | None = None
    consent_group: str | None = None


def _normalize_part(value: str | int | None, prefix: str) -> str | None:
    """``2``, ``"2"``, ``"v2"`` and ``"V2"`` all become ``"v2"``."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text.startswith(prefix):
        text = text[len(prefix) :]
    if not text.isdigit():
        raise InvalidAccessionError(f"Invalid {prefix} component {value!r}")
    return f"{prefix}{int(text)}"


def parse_accession(text: str) -> Accession:
    match = ACCESSION_RE.match(text.strip()) if isinstance(text, str) else None
    if not match:
        raise InvalidAccessionError(f"Not a dbGaP study accession: {text!r}")
    return Accession(
        phs_id=f"phs{int(match['study']):06d}",
        version=_normalize_part(match["version"], "v"),
        participant_set=_normalize_part(match["participant_set"], "p"),
        consent_group=_normalize_part(match["consent"], "c"),
    )


def normalize_phs_id(text: str) -> str:
    return parse_accession(text).phs_id


def _permission_accession(permission: DbgapPermission) -> Accession | None:
    """The permission's fields as an :class:`Accession`, or ``None`` if they don't parse."""
    try:
        base = parse_accession(permission.phs_id)
        return Accession(
            phs_id=base.phs_id,
            version=_normalize_part(permission.version, "v") or base.version,
            participant_set=_normalize_part(permission.participant_set, "p") or base.participant_set,
            consent_group=_normalize_part(permission.consent_group, "c") or base.consent_group,
        )
    except InvalidAccessionError:
        return None


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def _visa_permissions(passport: Passport) -> list[tuple[Visa, DbgapPermission]]:
    return [(visa, permission) for visa in passport.visas if visa.is_ras_visa for permission in visa.dbgap_permissions]


def carried_permissions(passport: Passport) -> list[tuple[DbgapPermission, Accession]]:
    """Every dbGaP permission in the passport's RAS visas with a readable accession, expired or not."""
    result = []
    for _, permission in _visa_permissions(passport):
        accession = _permission_accession(permission)
        if accession is not None:
            result.append((permission, accession))
    return result


def effective_expiry(passport: Passport, visa: Visa | None = None, permission: DbgapPermission | None = None) -> int:
    """The earliest of the passport's, the visa's and the permission's expiry."""
    candidates = [passport.expires_at]
    if visa is not None:
        candidates.append(visa.expires_at)
    if permission is not None and permission.expiration is not None:
        candidates.append(permission.expiration)
    return min(candidates)


@dataclass(frozen=True)
class EffectivePermission:
    permission: DbgapPermission
    accession: Accession
    visa: Visa
    expires_at: int


def permissions_for(passport: Passport, now: float | None = None) -> list[EffectivePermission]:
    """Every dbGaP permission the passport currently grants, with its effective expiry."""
    current = _now(now)
    result = []
    for visa, permission in _visa_permissions(passport):
        accession = _permission_accession(permission)
        if accession is None:
            continue
        expires_at = effective_expiry(passport, visa, permission)
        if expires_at > current:
            result.append(EffectivePermission(permission, accession, visa, expires_at))
    return result


def _matches(granted: Accession, wanted: Accession) -> bool:
    if granted.phs_id != wanted.phs_id:
        return False
    for field_name in ("version", "participant_set", "consent_group"):
        wanted_value = getattr(wanted, field_name)
        if wanted_value is not None and getattr(granted, field_name) != wanted_value:
            return False
    return True


def matching_permissions(
    passport: Passport,
    phs_id: str,
    version: str | int | None = None,
    consent_group: str | int | None = None,
    now: float | None = None,
) -> list[EffectivePermission]:
    wanted = parse_accession(phs_id)
    wanted = Accession(
        phs_id=wanted.phs_id,
        version=_normalize_part(version, "v") or wanted.version,
        participant_set=wanted.participant_set,
        consent_group=_normalize_part(consent_group, "c") or wanted.consent_group,
    )
    return [p for p in permissions_for(passport, now=now) if _matches(p.accession, wanted)]


def grants_access(
    passport: Passport,
    phs_id: str,
    version: str | int | None = None,
    consent_group: str | int | None = None,
    now: float | None = None,
) -> bool:
    """Does ``passport`` grant access to the study, optionally a specific version / consent group?

    A version or consent group that isn't asked for matches any; one that is asked
    for must match the permission exactly. Version and consent group may also be
    given inside ``phs_id`` (``phs000123.v2.c1``).
    """
    return bool(matching_permissions(passport, phs_id, version=version, consent_group=consent_group, now=now))


def granted_phs_ids(passport: Passport, now: float | None = None) -> list[str]:
    return sorted({p.accession.phs_id for p in permissions_for(passport, now=now)})
