from datetime import datetime

from pydantic import Field

from galaxy.schema.schema import Model


class RasPassportSummary(Model):
    present: bool = Field(..., description="Whether Galaxy holds a valid NIH RAS passport for the current user.")
    status: str = Field(
        ...,
        description="Why there is or isn't a usable passport: ok, missing, expired or invalid.",
    )
    expires_at: datetime | None = Field(None, description="When the passport expires (UTC).")
    phs_ids: list[str] = Field(
        default_factory=list, description="dbGaP studies the passport currently grants access to."
    )
    redirect_url: str | None = Field(
        None,
        description="Where to send the user to get a new passport, when there is no valid one and a bridge is configured.",
    )
