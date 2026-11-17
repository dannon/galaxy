from fastapi import (
    Response,
    status,
)

from galaxy.managers.context import ProvidesUserContext
from galaxy.schema.ras_passport import (
    RasPassportLoginCallback,
    RasPassportLoginRequest,
    RasPassportSummary,
)
from galaxy.webapps.galaxy.services.ras_passport import RasPassportService
from . import (
    depends,
    DependsOnTrans,
    Router,
)

router = Router(tags=["users"])


@router.cbv
class FastAPIRasPassport:
    service: RasPassportService = depends(RasPassportService)

    @router.get(
        "/api/users/current/ras_passport",
        summary="Summarize the current user's NIH RAS passport.",
        unstable=True,
    )
    def show(self, trans: ProvidesUserContext = DependsOnTrans) -> RasPassportSummary:
        """Whether Galaxy holds a valid NIH RAS passport for the current user, when it expires, which
        dbGaP studies it grants, and where to go for a new one when it's missing or expired."""
        return self.service.summary(trans)

    @router.post(
        "/api/users/current/ras_passport/login",
        summary="Start a direct NIH RAS login for the current user.",
        unstable=True,
    )
    def start_login(self, trans: ProvidesUserContext = DependsOnTrans) -> RasPassportLoginRequest:
        """Returns the RAS authorization URL. After logging in, paste the URL RAS redirected to into the
        callback endpoint."""
        return self.service.start_login(trans)

    @router.post(
        "/api/users/current/ras_passport/login/callback",
        summary="Finish a direct NIH RAS login with the pasted redirect URL.",
        unstable=True,
    )
    def finish_login(
        self, payload: RasPassportLoginCallback, trans: ProvidesUserContext = DependsOnTrans
    ) -> RasPassportSummary:
        """Redeems the code in the URL, verifies the passport and stores it."""
        return self.service.finish_login(trans, payload.callback_url)

    @router.delete(
        "/api/users/current/ras_passport",
        summary="Remove the current user's stored NIH RAS passport.",
        status_code=status.HTTP_204_NO_CONTENT,
        unstable=True,
    )
    def delete(self, trans: ProvidesUserContext = DependsOnTrans) -> Response:
        self.service.delete(trans)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
