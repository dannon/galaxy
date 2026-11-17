"""Connect Galaxy users to a GA4GH Passport broker and keep their Passports.

Galaxy is an OAuth 2.0 client of the broker (authorization code + PKCE):

1. ``connect_url``: the browser is sent to the broker. ``state`` and the PKCE
   verifier are stored with ``FlowState``, bound to the Galaxy session.
2. ``complete_connect``: the broker sends the browser back with a one-time
   code. Galaxy exchanges it (back channel, client secret + PKCE verifier) for
   the Passport, verifies the Passport itself, checks the Passport identity
   against the one bound to the user, and stores the Passport in the user's
   vault (encrypted at rest with the database vault).
3. ``current``: the user's stored Passport, verified again on every read. This
   is the interface the rest of Galaxy uses; ``fresh_until`` says how long it
   may still be sent to data services.
4. ``sync_grant_roles``: each data access grant in the Passport becomes a role
   only this user holds (``Role.types.GA4GH_GRANT``), with a membership that
   expires with the grant, capped at ``ga4gh_grant_max_membership`` after the
   connect. Data fetched with the Passport is restricted to these roles, so
   access to it ends when the membership does, without asking the issuer again.
5. ``purge_ended_controlled_data``: once a grant's membership has been ended
   for ``ga4gh_controlled_data_grace_period``, the data restricted to it is
   purged, and the ended membership deleted.

The broker returns the Passport untouched; all decoding happens here, through
``galaxy.security.ga4gh_passport``.
"""

import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import (
    Any,
    TYPE_CHECKING,
)
from urllib.parse import urlencode

