"""The check protected-data code paths call before touching dbGaP data.

``PassportGate.require`` answers "does this user have a passport (for this
study)?" with either the verified passport or a ``PassportNeeded`` that says
what is missing. Where to send the user to get a passport is the caller's
business: on a Galaxy with a passport broker it is the connect flow.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    TYPE_CHECKING,
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
from galaxy.authnz.ras.login import DirectRasLogin
from galaxy.authnz.ras.models import Passport
from galaxy.authnz.ras.policy import (
    grants_access,
    InvalidAccessionError,
)
from galaxy.authnz.ras.protected import match_protected_source
from galaxy.authnz.ras.sources import DirectRasPassportSource
from galaxy.authnz.ras.store import (
    PassportStatus,
    PassportStore,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import IssuerConfig
from galaxy.security.vault import is_vault_configured

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
    PassportNeededReason.MISSING: "An NIH RAS passport is required to access this data.",
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
        clock: Callable[[], float] = time.time,
        direct_login: DirectRasLogin | None = None,
    ):
        self.config = config
        self.store = store
        self.clock = clock
        self.direct_login = direct_login

    def now(self) -> float:
        return self.clock()

    def require(
        self,
        user: Any,
        phs_id: str | None = None,
        version: str | int | None = None,
        consent_group: str | int | None = None,
    ) -> PassportDecision:
        """The user's verified passport, or what's missing.

        With ``phs_id`` the passport must also grant that study (optionally a
        specific version / consent group).
        """
        if user is None:
            return self._needed(PassportNeededReason.ANONYMOUS)
        lookup = self.store.load(user)
        if lookup.passport is None:
            return self._needed(_STATUS_REASONS.get(lookup.status, PassportNeededReason.MISSING))
        if phs_id is not None:
            try:
                granted = grants_access(
                    lookup.passport, phs_id, version=version, consent_group=consent_group, now=self.now()
                )
            except InvalidAccessionError:
                granted = False
            if not granted:
                return self._needed(PassportNeededReason.NO_ACCESS, phs_id=phs_id)
        return PassportOk(lookup.passport)

    def require_for_url(self, user: Any, url: str) -> PassportDecision | None:
        """``None`` if ``url`` isn't a protected source, otherwise what ``require`` says for its study."""
        match = match_protected_source(self.config.protected_sources, url)
        if match is None:
            return None
        decision = self.require(user, phs_id=match.accession)
        if match.study_not_captured and isinstance(decision, PassportOk):
            return self._needed(PassportNeededReason.NO_ACCESS)
        return decision

    def require_for_trans(self, trans: Any, phs_id: str | None = None, **kwds: Any) -> PassportDecision:
        return self.require(trans.user, phs_id=phs_id, **kwds)

    def _needed(self, reason: PassportNeededReason, phs_id: str | None = None) -> PassportNeeded:
        return PassportNeeded(reason=reason, message=_MESSAGES[reason].format(phs_id=phs_id or "the requested study"))


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
        raise RasConfigError("RAS passport support needs vault_config_file to be configured")
    issuer_config = issuer_config_from(config)
    store = VaultPassportStore(app.vault, issuer_config)
    direct_login = None
    if config.direct_ras:
        settings = config.direct_ras
        source = DirectRasPassportSource(
            config.issuer,
            settings.client_id,
            settings.resolve_client_secret(),
            settings.redirect_uri,
            scopes=settings.scopes,
            jwks=issuer_config.jwks,
            leeway=config.leeway,
        )
        direct_login = DirectRasLogin(source, app.vault)
    log.info("RAS passport support enabled for issuer %s", config.issuer)
    return PassportGate(config, store, direct_login=direct_login)
