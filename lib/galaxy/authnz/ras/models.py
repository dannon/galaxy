"""Typed views of a verified GA4GH passport as issued by NIH RAS.

Only :func:`galaxy.authnz.ras.verify.verify_passport` builds these, so holding a
``Passport`` means its signature, issuer and lifetime were checked.
"""

from dataclasses import (
    dataclass,
    field,
)
from typing import Any

RAS_VISA_TYPE_PREFIX = "https://ras.nih.gov/visas/"


@dataclass(frozen=True)
class DbgapPermission:
    """One entry of a RAS visa's ``ras_dbgap_permissions`` claim.

    Test accounts on RAS staging carry no permissions, so the field names here come
    from the RAS documentation rather than observed tokens. Parsing is tolerant:
    every field but ``phs_id`` is optional and anything unrecognized lands in
    ``extra``.
    """

    phs_id: str
    version: str | None = None
    participant_set: str | None = None
    consent_group: str | None = None
    consent_name: str | None = None
    role: str | None = None
    expiration: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Visa:
    # The signed JWTs are bearer credentials; keep them out of reprs and logs.
    raw: str = field(repr=False)
    issuer: str
    subject: str
    issued_at: int
    expires_at: int
    jti: str | None
    visa_type: str | None
    value: str | None
    source: str | None
    by: str | None
    asserted: int | None
    dbgap_permissions: tuple[DbgapPermission, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_ras_visa(self) -> bool:
        return bool(self.visa_type and self.visa_type.startswith(RAS_VISA_TYPE_PREFIX))


@dataclass(frozen=True)
class DroppedVisa:
    """A visa left out of the passport because it had expired."""

    jti: str | None
    expires_at: int
    reason: str


@dataclass(frozen=True)
class Passport:
    raw: str = field(repr=False)
    issuer: str
    subject: str
    issued_at: int
    expires_at: int
    jti: str | None
    txn: str | None
    visas: tuple[Visa, ...] = ()
    dropped_visas: tuple[DroppedVisa, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict, repr=False)