from pkce import generate_pkce_pair
from sqlalchemy import (
    delete,
    false,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError

from galaxy import exceptions
from galaxy.authnz.flow_state import (
    authentication_id,
    FlowState,
    InvalidFlow,
)
from galaxy.config import GalaxyAppConfiguration
from galaxy.files.sources.drs import RECONNECT_HINT
from galaxy.model import (
    Dataset,
    DatasetPermissions,
    HistoryDatasetAssociation,
    Role,
    User,
    UserGA4GHIdentity,
    UserRoleAssociation,
)
from galaxy.model.orm.now import now
from galaxy.model.scoped_session import galaxy_scoped_session
from galaxy.security.ga4gh_passport import (
    InvalidPassport,
    PassportVerifier,
    VerifiedPassport,
)
from galaxy.security.vault import (
    is_vault_configured,
    UserVaultWrapper,
    Vault,
)
from galaxy.util import (
    listify,
    requests,
)
from galaxy.work.context import SessionRequestContext

if TYPE_CHECKING:
    from galaxy.managers.hdas import HDAManager

log = logging.getLogger(__name__)

PASSPORT_VAULT_KEY = "ga4gh_passport"
FLOW_PROVIDER = "ga4gh_passport"
GRANT_ROLE_PREFIX = "ga4gh_grant"
# Manages every dataset fetched with a Passport (and derived from one). No user
# holds it or can be given it, and admins get no override for such data, so no
# one can change those datasets' permissions through Galaxy.
CONTROLLED_DATA_MANAGE_ROLE = "ga4gh_controlled_data"
CONNECT_PATH = "ga4gh_passport/connect"
CALLBACK_PATH = "ga4gh_passport/callback"
TOKEN_REQUEST_TIMEOUT = 10
# OAuth errors a broker may send back (RFC 6749 4.1.2.1), passed on as they are.
BROKER_ERRORS = {
    "access_denied",
    "invalid_request",
    "unauthorized_client",
    "unsupported_response_type",
    "invalid_scope",
    "server_error",
    "temporarily_unavailable",
}


@dataclass(frozen=True)
class PassportBrokerConfig:
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: str
    trusted_issuers: tuple[str, ...]

    @staticmethod
    def from_config(config: GalaxyAppConfiguration) -> "PassportBrokerConfig | None":
        authorize_url = config.ga4gh_passport_broker_authorize_url
        if not authorize_url:
            return None
        settings = {
            "ga4gh_passport_broker_token_url": config.ga4gh_passport_broker_token_url,
            "ga4gh_passport_broker_client_id": config.ga4gh_passport_broker_client_id,
            "ga4gh_passport_broker_client_secret": config.ga4gh_passport_broker_client_secret,
            "ga4gh_passport_trusted_issuers": config.ga4gh_passport_trusted_issuers,
        }
        if missing := [name for name, value in settings.items() if not value]:
            raise exceptions.ConfigurationError(
                f"ga4gh_passport_broker_authorize_url is set, but these options are not: {', '.join(missing)}"
            )
        return PassportBrokerConfig(
            authorize_url=authorize_url,
            token_url=str(settings["ga4gh_passport_broker_token_url"]),
            client_id=str(settings["ga4gh_passport_broker_client_id"]),
            client_secret=str(settings["ga4gh_passport_broker_client_secret"]),
            trusted_issuers=tuple(listify(settings["ga4gh_passport_trusted_issuers"], do_strip=True)),
        )


@dataclass(frozen=True)
class ConnectResult:
    """Where to send the browser after the broker returns, and why if it failed."""

    next_path: str
    error: str | None = None


class PassportManager:
    def __init__(self, config: GalaxyAppConfiguration, sa_session: galaxy_scoped_session, vault: Vault) -> None:
        self._broker = PassportBrokerConfig.from_config(config)
        self._sa_session = sa_session
        self._vault = vault
        self._verifier = PassportVerifier(self._broker.trusted_issuers) if self._broker else None
        self._max_age = config.ga4gh_passport_max_age
        self._grant_max_membership = config.ga4gh_grant_max_membership
        self._grace_period = config.ga4gh_controlled_data_grace_period

    @property
    def enabled(self) -> bool:
        return self._broker is not None

    # --- connect flow ------------------------------------------------------------------

    def connect_url(self, trans: SessionRequestContext, redirect_uri: str, next_path: str) -> str:
        """Start a connect for the logged-in user; return the broker URL to send the browser to."""
        broker = self._require_broker()
        if not is_vault_configured(self._vault):
            raise exceptions.ServerNotConfiguredForRequest("GA4GH Passports need a configured vault.")
        user = trans.user
        if user is None:
            raise exceptions.AuthenticationRequired("Log in to Galaxy before connecting a GA4GH Passport.")
        verifier, challenge = generate_pkce_pair(96)
        state = secrets.token_urlsafe(32)
        flow = FlowState(trans.sa_session, trans.galaxy_session, FLOW_PROVIDER)
        flow.save(
            "authentication",
            {"state": state, "verifier": verifier, "next": next_path, "user_id": user.id},
            identifier=authentication_id(state),
        )
        query = {
            "client_id": broker.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        log.info("GA4GH passport connect started user_id=%s", user.id)
        return f"{broker.authorize_url}?{urlencode(query)}"

    def complete_connect(
        self,
        trans: SessionRequestContext,
        redirect_uri: str,
        state: str | None,
        code: str | None,
        error: str | None,
    ) -> ConnectResult:
        """Handle the broker's redirect back to Galaxy. Never raises for expected failures."""
        broker = self._require_broker()
        try:
            flow = FlowState(trans.sa_session, trans.galaxy_session, FLOW_PROVIDER)
            payload = flow.consume("authentication", authentication_id(state))
        except InvalidFlow:
            return ConnectResult("/", "invalid_flow")
        next_path = payload["next"]
        user = trans.user
        if payload.get("state") != state or user is None or user.id != payload.get("user_id"):
            return ConnectResult(next_path, "invalid_flow")
        if error:
            log.info("GA4GH passport connect: broker returned error=%s user_id=%s", error, user.id)
            return ConnectResult(next_path, error if error in BROKER_ERRORS else "broker_error")
        if not code:
            return ConnectResult(next_path, "invalid_request")

        token, preferred_username = self._exchange_code(broker, redirect_uri, code, payload["verifier"])
        if token is None:
            return ConnectResult(next_path, "broker_error")
        try:
            passport = self._get_verifier().verify(token)
        except InvalidPassport as e:
            log.warning("GA4GH passport connect: %s user_id=%s", e, user.id)
            return ConnectResult(next_path, "invalid_passport")
        if bind_error := self._bind_identity(user, passport, preferred_username):
            return ConnectResult(next_path, bind_error)

        record = {"passport": token, "preferred_username": preferred_username, "received_at": int(time.time())}
        UserVaultWrapper(self._vault, user).write_secret(PASSPORT_VAULT_KEY, json.dumps(record))
        self.sync_grant_roles(user, passport)
        log.info(
            "GA4GH passport connected user_id=%s iss=%s sub=%s txn=%s preferred_username=%s grants=%s",
            user.id,
            passport.issuer,
            passport.sub,
            passport.txn,
            preferred_username,
            ",".join(grant.authorization for grant in passport.grants) or "-",
        )
        return ConnectResult(next_path)

    def _exchange_code(
        self, broker: PassportBrokerConfig, redirect_uri: str, code: str, verifier: str
    ) -> tuple[str | None, str | None]:
        try:
            response = requests.post(
                broker.token_url,
                auth=(broker.client_id, broker.client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                timeout=TOKEN_REQUEST_TIMEOUT,
            )
            body = response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            log.warning("GA4GH passport broker token request failed: %s", e)
            return None, None
        if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("passport"), str):
            # Log the OAuth error only; the body of a successful response holds the Passport.
            oauth_error = body.get("error") if isinstance(body, dict) else None
            log.warning("GA4GH passport broker token request: HTTP %s error=%s", response.status_code, oauth_error)
            return None, None
        preferred_username = body.get("preferred_username")
        return body["passport"], preferred_username if isinstance(preferred_username, str) else None

    def _bind_identity(self, user: User, passport: VerifiedPassport, preferred_username: str | None) -> str | None:
        """Bind the Passport identity to the user on first connect; refuse a different one later."""
        session = self._sa_session
        bound = session.scalars(
            select(UserGA4GHIdentity).filter_by(issuer=passport.issuer, sub=passport.sub)
        ).one_or_none()
        if bound is not None and bound.user_id != user.id:
            log.warning(
                "GA4GH passport connect refused: identity %s/%s is bound to another user, user_id=%s",
                passport.issuer,
                passport.sub,
                user.id,
            )
            return "identity_in_use"
        mine = session.scalars(
            select(UserGA4GHIdentity).filter_by(user_id=user.id, issuer=passport.issuer)
        ).one_or_none()
        if mine is not None and mine.sub != passport.sub:
            log.warning(
                "GA4GH passport connect refused: user_id=%s is bound to %s/%s, got sub=%s",
                user.id,
                passport.issuer,
                mine.sub,
                passport.sub,
            )
            return "identity_mismatch"
        if mine is None:
            mine = UserGA4GHIdentity(user_id=user.id, issuer=passport.issuer, sub=passport.sub)
            log.info("GA4GH passport identity bound user_id=%s iss=%s sub=%s", user.id, passport.issuer, passport.sub)
        mine.preferred_username = preferred_username
        session.add(mine)
        try:
            session.commit()
        except IntegrityError:
            # A concurrent connect bound this identity (or this user) first.
            session.rollback()
            return "identity_in_use"
        return None

    # --- the stored Passport ------------------------------------------------------------

    def current(self, user: User) -> VerifiedPassport | None:
        """The user's stored Passport if it still verifies (signatures, expiry), else None."""
        record = self._read(user)
        if record is None:
            return None
        try:
            return self._get_verifier().verify(record["passport"])
        except InvalidPassport as e:
            log.info("GA4GH passport of user_id=%s no longer valid: %s", user.id, e)
            return None

    def fresh_until(self, passport: VerifiedPassport) -> int:
        """Until when (epoch seconds) Galaxy may send ``passport`` to a data service.

        The earliest of: the Passport's expiry, its Visas' expiry, and
        ``ga4gh_passport_max_age`` after the Passport or its oldest Visa was
        issued. Visas are access tokens: past that age a data service should
        reject them unless it re-validates them with the issuer.
        """
        issued_at = min([passport.issued_at, *(visa.issued_at for visa in passport.visas)])
        limits = [passport.expires_at, issued_at + self._max_age]
        if passport.visas_expire_at is not None:
            limits.append(passport.visas_expire_at)
        return min(limits)

    def identity(self, user: User) -> UserGA4GHIdentity | None:
        if self._broker is None:
            return None
        return self._sa_session.scalars(
            select(UserGA4GHIdentity).filter_by(user_id=user.id).order_by(UserGA4GHIdentity.id)
        ).first()

    # --- grant roles ---------------------------------------------------------------------

    def sync_grant_roles(self, user: User, passport: VerifiedPassport) -> dict[str, Role]:
        """Make the user's grant roles match ``passport``; return them by authorization.

        A grant's membership lasts until the grant expires, at most
        ``ga4gh_grant_max_membership`` from now. Grant roles the user holds
        that are not in ``passport`` (revoked or expired at the issuer) end now.
        """
        current = now()
        cap = current + timedelta(seconds=self._grant_max_membership)
        until: dict[str, datetime] = {}
        for grant in passport.grants:
            # The same grant may come in more than one Visa; any of them is enough.
            expiration = _utc(grant.expiration)
            until[grant.authorization] = max(until.get(grant.authorization, expiration), expiration)
        held = {_grant_authorization(ura.role, user): ura for ura in self._grant_memberships(user)}
        roles: dict[str, Role] = {}
        for authorization, grant_until in sorted(until.items()):
            expiration_time = min(grant_until, cap)
            if (ura := held.get(authorization)) is None:
                role = self._get_or_create_grant_role(user, authorization)
                ura = UserRoleAssociation(user, role, expiration_time=expiration_time)
                self._sa_session.add(ura)
            else:
                ura.expiration_time = expiration_time
            roles[authorization] = ura.role
        ended = []
        for authorization, ura in held.items():
            if authorization not in until and ura.is_active:
                ura.expiration_time = current
                ended.append(authorization)
        self._sa_session.commit()
        log.info(
            "GA4GH grant roles synced user_id=%s txn=%s held=%s ended=%s",
            user.id,
            passport.txn,
            ",".join(f"{a}@{min(u, cap):%Y-%m-%dT%H:%M:%SZ}" for a, u in sorted(until.items())) or "-",
            ",".join(sorted(ended)) or "-",
        )
        return roles

    def fetch_roles(self, user: User | None) -> tuple[list[Role], Role] | None:
        """The roles to restrict data fetched now with the user's Passport to.

        Returns the user's grant roles (synced from the Passport; access needs
        all of them, as Galaxy can't tell which grant covers an object) and the
        manage role no one holds. None if the user has no Passport: nothing is
        sent, so no controlled data can come back (a controlled object fails
        in the job, telling the user to connect). Raises if the Passport is too
        old to send (the fetch would fail anyway), or carries no grants Galaxy
        understands (a repository could still accept it, and Galaxy would have
        no role to restrict the data to).
        """
        if user is None or (passport := self.current(user)) is None:
            return None
        if self.fresh_until(passport) <= time.time():
            raise exceptions.AuthenticationRequired(
                f"Your GA4GH Passport is too old to send to a data repository. {RECONNECT_HINT}"
            )
        roles = self.sync_grant_roles(user, passport)
        if not roles:
            raise exceptions.ItemAccessibilityException(
                "Your GA4GH Passport carries no data access grants Galaxy can restrict fetched data to. Forget it "
                "(User Preferences, /user/ga4gh_passport) to fetch public data from this repository."
            )
        return [roles[authorization] for authorization in sorted(roles)], self._controlled_data_manage_role()

    def _controlled_data_manage_role(self) -> Role:
        stmt = select(Role).filter_by(name=CONTROLLED_DATA_MANAGE_ROLE, type=Role.types.GA4GH_GRANT).order_by(Role.id)
        role = self._sa_session.scalars(stmt).first()
        if role is None:
            role = Role(
                name=CONTROLLED_DATA_MANAGE_ROLE,
                description="Manages data fetched with a GA4GH Passport and data derived from it. No user holds "
                "it, so no one can change the permissions of such data.",
                type=Role.types.GA4GH_GRANT,
            )
            self._sa_session.add(role)
            self._sa_session.commit()
        return role

    def controlled_access_ended(self, user: User | None, dataset: Dataset) -> tuple[str, datetime | None] | None:
        """Why ``user`` can no longer read controlled ``dataset`` they fetched (or derived from such data).

        Returns the grant whose membership ended first and when (None if no
        membership is left), or None if the user can still read it or it isn't
        their controlled data.
        """
        if user is None or not dataset.is_controlled:
            return None
        prefix = f"{GRANT_ROLE_PREFIX}:{user.id}:"
        access = Dataset.permitted_actions.DATASET_ACCESS.action
        roles = [
            p.role
            for p in dataset.actions
            if p.action == access
            and p.role is not None
            and p.role.type == Role.types.GA4GH_GRANT
            and p.role.name.startswith(prefix)
        ]
        memberships = {ura.role_id: ura for ura in self._grant_memberships(user)}
        ended: list[tuple[str, datetime | None]] = []
        for role in roles:
            ura = memberships.get(role.id)
            if ura is None or not ura.is_active:
                ended.append((_grant_authorization(role, user), ura.expiration_time if ura else None))
        if not ended:
            return None
        return min(ended, key=lambda e: e[1] or datetime.min)

    def controlled_access_message(self, user: User | None, dataset: Dataset) -> str | None:
        """What to tell ``user`` about controlled ``dataset`` they can no longer read, if it is theirs."""
        if (ended := self.controlled_access_ended(user, dataset)) is None:
            return None
        authorization, ended_at = ended
        if ended_at is None:
            return f"Your access to this controlled-access data ({authorization}) has ended. {RECONNECT_HINT}"
        delete_after = ended_at + timedelta(seconds=self._grace_period)
        return (
            f"Your access to this controlled-access data ({authorization}) ended on {ended_at:%Y-%m-%d %H:%M} UTC. "
            f"Reconnect your GA4GH Passport in User Preferences (/user/ga4gh_passport) to restore it; otherwise it "
            f"will be deleted after {delete_after:%Y-%m-%d %H:%M} UTC."
        )

    def data_access(self, user: User) -> dict[str, datetime]:
        """The grants the user currently holds in Galaxy, and until when (UTC)."""
        return {
            _grant_authorization(ura.role, user): ura.expiration_time or datetime.max
            for ura in self._grant_memberships(user)
            if ura.is_active
        }

    def ended_access(self, user: User) -> dict[str, tuple[datetime, datetime]]:
        """Grants whose membership has ended, data not purged yet: when it ended, and when its data is deleted."""
        return {
            _grant_authorization(ura.role, user): (
                ura.expiration_time,
                ura.expiration_time + timedelta(seconds=self._grace_period),
            )
            for ura in self._grant_memberships(user)
            if not ura.is_active and ura.expiration_time is not None
        }

    # --- purge after the grace period ----------------------------------------------------

    def purge_ended_controlled_data(self, hda_manager: "HDAManager") -> int:
        """Purge controlled data whose grant ended more than the grace period ago; return how many datasets.

        A dataset is purged when any of its ``ga4gh_grant`` access roles has
        no membership that is current or ended within the grace period (no
        membership at all counts as ended: its user was purged). Every HDA of
        it is purged, in every history, and then its files. This is policy,
        so ``allow_user_dataset_purge`` doesn't apply. Then the ended grant
        memberships are deleted, except those of datasets left for the next
        run: a dataset whose job hasn't finished, or whose purge failed.
        """
        cutoff = now() - timedelta(seconds=self._grace_period)
        session = self._sa_session
        held = (
            select(UserRoleAssociation.id)
            .where(UserRoleAssociation.role_id == Role.id)
            .where(or_(UserRoleAssociation.expiration_time.is_(None), UserRoleAssociation.expiration_time > cutoff))
        )
        stmt = (
            select(Dataset, Role)
            .join(DatasetPermissions, DatasetPermissions.dataset_id == Dataset.id)
            .join(Role, Role.id == DatasetPermissions.role_id)
            .where(
                Role.type == Role.types.GA4GH_GRANT,
                DatasetPermissions.action == Dataset.permitted_actions.DATASET_ACCESS.action,
                Dataset.purged == false(),
                ~held.exists(),
            )
            .order_by(Dataset.id, Role.id)
        )
        ended: dict[Dataset, list[Role]] = {}
        for dataset, role in session.execute(stmt):
            ended.setdefault(dataset, []).append(role)

        purged = 0
        kept_roles: set[int] = set()
        for dataset, roles in ended.items():
            if dataset.state in Dataset.uncharged_states:
                # Its job hasn't finished; mark_purged() would skip its HDAs.
                kept_roles.update(role.id for role in roles)
                continue
            try:
                self._purge_dataset(hda_manager, dataset)
            except Exception:
                log.exception("GA4GH controlled data purge failed dataset_id=%s", dataset.id)
                session.rollback()
                kept_roles.update(role.id for role in roles)
                continue
            purged += 1
            for role in roles:
                user_id, authorization = _grant_role_parts(role)
                ended_at = max((ura.expiration_time for ura in role.users if ura.expiration_time), default=None)
                log.info(
                    "GA4GH controlled data purged dataset_id=%s user_id=%s grant=%s ended_at=%s",
                    dataset.id,
                    user_id,
                    authorization,
                    f"{ended_at:%Y-%m-%dT%H:%M:%SZ}" if ended_at else "-",
                )

        grant_roles = select(Role.id).where(Role.type == Role.types.GA4GH_GRANT)
        delete_ended = delete(UserRoleAssociation).where(
            UserRoleAssociation.expiration_time <= cutoff, UserRoleAssociation.role_id.in_(grant_roles)
        )
        if kept_roles:
            delete_ended = delete_ended.where(UserRoleAssociation.role_id.not_in(kept_roles))
        session.execute(delete_ended, execution_options={"synchronize_session": False})
        session.commit()
        return purged

    def _purge_dataset(self, hda_manager: "HDAManager", dataset: Dataset) -> None:
        by_owner: dict[User | None, list[HistoryDatasetAssociation]] = {}
        for hda in dataset.history_associations:
            by_owner.setdefault(hda.user, []).append(hda)
        for owner, hdas in by_owner.items():
            # Also charges each owner's disk usage back.
            hda_manager.mark_purged(owner, hdas)
        owners = [owner for owner in by_owner if owner is not None]
        dataset.full_delete(user=owners[0] if owners else None)
        self._sa_session.add(dataset)
        self._sa_session.commit()

    def _grant_memberships(self, user: User) -> list[UserRoleAssociation]:
        stmt = (
            select(UserRoleAssociation)
            .join(Role)
            .where(UserRoleAssociation.user_id == user.id, Role.type == Role.types.GA4GH_GRANT)
        )
        return list(self._sa_session.scalars(stmt))

    def _get_or_create_grant_role(self, user: User, authorization: str) -> Role:
        name = _grant_role_name(user, authorization)
        role = self._sa_session.scalars(
            select(Role).filter_by(name=name, type=Role.types.GA4GH_GRANT).order_by(Role.id)
        ).first()
        if role is None:
            role = Role(
                name=name,
                description=f"Data access grant {authorization} (GA4GH Passport) of user {user.id}",
                type=Role.types.GA4GH_GRANT,
            )
            self._sa_session.add(role)
        return role

    def forget(self, user: User) -> None:
        """Delete the user's stored Passport. The identity binding stays."""
        if is_vault_configured(self._vault) and self._read(user) is not None:
            UserVaultWrapper(self._vault, user).delete_secret(PASSPORT_VAULT_KEY)
            log.info("GA4GH passport forgotten user_id=%s", user.id)

    def _read(self, user: User) -> dict[str, Any] | None:
        if self._broker is None or not is_vault_configured(self._vault):
            return None
        raw = UserVaultWrapper(self._vault, user).read_secret(PASSPORT_VAULT_KEY)
        if not raw:  # never written, or deleted (the vault deletes by writing "")
            return None
        record = json.loads(raw)
        return record if isinstance(record, dict) else None

    def _require_broker(self) -> PassportBrokerConfig:
        if self._broker is None:
            raise exceptions.ConfigDoesNotAllowException("GA4GH Passport connections are not enabled.")
        return self._broker

    def _get_verifier(self) -> PassportVerifier:
        assert self._verifier is not None
        return self._verifier


def _utc(timestamp: int) -> datetime:
    """Epoch seconds as the naive UTC datetime Galaxy stores."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def _grant_role_name(user: User, authorization: str) -> str:
    return f"{GRANT_ROLE_PREFIX}:{user.id}:{authorization}"


def _grant_role_parts(role: Role) -> tuple[str, str]:
    """(user id, authorization) from a grant role's name."""
    _, user_id, authorization = role.name.split(":", 2)
    return user_id, authorization


def _grant_authorization(role: Role, user: User) -> str:
    return role.name.removeprefix(f"{GRANT_ROLE_PREFIX}:{user.id}:")
