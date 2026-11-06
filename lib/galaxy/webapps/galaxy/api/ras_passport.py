from galaxy.managers.context import ProvidesUserContext
from galaxy.schema.ras_passport import RasPassportSummary
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
