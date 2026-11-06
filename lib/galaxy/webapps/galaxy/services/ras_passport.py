from datetime import (
    datetime,
    timezone,
)

from galaxy.authnz.ras.gate import (
    PassportGate,
    PassportOk,
)
from galaxy.authnz.ras.policy import granted_phs_ids
from galaxy.exceptions import (
    AuthenticationRequired,
    ServerNotConfiguredForRequest,
)
from galaxy.managers.context import ProvidesUserContext
from galaxy.schema.ras_passport import RasPassportSummary
from galaxy.structured_app import MinimalManagerApp


class RasPassportService:
    def __init__(self, app: MinimalManagerApp):
        self.app = app

    @property
    def gate(self) -> PassportGate:
        gate = self.app.ras_passport_gate
        if gate is None:
            raise ServerNotConfiguredForRequest("NIH RAS passport support is not enabled on this Galaxy.")
        return gate

    def summary(self, trans: ProvidesUserContext) -> RasPassportSummary:
        gate = self.gate
        if trans.anonymous:
            raise AuthenticationRequired("You must be logged in to check your NIH RAS passport.")
        decision = gate.require(trans.user)
        if isinstance(decision, PassportOk):
            passport = decision.passport
            return RasPassportSummary(
                present=True,
                status="ok",
                expires_at=datetime.fromtimestamp(passport.expires_at, tz=timezone.utc),
                phs_ids=granted_phs_ids(passport, now=gate.now()),
            )
        return RasPassportSummary(present=False, status=decision.reason.value, redirect_url=decision.redirect_url)
