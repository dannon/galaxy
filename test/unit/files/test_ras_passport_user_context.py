import os
from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace
from typing import (
    Any,
    cast,
)

import pytest
import requests
import responses

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.store import (
    InMemoryVault,
    PASSPORT_VAULT_KEY,
    PassportLookup,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import IssuerConfig
from galaxy.exceptions import AuthenticationRequired
from galaxy.files import (
    DictFileSourcesUserContext,
    GA4GHPassportCredential,
    ProvidesFileSourcesTransaction,
    ProvidesFileSourcesUserContext,
)
from galaxy.files.sources.drs import CONNECT_HINT
from galaxy.managers.ga4gh_passports import PassportManager
from ._util import (
    assert_realizes_as,
    configured_file_sources,
    serialize_and_recover,
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
MAX_AGE = 3600
RAS_FILE_SOURCES_CONF = os.path.join(os.path.dirname(__file__), "drs_ras_passport_file_sources_conf.yml")
RAS_DRS_URI = "drs://drs.ras-example.org/obj1"
RAS_OBJECT_URL = "https://drs.ras-example.org/ga4gh/drs/v1/objects/obj1"


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


def _app(store: VaultPassportStore, vault: InMemoryVault) -> SimpleNamespace:
    """An app the way Galaxy builds one with ras_config_file and no passport broker."""
    config = SimpleNamespace(
        ga4gh_passport_broker_authorize_url=None,
        ga4gh_passport_max_age=MAX_AGE,
        ga4gh_grant_max_membership=30 * 86400,
        ga4gh_controlled_data_grace_period=30 * 86400,
    )
    manager = PassportManager(config, None, vault, store=store)  # type: ignore[arg-type]
    return SimpleNamespace(ga4gh_passport_manager=manager)


def _gate(clock: Clock | None = None, config: IssuerConfig | None = None) -> tuple[InMemoryVault, CountingStore, Any]:
    vault = InMemoryVault()
    store = CountingStore(vault, config or issuer_config(), clock=clock or Clock(NOW))
    return vault, store, _app(store, vault)


def _context(app: Any, user: Any = USER) -> ProvidesFileSourcesUserContext:
    trans = SimpleNamespace(app=app, user=user, user_is_admin=False, anonymous=user is None)
    return ProvidesFileSourcesUserContext(cast(ProvidesFileSourcesTransaction, trans))


def test_returns_raw_passport_and_freshness_limit():
    vault, _, app = _gate()
    raw = make_passport([permission("phs000123")])
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", raw)
    assert _context(app).ga4gh_passport == GA4GHPassportCredential(
        passport=raw, fresh_until=datetime.fromtimestamp(NOW - 60 + MAX_AGE, tz=timezone.utc), txn="txn-1"
    )


def test_none_when_anonymous():
    _, store, app = _gate()
    assert _context(app, user=None).ga4gh_passport is None
    assert store.loads == 0


def test_none_when_expired():
    vault, _, app = _gate(clock=Clock(NOW + PASSPORT_LIFETIME + 3600))
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", make_passport([]))
    assert _context(app).ga4gh_passport is None


def test_none_when_keys_unavailable():
    def unreachable(url: str) -> dict[str, Any]:
        raise requests.ConnectionError("RAS is down")

    config = IssuerConfig(issuer=ISSUER, jwks=JwksClient("https://jwks.example", fetcher=unreachable))
    vault, _, app = _gate(config=config)
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", make_passport([]))
    assert _context(app).ga4gh_passport is None


def test_dict_context_never_serializes_the_passport():
    credential = GA4GHPassportCredential(
        passport="aaa.bbb.ccc", fresh_until=datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc), txn="txn-1"
    )
    context = DictFileSourcesUserContext(username="alice", ga4gh_passport=credential)
    assert context.ga4gh_passport == credential
    # Dictifying the context is how it reaches job_io.json; the passport must stay out of it.
    as_dict = context.to_dict()
    assert "ga4gh_passport" not in as_dict
    assert "aaa.bbb.ccc" not in str(as_dict)

    assert DictFileSourcesUserContext(username="alice").ga4gh_passport is None


def _expired_passport_context() -> ProvidesFileSourcesUserContext:
    clock = Clock(NOW)
    _, store, app = _gate(clock=clock)
    store.put(USER, make_passport([permission("phs000123")]))
    clock.now = NOW + PASSPORT_LIFETIME + 3600
    return _context(app)


def _never_had_one_context() -> ProvidesFileSourcesUserContext:
    _, _, app = _gate()
    return _context(app)


@pytest.mark.parametrize("make_context", [_expired_passport_context, _never_had_one_context])
@responses.activate
def test_expired_passport_is_no_passport(make_context):
    responses.add(responses.GET, RAS_OBJECT_URL, status=401)
    file_sources = configured_file_sources(RAS_FILE_SOURCES_CONF)
    context = make_context()
    as_dict = file_sources.to_dict(for_serialization=True, user_context=context)
    (source,) = (source for source in as_dict["file_sources"] if source["id"] == "test_ras")
    assert "passports" not in source and "auth_expires_at" not in source

    recovered = serialize_and_recover(file_sources, user_context=context)
    for sources, user_context in [(file_sources, context), (recovered, None)]:
        with pytest.raises(AuthenticationRequired) as excinfo:
            assert_realizes_as(sources, RAS_DRS_URI, "", user_context=user_context)
        assert str(excinfo.value).endswith(CONNECT_HINT)
    assert [(call.request.method, call.request.url) for call in responses.calls] == [("GET", RAS_OBJECT_URL)] * 2


class FlakyStore(CountingStore):
    def load(self, user: Any) -> PassportLookup:
        if self.loads == 0:
            self.loads += 1
            raise RuntimeError("vault hiccup")
        return super().load(user)


def test_failed_load_is_not_remembered():
    vault = InMemoryVault()
    store = FlakyStore(vault, issuer_config(), clock=Clock(NOW))
    raw = make_passport([])
    vault.write_secret(f"user/{USER.id}/{PASSPORT_VAULT_KEY}", raw)
    context = _context(_app(store, vault))
    with pytest.raises(RuntimeError):
        _ = context.ga4gh_passport
    credential = context.ga4gh_passport
    assert credential is not None and credential.passport == raw
