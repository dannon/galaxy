"""Connect the current user to a GA4GH Passport broker, and report on their Passport.

``/ga4gh_passport/connect`` and ``/ga4gh_passport/callback`` are browser
endpoints (they answer with redirects); the ``/api`` ones are JSON. The
``ras_login`` pair is the development-only direct NIH RAS login, for when
there is no broker: the user pastes back the URL RAS sent them to.
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

from galaxy.authnz.ras.login import (
    DirectRasLogin,
    LOGIN_REQUEST_LIFETIME,
    PassportLoginError,
)
from galaxy.authnz.ras.models import (
    DbgapPermission,
    Passport,
)
from galaxy.authnz.ras.policy import (
    InvalidAccessionError,
    parse_accession,
)
from galaxy.exceptions import (
    AuthenticationRequired,
    RequestParameterInvalidException,
    ServerNotConfiguredForRequest,
)
from galaxy.managers._config_templates import galaxy_url_for_path
from galaxy.managers.context import ProvidesUserContext
from galaxy.managers.ga4gh_passports import (
    CALLBACK_PATH,
    CONNECT_PATH,
    PassportManager,
    PassportRefused,
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
    GA4GHRasLoginCallback,
    GA4GHRasLoginRequest,
    GA4GHStoredPassportStatus,
    GA4GHVisa,
)
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
        lookup = self.manager.lookup(user)
        passport = lookup.passport
        return GA4GHPassportStatus(
            enabled=self.manager.enabled,
            identity=_identity(identity) if identity else None,
            passport=_summary(passport, self.manager.fresh_until(passport)) if passport else None,
            passport_status=GA4GHStoredPassportStatus(lookup.status.value),
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

    @router.post(
        "/api/users/current/ga4gh_passport/ras_login",
        summary="Start a direct NIH RAS login for the current user (development only)",
        unstable=True,
    )
    def start_ras_login(self, trans: ProvidesUserContext = DependsOnTrans) -> GA4GHRasLoginRequest:
        """Returns the RAS authorization URL. After logging in, paste the URL RAS redirected to into
        ``ras_login/callback``. Needs ``direct_ras`` in ``ras_config_file``."""
        login, user = _direct_login(trans)
        try:
            url = login.start(user)
        except PassportLoginError as e:
            raise RequestParameterInvalidException(str(e))
        return GA4GHRasLoginRequest(authorization_url=url, expires_in=LOGIN_REQUEST_LIFETIME)

    @router.post(
        "/api/users/current/ga4gh_passport/ras_login/callback",
        summary="Finish a direct NIH RAS login with the pasted redirect URL (development only)",
        unstable=True,
    )
    def finish_ras_login(
        self, payload: GA4GHRasLoginCallback, trans: ProvidesUserContext = DependsOnTrans
    ) -> GA4GHPassportStatus:
        """Redeems the code in the URL and takes the Passport like a connect does: verifies it, binds its
        identity to the current user, stores it and syncs the user's grant roles."""
        login, user = _direct_login(trans)
        try:
            token = login.finish(user, payload.callback_url)
        except PassportLoginError as e:
            raise RequestParameterInvalidException(str(e))
        try:
            self.manager.accept(user, token)
        except PassportRefused as e:
            raise RequestParameterInvalidException(str(e), ga4gh_passport_error=e.code)
        return self.show(user)


def _direct_login(trans: ProvidesUserContext) -> tuple[DirectRasLogin, User]:
    gate = trans.app.ras_passport_gate
    if gate is None or gate.direct_login is None:
        raise ServerNotConfiguredForRequest("Direct NIH RAS login is not enabled on this Galaxy.")
    if trans.user is None:
        raise AuthenticationRequired("You must be logged in to log in to NIH RAS.")
    return gate.direct_login, trans.user


def _utc(naive: datetime) -> datetime:
    """A naive UTC datetime as stored by Galaxy, made timezone-aware."""
    return naive.replace(tzinfo=timezone.utc)


def _time(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _summary(passport: Passport, usable_until: int) -> GA4GHPassportSummary:
    visas_expire_at = min((visa.expires_at for visa in passport.visas), default=None)
    return GA4GHPassportSummary(
        issuer=passport.issuer,
        sub=passport.subject,
        txn=passport.txn,
        issued_at=_time(passport.issued_at),
        expires_at=_time(passport.expires_at),
        visas_expire_at=_time(visas_expire_at) if visas_expire_at is not None else None,
        usable_until=_time(usable_until),
        visas=[
            GA4GHVisa(
                type=visa.visa_type or "",
                issuer=visa.issuer,
                recognized=visa.is_ras_visa,
                expires_at=_time(visa.expires_at),
                grants=[grant for grant in map(_grant, visa.dbgap_permissions) if grant is not None],
            )
            for visa in passport.visas
        ],
        rejected_visas=len(passport.dropped_visas),
    )


def _grant(permission: DbgapPermission) -> GA4GHGrant | None:
    """A permission as the API reports it; None if it lacks what a grant needs (study, consent group, expiration)."""
    if permission.consent_group is None or permission.expiration is None:
        return None
    try:
        accession = parse_accession(permission.phs_id)
    except InvalidAccessionError:
        return None
    return GA4GHGrant(
        phs_id=permission.phs_id,
        version=permission.version or "",
        participant_set=permission.participant_set or "",
        consent_group=permission.consent_group,
        consent_name=permission.consent_name or "",
        role=permission.role or "",
        authorization=f"{accession.phs_id}.{permission.consent_group}",
        expiration=_time(permission.expiration),
    )


def _identity(identity: UserGA4GHIdentity) -> GA4GHIdentity:
    return GA4GHIdentity(
        issuer=identity.issuer,
        sub=identity.sub,
        preferred_username=identity.preferred_username,
        bound_since=identity.create_time,
    )
