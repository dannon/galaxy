from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace
from typing import (
    Any,
    cast,
)

import requests

from galaxy.authnz.ras.config import RasConfig
from galaxy.authnz.ras.gate import PassportGate
from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.store import (
    InMemoryVault,
    PASSPORT_VAULT_KEY,
    PassportLookup,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import IssuerConfig
from galaxy.files import (
    DictFileSourcesUserContext,
    ProvidesFileSourcesTransaction,
    ProvidesFileSourcesUserContext,
)
from ..authnz.ras.util import (
    ISSUER,
    issuer_config,
    make_passport,
    NOW,
    PASSPORT_LIFETIME,
    permission,
)

USER = SimpleNamespace(id=3)


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class CountingStore(VaultPassportStore):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.loads = 0

    def load(self, user: Any) -> PassportLookup:
        self.loads += 1
        return super().load(user)


def _gate(clock: Clock | None = None, config: IssuerConfig | None = None) -> tuple[InMemoryVault, CountingStore, Any]:
    vault = InMemoryVault()
    store = CountingStore(vault, config or issuer_config(), clock=clock or Clock(NOW))
    gate = PassportGate(RasConfig(issuer=ISSUER), store, galaxy_url="https://galaxy.example.org/")
    return vault, store, gate


def _context(app: Any, user: Any = USER) -> ProvidesFileSourcesUserContext:
    trans = SimpleNamespace(app=app, user=user)
    return ProvidesFileSourcesUserContext(cast(ProvidesFileSourcesTransaction, trans))


def test_returns_raw_passport_and_expiry():
    vault, store, gate = _gate()
    raw = make_passport([permission("phs000123")])
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", raw)
    context = _context(SimpleNamespace(ras_passport_gate=gate))
    assert context.ras_passport == raw
    assert context.ras_passport_expires_at == datetime.fromtimestamp(NOW - 60 + PASSPORT_LIFETIME, tz=timezone.utc)
    # Reading the properties again doesn't verify the passport again.
    assert context.ras_passport == raw
    assert context.ras_passport_expires_at is not None
    assert store.loads == 1


def test_none_when_feature_is_off():
    for app in (SimpleNamespace(ras_passport_gate=None), SimpleNamespace()):
        context = _context(app)
        assert context.ras_passport is None
        assert context.ras_passport_expires_at is None


def test_none_when_anonymous():
    _, store, gate = _gate()
    context = _context(SimpleNamespace(ras_passport_gate=gate), user=None)
    assert context.ras_passport is None
    assert context.ras_passport_expires_at is None
    assert store.loads == 0


def test_none_when_expired():
    vault, _, gate = _gate(clock=Clock(NOW + PASSPORT_LIFETIME + 3600))
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", make_passport([]))
    context = _context(SimpleNamespace(ras_passport_gate=gate))
    assert context.ras_passport is None
    assert context.ras_passport_expires_at is None


def test_none_when_keys_unavailable():
    def unreachable(url: str) -> dict[str, Any]:
        raise requests.ConnectionError("RAS is down")

    config = IssuerConfig(issuer=ISSUER, jwks=JwksClient("https://jwks.example", fetcher=unreachable))
    vault, _, gate = _gate(config=config)
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", make_passport([]))
    context = _context(SimpleNamespace(ras_passport_gate=gate))
    assert context.ras_passport is None
    assert context.ras_passport_expires_at is None


def test_dict_context_round_trip():
    expires_at = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    context = DictFileSourcesUserContext(
        username="alice", ras_passport="aaa.bbb.ccc", ras_passport_expires_at=expires_at
    )
    assert context.ras_passport == "aaa.bbb.ccc"
    assert context.ras_passport_expires_at == expires_at
    # Dictifying the context is how it reaches job_io.json; the passport must stay out of it.
    as_dict = context.to_dict()
    assert "ras_passport" not in as_dict
    assert "ras_passport_expires_at" not in as_dict
    assert "aaa.bbb.ccc" not in str(as_dict)

    empty = DictFileSourcesUserContext(username="alice")
    assert empty.ras_passport is None
    assert empty.ras_passport_expires_at is None
