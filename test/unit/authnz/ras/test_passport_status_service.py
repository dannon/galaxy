from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace

import pytest

from galaxy.authnz.ras.config import RasConfig
from galaxy.authnz.ras.gate import PassportGate
from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.store import (
    InMemoryVault,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import IssuerConfig
from galaxy.exceptions import (
    AuthenticationRequired,
    ServerNotConfiguredForRequest,
)
from galaxy.schema.ras_passport import RasPassportStatus
from galaxy.webapps.galaxy.services.ras_passport import RasPassportService
from .util import (
    ISSUER,
    issuer_config,
    make_passport,
    NOW,
    permission,
)

USER = SimpleNamespace(id=5)
BRIDGE = "https://bridge.example.org/ras"


def _app(gate):
    return SimpleNamespace(ras_passport_gate=gate)


def _service():
    store = VaultPassportStore(InMemoryVault(), issuer_config(), clock=lambda: NOW)
    galaxy_url = "https://galaxy.example.org"
    gate = PassportGate(
        RasConfig(issuer=ISSUER, bridge_url=BRIDGE, galaxy_url=galaxy_url),
        store,
        galaxy_url=galaxy_url,
        clock=lambda: NOW,
    )
    return store, RasPassportService(_app(gate))


def _trans(user=USER):
    return SimpleNamespace(user=user, anonymous=user is None)


def test_disabled_when_not_configured():
    service = RasPassportService(_app(None))
    with pytest.raises(ServerNotConfiguredForRequest):
        service.summary(_trans())


def test_anonymous():
    _, service = _service()
    with pytest.raises(AuthenticationRequired):
        service.summary(_trans(None))


def test_missing_passport_gives_redirect():
    _, service = _service()
    summary = service.summary(_trans())
    assert not summary.present
    assert summary.status == RasPassportStatus.MISSING
    assert summary.expires_at is None
    assert summary.phs_ids == []
    assert summary.redirect_url and summary.redirect_url.startswith(f"{BRIDGE}?return_to=")


def test_valid_passport_summary():
    store, service = _service()
    passport = store.put(USER, make_passport([permission("phs000123"), permission("phs42")]))
    summary = service.summary(_trans())
    assert summary.present
    assert summary.status == RasPassportStatus.OK
    assert summary.expires_at == datetime.fromtimestamp(passport.expires_at, tz=timezone.utc)
    assert summary.phs_ids == ["phs000042", "phs000123"]
    assert summary.redirect_url is None


def test_unavailable_keys_summary():
    def down(url):
        raise ValueError("RAS is down")

    store, service = _service()
    store.put(USER, make_passport([]))
    store.issuer_config = IssuerConfig(issuer=ISSUER, jwks=JwksClient("https://jwks.example", fetcher=down))
    summary = service.summary(_trans())
    assert not summary.present
    assert summary.status == RasPassportStatus.UNAVAILABLE
    assert summary.redirect_url is None
