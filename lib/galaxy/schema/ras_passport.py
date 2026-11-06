from datetime import datetime
from enum import Enum

from pydantic import Field

from galaxy.schema.schema import Model


class RasPassportStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    EXPIRED = "expired"
    INVALID = "invalid"
    # The issuer's keys couldn't be fetched, so the stored passport can't be checked right now.
    UNAVAILABLE = "unavailable"


class RasPassportSummary(Model):
    present: bool = Field(..., description="Whether Galaxy holds a valid NIH RAS passport for the current user.")
    status: RasPassportStatus = Field(..., description="Why there is or isn't a usable passport.")
    expires_at: datetime | None = Field(None, description="When the passport expires (UTC).")
    phs_ids: list[str] = Field(
        default_factory=list, description="dbGaP studies the passport currently grants access to."
    )
    redirect_url: str | None = Field(
        None,
        description="Where to send the user to get a new passport, when there is no valid one and a bridge is configured.",
    )
