from types import SimpleNamespace

import pytest
import requests

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.store import (
    InMemoryVault,
    PASSPORT_VAULT_KEY,
    PassportStatus,
    TapisVaultPassportStore,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import (
    IssuerConfig,
    PassportVerificationError,
    TokenExpiredError,
)
from galaxy.security.vault import (
    VaultKeyPrefixWrapper,
    VaultKeyValidationWrapper,
)
from .util import (
    ISSUER,
    issuer_config,
    jwks_doc,
    KEY,
    KID,
    make_passport,
    NOW,
    OTHER_KEY,
    passport_claims,
    permission,
    sign,
)

USER = SimpleNamespace(id=7)
OTHER_USER = SimpleNamespace(id=8)


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _store(clock=None):
    vault = InMemoryVault()
    return vault, VaultPassportStore(vault, issuer_config(), clock=clock or Clock(NOW))


def test_round_trip():
    vault, store = _store()
    raw = make_passport([permission("phs000123")])
    stored = store.put(USER, raw)
    assert vault.secrets == {f"user/7/{PASSPORT_VAULT_KEY}": raw}
    lookup = store.load(USER)
    assert lookup.status == PassportStatus.OK
    assert lookup.passport == stored
    assert store.get(USER).visas[0].dbgap_permissions[0].phs_id == "phs000123"


def test_missing_and_per_user():
    _, store = _store()
    store.put(USER, make_passport([]))
    assert store.load(OTHER_USER).status == PassportStatus.MISSING
    assert store.get(OTHER_USER) is None


def test_anonymous_user():
    _, store = _store()
    assert store.load(None).status == PassportStatus.MISSING
    with pytest.raises(ValueError):
        store.put(None, make_passport([]))
    store.clear(None)


def test_put_refuses_invalid_passport():
    vault, store = _store()
    with pytest.raises(PassportVerificationError):
        store.put(USER, sign(passport_claims([]), key=OTHER_KEY))
    with pytest.raises(TokenExpiredError):
        store.put(USER, make_passport([], iat=NOW - 7200, exp=NOW - 3600))
    assert vault.secrets == {}


def test_expired_passport_is_evicted():
    clock = Clock(NOW)
    vault, store = _store(clock)
    raw = make_passport([], exp=NOW + 600)
    store.put(USER, raw)
    clock.now = NOW + 3600
    assert store.load(USER).status == PassportStatus.EXPIRED
    assert not vault.read_secret(f"user/7/{PASSPORT_VAULT_KEY}")
    assert store.load(USER).status == PassportStatus.MISSING


def test_tampered_value_rejected_and_cleared():
    vault, store = _store()
    store.put(USER, make_passport([]))
    vault.write_secret(f"user/7/{PASSPORT_VAULT_KEY}", sign(passport_claims([]), key=OTHER_KEY))
    assert store.load(USER).status == PassportStatus.INVALID
    assert store.load(USER).status == PassportStatus.MISSING


class FlakyFetcher:
    def __init__(self):
        self.down = False

    def __call__(self, url):
        if self.down:
            raise requests.ConnectionError("RAS is down")
        return jwks_doc((KEY, KID))


def test_key_fetch_outage_keeps_the_passport():
    fetcher = FlakyFetcher()
    fetcher.down = True
    keys_clock = Clock(0)
    config = IssuerConfig(issuer=ISSUER, jwks=JwksClient("https://jwks.example", fetcher=fetcher, clock=keys_clock))
    vault = InMemoryVault()
    store = VaultPassportStore(vault, config, clock=Clock(NOW))
    # Stored before a restart; the fresh process has no cached keys and RAS is down.
    raw = make_passport([])
    vault.write_secret(f"user/7/{PASSPORT_VAULT_KEY}", raw)
    lookup = store.load(USER)
    assert lookup.status == PassportStatus.UNAVAILABLE
    assert lookup.passport is None
    assert vault.read_secret(f"user/7/{PASSPORT_VAULT_KEY}") == raw
    fetcher.down = False
    keys_clock.now = 120
    assert store.load(USER).status == PassportStatus.OK


class RacingVault(InMemoryVault):
    """Simulates another request storing a fresh passport right after the first read."""

    def __init__(self):
        super().__init__()
        self.fresh: str | None = None

    def read_secret(self, key):
        value = super().read_secret(key)
        if self.fresh is not None:
            self.write_secret(key, self.fresh)
            self.fresh = None
        return value


def test_eviction_does_not_delete_a_passport_stored_meanwhile():
    clock = Clock(NOW)
    vault = RacingVault()
    store = VaultPassportStore(vault, issuer_config(), clock=clock)
    store.put(USER, make_passport([], exp=NOW + 600))
    clock.now = NOW + 3600
    fresh = make_passport([], iat=NOW + 3000)
    vault.fresh = fresh
    assert store.load(USER).status == PassportStatus.EXPIRED
    assert vault.secrets[f"user/7/{PASSPORT_VAULT_KEY}"] == fresh
    assert store.load(USER).status == PassportStatus.OK


def test_clear():
    _, store = _store()
    store.put(USER, make_passport([]))
    store.clear(USER)
    assert store.get(USER) is None


def test_tapis_store_is_a_stub():
    store = TapisVaultPassportStore()
    with pytest.raises(NotImplementedError):
        store.load(USER)
    with pytest.raises(NotImplementedError):
        store.put(USER, "x")


def test_works_through_galaxy_vault_wrappers():
    backing = InMemoryVault()
    vault = VaultKeyValidationWrapper(VaultKeyPrefixWrapper(backing, prefix="/galaxy"))
    store = VaultPassportStore(vault, issuer_config(), clock=Clock(NOW))
    store.put(USER, make_passport([]))
    assert list(backing.secrets) == [f"galaxy/user/7/{PASSPORT_VAULT_KEY}"]
    assert store.load(USER).status == PassportStatus.OK
    store.clear(USER)
    assert store.load(USER).status == PassportStatus.MISSING
