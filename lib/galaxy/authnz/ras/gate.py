"""The check protected-data code paths call before touching dbGaP data.

``PassportGate.require`` answers "does this user have a passport (for this
study)?" with either the verified passport or a ``PassportNeeded`` that carries
where to send the user to get one: the configured bridge application, with a
validated Galaxy return address attached.
"""

import logging
import threading
import time
from collections.abc import (
    Callable,
    Iterable,
)
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    TYPE_CHECKING,
)
from urllib.parse import (
    urlencode,
    urlsplit,
    urlunsplit,
)

from galaxy.authnz.ras.config import (
    load_ras_config,
    RasConfig,
    RasConfigError,
)
from galaxy.authnz.ras.jwks import (
    JsonFetcher,
    JwksClient,
    JwksUnavailableError,
    requests_json_fetcher,
)
from galaxy.authnz.ras.models import Passport
from galaxy.authnz.ras.policy import (
    grants_access,
    InvalidAccessionError,
)
from galaxy.authnz.ras.store import (
    PassportStatus,
    PassportStore,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import IssuerConfig
from galaxy.security.vault import is_vault_configured
from galaxy.util import is_safe_login_redirect

if TYPE_CHECKING:
    from galaxy.structured_app import MinimalManagerApp

log = logging.getLogger(__name__)


class PassportNeededReason(str, Enum):
    ANONYMOUS = "anonymous"
    MISSING = "missing"
    EXPIRED = "expired"
    INVALID = "invalid"
    # The issuer's keys couldn't be fetched, so the stored passport can't be checked right now.
    UNAVAILABLE = "unavailable"
    # The passport is fine but doesn't cover the requested study.
    NO_ACCESS = "no_access"


@dataclass(frozen=True)
class PassportOk:
    passport: Passport


@dataclass(frozen=True)
class PassportNeeded:
    reason: PassportNeededReason
    # None when no bridge is configured (e.g. a dev setup using direct RAS only).
    redirect_url: str | None
    message: str


PassportDecision = PassportOk | PassportNeeded

_STATUS_REASONS = {
    PassportStatus.MISSING: PassportNeededReason.MISSING,
    PassportStatus.EXPIRED: PassportNeededReason.EXPIRED,
    PassportStatus.INVALID: PassportNeededReason.INVALID,
    PassportStatus.UNAVAILABLE: PassportNeededReason.UNAVAILABLE,
}
_MESSAGES = {
    PassportNeededReason.ANONYMOUS: "You must be logged in to access controlled-access data.",
    PassportNeededReason.MISSING: "A NIH RAS passport is required to access this data.",
    PassportNeededReason.EXPIRED: "Your NIH RAS passport has expired; please log in to RAS again.",
    PassportNeededReason.INVALID: "Your NIH RAS passport could not be verified; please log in to RAS again.",
    PassportNeededReason.UNAVAILABLE: "Your NIH RAS passport can't be checked right now; please try again shortly.",
    PassportNeededReason.NO_ACCESS: "Your NIH RAS passport does not grant access to {phs_id}.",
}


class PassportGate:
    def __init__(
        self,
        config: RasConfig,
        store: PassportStore,
        galaxy_url: str,
        allowed_return_hosts: Iterable[str] = (),
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.store = store
        self.galaxy_url = galaxy_url.rstrip("/") + "/"
        self.allowed_return_hosts = list(allowed_return_hosts)
        self.clock = clock

    def now(self) -> float:
        return self.clock()

    def require(
        self,
        user: Any,
        phs_id: str | None = None,
        version: str | int | None = None,
        consent_group: str | int | None = None,
        return_to: str | None = None,
    ) -> PassportDecision:
        """The user's verified passport, or what's missing and where to go to fix it.

        With ``phs_id`` the passport must also grant that study (optionally a
        specific version / consent group). ``return_to`` is where the bridge should
        send the user afterwards; it must be on this Galaxy (or an allowed login
        redirect host) and otherwise falls back to the configured ``return_path``.
        """
        if user is None:
            return self._needed(PassportNeededReason.ANONYMOUS, return_to)
        lookup = self.store.load(user)
        if lookup.passport is None:
            return self._needed(_STATUS_REASONS.get(lookup.status, PassportNeededReason.MISSING), return_to)
        if phs_id is not None:
            try:
                granted = grants_access(
                    lookup.passport, phs_id, version=version, consent_group=consent_group, now=self.now()
                )
            except InvalidAccessionError:
                granted = False
            if not granted:
                return self._needed(PassportNeededReason.NO_ACCESS, return_to, phs_id=phs_id)
        return PassportOk(lookup.passport)

    def require_for_trans(self, trans: Any, phs_id: str | None = None, **kwds: Any) -> PassportDecision:
        return self.require(trans.user, phs_id=phs_id, **kwds)

    def _needed(self, reason: PassportNeededReason, return_to: str | None, phs_id: str | None = None) -> PassportNeeded:
        # Sending the user back through RAS can't help while its keys are unreachable.
        redirect_url = None if reason == PassportNeededReason.UNAVAILABLE else self.bridge_redirect_url(return_to)
        return PassportNeeded(
            reason=reason,
            redirect_url=redirect_url,
            message=_MESSAGES[reason].format(phs_id=phs_id),
        )

    def return_address(self, return_to: str | None = None) -> str:
        """An absolute URL on this Galaxy for the bridge to send the user back to."""
        if return_to and is_safe_login_redirect(return_to, self.allowed_return_hosts, base_url=self.galaxy_url):
            target = return_to
        else:
            if return_to:
                log.warning("Ignoring RAS return address that is not on this Galaxy: %s", return_to)
            target = self.config.return_path
        # Relative targets go below Galaxy's URL, keeping any prefix. Concatenate rather
        # than urljoin, which reads "/https://evil.example" as absolute once the slash is gone.
        return self.galaxy_url.rstrip("/") + target if target.startswith("/") else target

    def bridge_redirect_url(self, return_to: str | None = None) -> str | None:
        if not self.config.bridge_url:
            return None
        bridge = urlsplit(self.config.bridge_url)
        query = urlencode({self.config.return_param: self.return_address(return_to)})
        return urlunsplit(bridge._replace(query=f"{bridge.query}&{query}" if bridge.query else query))


class _DiscoveredJwksUri:
    """Resolves the issuer's ``jwks_uri`` from its discovery document on first use."""

    def __init__(self, issuer: str, fetcher: JsonFetcher):
        self.issuer = issuer
        self.fetcher = fetcher
        self._uri: str | None = None
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._uri is None:
                try:
                    doc = self.fetcher(f"{self.issuer}/.well-known/openid-configuration")
                except Exception as e:
                    raise JwksUnavailableError(f"Could not fetch RAS discovery document: {e}") from e
                if (
                    not isinstance(doc, dict)
                    or str(doc.get("issuer") or "").rstrip("/") != self.issuer
                    or not doc.get("jwks_uri")
                ):
                    raise JwksUnavailableError("RAS discovery document does not match the configured issuer")
                self._uri = str(doc["jwks_uri"])
            return self._uri


def issuer_config_from(config: RasConfig, fetcher: JsonFetcher = requests_json_fetcher) -> IssuerConfig:
    jwks_uri = config.jwks_uri or _DiscoveredJwksUri(config.issuer, fetcher)
    return IssuerConfig(issuer=config.issuer, jwks=JwksClient(jwks_uri, fetcher=fetcher), leeway=config.leeway)


def build_passport_gate(app: "MinimalManagerApp") -> PassportGate | None:
    """The app's gate, or ``None`` when ``ras_config_file`` doesn't exist.

    Nothing here touches the network; keys are fetched on the first verification.
    """
    config = load_ras_config(getattr(app.config, "ras_config_file", None))
    if config is None:
        return None
    if not is_vault_configured(app.vault):
        raise RasConfigError("RAS passport support with store: vault needs vault_config_file to be configured")
    store: PassportStore = VaultPassportStore(app.vault, issuer_config_from(config))
    # Without a bridge the return address is never used.
    galaxy_url = config.galaxy_url or app.config.galaxy_infrastructure_url
    log.info("RAS passport support enabled for issuer %s", config.issuer)
    return PassportGate(
        config,
        store,
        galaxy_url=galaxy_url,
        allowed_return_hosts=getattr(app.config, "allowed_login_redirect_hosts", None) or (),
    )
