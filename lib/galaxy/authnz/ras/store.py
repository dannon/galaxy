"""Where a user's current RAS passport is kept between requests.

The passport is just-in-time: it's used until it expires and then the user goes
through the bridge again, so stores hold the raw signed JWT and re-verify it on
every read rather than trusting anything parsed earlier.
"""

import abc
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from galaxy.authnz.ras.models import Passport
from galaxy.authnz.ras.verify import (
    IssuerConfig,
    PassportVerificationError,
    TokenExpiredError,
    VerificationUnavailableError,
    verify_passport,
)
from galaxy.security.vault import (
    UserVaultWrapper,
    Vault,
)

log = logging.getLogger(__name__)

PASSPORT_VAULT_KEY = "ras/passport"


class PassportStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    EXPIRED = "expired"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PassportLookup:
    status: PassportStatus
    passport: Passport | None = None


class PassportStore(abc.ABC):
    @abc.abstractmethod
    def load(self, user: Any) -> PassportLookup:
        """The user's verified passport, or why there isn't one.

        Drops expired or invalid passports, but keeps one that only couldn't be
        checked because the issuer's keys were unreachable.
        """

    @abc.abstractmethod
    def put(self, user: Any, raw_jwt: str) -> Passport:
        """Verify ``raw_jwt`` and store it for ``user``. Raises ``PassportVerificationError``."""

    @abc.abstractmethod
    def clear(self, user: Any) -> None:
        """Forget the user's passport."""

    def get(self, user: Any) -> Passport | None:
        return self.load(user).passport


class VaultPassportStore(PassportStore):
    """Keeps the raw passport JWT in Galaxy's vault under ``user/<id>/ras/passport``."""

    def __init__(self, vault: Vault, issuer_config: IssuerConfig, clock: Callable[[], float] = time.time):
        self.vault = vault
        self.issuer_config = issuer_config
        self.clock = clock

    def _user_vault(self, user: Any) -> UserVaultWrapper:
        return UserVaultWrapper(self.vault, user)

    def load(self, user: Any) -> PassportLookup:
        if user is None:
            return PassportLookup(PassportStatus.MISSING)
        raw = self._user_vault(user).read_secret(PASSPORT_VAULT_KEY)
        if not raw:
            return PassportLookup(PassportStatus.MISSING)
        try:
            passport = verify_passport(raw, self.issuer_config, now=self.clock())
        except VerificationUnavailableError as e:
            log.warning("Could not check the stored RAS passport for user %s: %s", user.id, e)
            return PassportLookup(PassportStatus.UNAVAILABLE)
        except TokenExpiredError:
            self._drop(user, raw)
            return PassportLookup(PassportStatus.EXPIRED)
        except PassportVerificationError as e:
            log.warning("Dropping stored RAS passport for user %s that no longer verifies: %s", user.id, e)
            self._drop(user, raw)
            return PassportLookup(PassportStatus.INVALID)
        return PassportLookup(PassportStatus.OK, passport)

    def _drop(self, user: Any, raw: str) -> None:
        # The user may have just come back from the bridge with a fresh passport
        # while this request was verifying the old one; don't delete that.
        if self._user_vault(user).read_secret(PASSPORT_VAULT_KEY) == raw:
            self.clear(user)

    def put(self, user: Any, raw_jwt: str) -> Passport:
        if user is None:
            raise ValueError("Cannot store a passport for an anonymous user")
        passport = verify_passport(raw_jwt, self.issuer_config, now=self.clock())
        self._user_vault(user).write_secret(PASSPORT_VAULT_KEY, raw_jwt)
        return passport

    def clear(self, user: Any) -> None:
        if user is not None:
            self._user_vault(user).delete_secret(PASSPORT_VAULT_KEY)


class TapisVaultPassportStore(PassportStore):
    """Placeholder for keeping the passport in the RAS-side Tapis tenant instead of Galaxy.

    At the 10/20 CWIC meeting the bridge application was described as running in
    the separate ``cfderas.tapis.io`` tenant, so the passport would be saved there,
    and "if it is stored in a vault, the passport/token does not need to be
    persisted within galaxy". Whether Tapis secrets can carry an expiry was still
    being checked. Until the bridge contract is settled this has no
    implementation; a real one would read the raw passport JWT from the Tapis
    secret for the user and run it through ``verify_passport`` exactly like
    :class:`VaultPassportStore`, so Galaxy still checks NIH's signature itself.
    """

    def load(self, user: Any) -> PassportLookup:
        raise NotImplementedError("The Tapis vault passport store is not implemented yet")

    def put(self, user: Any, raw_jwt: str) -> Passport:
        raise NotImplementedError("The Tapis vault passport store is not implemented yet")

    def clear(self, user: Any) -> None:
        raise NotImplementedError("The Tapis vault passport store is not implemented yet")


class InMemoryVault(Vault):
    """A process-local vault for dev scripts and tests. Nothing survives a restart."""

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def read_secret(self, key: str) -> str | None:
        return self.secrets.get(key)

    def write_secret(self, key: str, value: str) -> None:
        self.secrets[key] = value

    def list_secrets(self, key: str) -> list[str]:
        prefix = key.rstrip("/") + "/"
        return sorted({prefix + k[len(prefix) :].split("/")[0] for k in self.secrets if k.startswith(prefix)})
