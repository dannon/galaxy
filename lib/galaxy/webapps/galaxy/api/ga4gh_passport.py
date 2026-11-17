"""Connect the current user to a GA4GH Passport broker, and report on their Passport.

``/ga4gh_passport/connect`` and ``/ga4gh_passport/callback`` are browser
endpoints (they answer with redirects); the ``/api`` ones are JSON.
"""

from datetime import (
    datetime,
    timezone,
)
from urllib.parse import (
    quote,
    urlencode,
)

from fastapi import (
    Query,
    Response,
    status,
)
from fastapi.responses import RedirectResponse

from galaxy.managers._config_templates import galaxy_url_for_path
from galaxy.managers.ga4gh_passports import (
    CALLBACK_PATH,
    CONNECT_PATH,
    PassportManager,
)
from galaxy.model import (
    User,
    UserGA4GHIdentity,
)
from galaxy.schema.ga4gh_passport import (
    GA4GHDataAccess,
    GA4GHGrant,
    GA4GHIdentity,
    GA4GHPassportStatus,
    GA4GHPassportSummary,
    GA4GHVisa,
)
from galaxy.security.ga4gh_passport import VerifiedPassport
from galaxy.util import is_safe_local_redirect
from galaxy.webapps.galaxy.api import (
    depends,
    DependsOnTrans,
    DependsOnUser,
    Router,
)
from galaxy.work.context import SessionRequestContext

router = Router(tags=["ga4gh_passport"])

NextQueryParam: str = Query(
    "/",
    title="Where to return to",
    description="Galaxy path to send the browser to once the connect finishes.",
)


@router.cbv
class FastAPIGA4GHPassport:
    manager: PassportManager = depends(PassportManager)

    @router.get(
        f"/{CONNECT_PATH}",
        summary="Start connecting the current user to their GA4GH Passport (browser redirect to the broker)",
        response_class=RedirectResponse,
        status_code=status.HTTP_302_FOUND,
    )
    def connect(
        self,
        trans: SessionRequestContext = DependsOnTrans,
        next: str = NextQueryParam,
    ) -> RedirectResponse:
        if not is_safe_local_redirect(next):
            next = "/"
        if trans.user is None:
            # Log in first, then come back here.
            here = f"/{CONNECT_PATH}?{urlencode({'next': next})}"
            login_url = galaxy_url_for_path(trans, f"login/start?redirect={quote(here, safe='')}")
            return RedirectResponse(login_url, status_code=status.HTTP_302_FOUND)
        url = self.manager.connect_url(trans, galaxy_url_for_path(trans, CALLBACK_PATH), next)
        return RedirectResponse(url, status_code=status.HTTP_302_FOUND)

    @router.get(
        f"/{CALLBACK_PATH}",
        summary="Finish connecting to a GA4GH Passport (the broker redirects the browser here)",
        response_class=RedirectResponse,
        status_code=status.HTTP_302_FOUND,
    )
    def callback(
        self,
        trans: SessionRequestContext = DependsOnTrans,
        state: str | None = Query(None),
        code: str | None = Query(None),
        error: str | None = Query(None),
    ) -> RedirectResponse:
        result = self.manager.complete_connect(
            trans, galaxy_url_for_path(trans, CALLBACK_PATH), state=state, code=code, error=error
        )
        outcome = {"ga4gh_passport_error": result.error} if result.error else {"ga4gh_passport": "connected"}
        separator = "&" if "?" in result.next_path else "?"
        return RedirectResponse(
            galaxy_url_for_path(trans, f"{result.next_path}{separator}{urlencode(outcome)}"),
            status_code=status.HTTP_302_FOUND,
        )

    @router.get(
        "/api/users/current/ga4gh_passport",
        summary="The current user's GA4GH Passport identity and stored Passport (never the Passport itself)",
    )
    def show(self, user: User = DependsOnUser) -> GA4GHPassportStatus:
        identity = self.manager.identity(user)
        passport = self.manager.current(user)
        return GA4GHPassportStatus(
            enabled=self.manager.enabled,
            identity=_identity(identity) if identity else None,
            passport=_summary(passport, self.manager.fresh_until(passport)) if passport else None,
            data_access=[
                GA4GHDataAccess(authorization=authorization, until=_utc(until), ended=False)
                for authorization, until in sorted(self.manager.data_access(user).items())
            ]
            + [
                GA4GHDataAccess(authorization=authorization, until=_utc(ended), ended=True, delete_after=_utc(delete))
                for authorization, (ended, delete) in sorted(self.manager.ended_access(user).items())
            ],
        )

    @router.delete(
        "/api/users/current/ga4gh_passport",
        summary="Delete the current user's stored GA4GH Passport (the identity binding stays)",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def forget(self, user: User = DependsOnUser) -> Response:
        self.manager.forget(user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _utc(naive: datetime) -> datetime:
    """A naive UTC datetime as stored by Galaxy, made timezone-aware."""
    return naive.replace(tzinfo=timezone.utc)


def _time(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _summary(passport: VerifiedPassport, usable_until: int) -> GA4GHPassportSummary:
    return GA4GHPassportSummary(
        issuer=passport.issuer,
        sub=passport.sub,
        txn=passport.txn,
        issued_at=_time(passport.issued_at),
        expires_at=_time(passport.expires_at),
        visas_expire_at=_time(passport.visas_expire_at) if passport.visas_expire_at is not None else None,
        usable_until=_time(usable_until),
        visas=[
            GA4GHVisa(
                type=visa.type,
                issuer=visa.issuer,
                recognized=visa.recognized,
                expires_at=_time(visa.expires_at),
                grants=[
                    GA4GHGrant(
                        phs_id=grant.phs_id,
                        version=grant.version,
                        participant_set=grant.participant_set,
                        consent_group=grant.consent_group,
                        consent_name=grant.consent_name,
                        role=grant.role,
                        authorization=grant.authorization,
                        expiration=_time(grant.expiration),
                    )
                    for grant in visa.grants
                ],
            )
            for visa in passport.visas
        ],
        rejected_visas=len(passport.rejected_visas),
    )


def _identity(identity: UserGA4GHIdentity) -> GA4GHIdentity:
    return GA4GHIdentity(
        issuer=identity.issuer,
        sub=identity.sub,
        preferred_username=identity.preferred_username,
        bound_since=identity.create_time,
    )
