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
from galaxy.schema.ras_passport import (
    RasPassportStatus,
    RasPassportSummary,
)
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
                status=RasPassportStatus.OK,
                expires_at=datetime.fromtimestamp(passport.expires_at, tz=timezone.utc),
                phs_ids=granted_phs_ids(passport, now=gate.now()),
            )
        # No phs id is asked for and anonymous users were turned away above, so the
        # gate's anonymous and no_access reasons can't reach here.
        return RasPassportSummary(
            present=False, status=RasPassportStatus(decision.reason.value), redirect_url=decision.redirect_url
        )
