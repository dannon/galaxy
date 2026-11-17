from datetime import (
    datetime,
    timezone,
)

from galaxy.authnz.ras.gate import (
    PassportGate,
    PassportOk,
)
from galaxy.authnz.ras.login import (
    DirectRasLogin,
    LOGIN_REQUEST_LIFETIME,
    PassportLoginError,
)
from galaxy.authnz.ras.policy import granted_phs_ids
from galaxy.exceptions import (
    AuthenticationRequired,
    RequestParameterInvalidException,
    ServerNotConfiguredForRequest,
)
from galaxy.managers.context import ProvidesUserContext
from galaxy.schema.ras_passport import (
    RasPassportLoginRequest,
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

    def _direct_login(self, trans: ProvidesUserContext) -> DirectRasLogin:
        gate = self.gate
        if gate.direct_login is None:
            raise ServerNotConfiguredForRequest("Direct NIH RAS login is not enabled on this Galaxy.")
        if trans.anonymous:
            raise AuthenticationRequired("You must be logged in to log in to NIH RAS.")
        return gate.direct_login

    def start_login(self, trans: ProvidesUserContext) -> RasPassportLoginRequest:
        login = self._direct_login(trans)
        try:
            url = login.start(trans.user)
        except PassportLoginError as e:
            raise RequestParameterInvalidException(str(e))
        return RasPassportLoginRequest(authorization_url=url, expires_in=LOGIN_REQUEST_LIFETIME)

    def finish_login(self, trans: ProvidesUserContext, callback_url: str) -> RasPassportSummary:
        login = self._direct_login(trans)
        try:
            login.finish(trans.user, callback_url)
        except PassportLoginError as e:
            raise RequestParameterInvalidException(str(e))
        return self.summary(trans)

    def delete(self, trans: ProvidesUserContext) -> None:
        gate = self.gate
        if trans.anonymous:
            raise AuthenticationRequired("You must be logged in to remove your NIH RAS passport.")
        gate.store.clear(trans.user)

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
