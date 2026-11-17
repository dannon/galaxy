from datetime import datetime

from pydantic import (
    BaseModel,
    Field,
)


class GA4GHGrant(BaseModel):
    phs_id: str
    version: str
    participant_set: str
    consent_group: str
    consent_name: str
    role: str
    authorization: str = Field(description="Study and consent group, e.g. phs000001.c1.")
    expiration: datetime = Field(description="When the grant (the data access request) expires.")


class GA4GHVisa(BaseModel):
    type: str
    issuer: str
    recognized: bool = Field(description="False for Visa types Galaxy does not understand; they grant nothing.")
    expires_at: datetime
    grants: list[GA4GHGrant]


class GA4GHPassportSummary(BaseModel):
    """What a stored Passport says. The Passport itself is never returned."""

    issuer: str
    sub: str
    txn: str | None = Field(None, description="Login transaction id at the issuer (NIH RAS), for audit.")
    issued_at: datetime
    expires_at: datetime
    visas_expire_at: datetime | None = Field(None, description="Earliest Visa expiry.")
    usable_until: datetime = Field(
        description=(
            "Until when Galaxy sends the Passport to data services: the earliest of its expiry, its Visas' "
            "expiry and ga4gh_passport_max_age after issue. Reconnect to get a new one."
        )
    )
    visas: list[GA4GHVisa]
    rejected_visas: int = Field(description="Visas dropped because they failed verification.")


class GA4GHIdentity(BaseModel):
    issuer: str
    sub: str
    preferred_username: str | None
    bound_since: datetime


class GA4GHDataAccess(BaseModel):
    authorization: str = Field(description="Study and consent group, e.g. phs000001.c1.")
    until: datetime = Field(
        description=(
            "Until when the user can use data fetched under this grant in Galaxy: the grant's expiration, "
            "capped by ga4gh_grant_max_membership after the last connect."
        )
    )


class GA4GHPassportStatus(BaseModel):
    enabled: bool = Field(description="Whether this Galaxy is configured for GA4GH Passport connections.")
    identity: GA4GHIdentity | None = Field(None, description="The Passport identity bound to this user, if any.")
    passport: GA4GHPassportSummary | None = Field(None, description="The stored Passport, if present and valid.")
    data_access: list[GA4GHDataAccess] = Field(
        description="Grants the user holds in Galaxy (as roles), from their last connected Passport.",
    )
