from types import SimpleNamespace
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import pytest
import requests

from galaxy.authnz.ras.config import (
    DirectRasSettings,
    load_ras_config,
    RasConfig,
    RasConfigError,
)
from galaxy.authnz.ras.gate import (
    build_passport_gate,
    issuer_config_from,
    PassportGate,
    PassportNeeded,
    PassportNeededReason,
    PassportOk,
)
from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.store import (
    InMemoryVault,
    PASSPORT_VAULT_KEY,
    PassportStatus,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import (
    IssuerConfig,
    PassportVerificationError,
    verify_passport,
)
from galaxy.security.vault import NullVault
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

USER = SimpleNamespace(id=3)
GALAXY_URL = "https://galaxy.example.org/"
BRIDGE = "https://bridge.example.org/ras/login"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _gate(clock=None, galaxy_url=GALAXY_URL, **config):
    clock = clock or Clock(NOW)
    vault = InMemoryVault()
    store = VaultPassportStore(vault, issuer_config(), clock=clock)
    ras_config = RasConfig(issuer=ISSUER, bridge_url=BRIDGE, galaxy_url=galaxy_url, **config)
    gate = PassportGate(
        ras_config, store, galaxy_url=galaxy_url, allowed_return_hosts=["portal.example.org"], clock=clock
    )
    return vault, store, gate


def _return_to(redirect_url):
    url = urlsplit(redirect_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == BRIDGE
    return parse_qs(url.query)["return_to"][0]


def _write_config(tmp_path, text):
    path = tmp_path / "ras_config.yml"
    path.write_text(text)
    return str(path)


def _app(ras_config_file, vault=None):
    config = SimpleNamespace(
        ras_config_file=ras_config_file,
        galaxy_infrastructure_url="http://localhost:8080",
        allowed_login_redirect_hosts=[],
    )
    return SimpleNamespace(config=config, vault=vault if vault is not None else InMemoryVault())


def test_off_by_default(tmp_path):
    assert load_ras_config(None) is None
    assert load_ras_config(str(tmp_path / "missing.yml")) is None
    assert build_passport_gate(_app(str(tmp_path / "missing.yml"))) is None
    assert build_passport_gate(_app(None)) is None


def test_build_from_config(tmp_path):
    path = _write_config(
        tmp_path, f"issuer: {ISSUER}\nbridge_url: {BRIDGE}\ngalaxy_url: https://galaxy.example.org\nleeway: 30\n"
    )
    gate = build_passport_gate(_app(path))
    assert isinstance(gate, PassportGate)
    assert gate.config.leeway == 30
    assert isinstance(gate.store, VaultPassportStore)
    assert gate.store.issuer_config.leeway == 30
    assert gate.galaxy_url == "https://galaxy.example.org/"


def test_vault_store_needs_a_vault(tmp_path):
    path = _write_config(tmp_path, f"issuer: {ISSUER}\n")
    with pytest.raises(RasConfigError, match="vault_config_file"):
        build_passport_gate(_app(path, vault=NullVault()))


@pytest.mark.parametrize(
    "text,match",
    [
        ("issuer: http://insecure.example\n", "https"),
        (f"bridge_url: http://bridge.example\nissuer: {ISSUER}\n", "https"),
        # galaxy_infrastructure_url is an internal address, no use to a browser coming back from the bridge.
        (f"bridge_url: {BRIDGE}\n", "galaxy_url"),
        ("return_path: //evil.example\n", "return_path"),
        ("return_path: https://evil.example\n", "return_path"),
        ("leeway: 100000\n", "leeway"),
        ("unknown_option: 1\n", "unknown_option"),
        ("store: s3\n", "store"),
        # TapisVaultPassportStore is only a placeholder; accepting it would 500 every request.
        ("store: tapis_vault\n", "store"),
        ("- a list\n", "mapping"),
    ],
)
def test_invalid_config(tmp_path, text, match):
    with pytest.raises(RasConfigError, match=match):
        load_ras_config(_write_config(tmp_path, text))


def test_empty_config_file_uses_defaults(tmp_path):
    config = load_ras_config(_write_config(tmp_path, ""))
    assert config is not None
    assert config.issuer == "https://stsstg.nih.gov"
    assert config.bridge_url is None


def test_direct_ras_secret_from_env(monkeypatch):
    settings = DirectRasSettings(client_id="c", redirect_uri="https://cb.example")
    monkeypatch.delenv("RAS_CLIENT_SECRET", raising=False)
    with pytest.raises(RasConfigError):
        settings.resolve_client_secret()
    monkeypatch.setenv("RAS_CLIENT_SECRET", "from-env")
    assert settings.resolve_client_secret() == "from-env"


def test_needed_when_missing():
    _, _, gate = _gate()
    decision = gate.require(USER)
    assert isinstance(decision, PassportNeeded)
    assert decision.reason == PassportNeededReason.MISSING
    assert decision.redirect_url
    assert _return_to(decision.redirect_url) == GALAXY_URL


def test_needed_when_anonymous():
    _, _, gate = _gate()
    decision = gate.require(None, phs_id="phs000123")
    assert isinstance(decision, PassportNeeded)
    assert decision.reason == PassportNeededReason.ANONYMOUS


def test_needed_when_expired():
    clock = Clock(NOW)
    _, store, gate = _gate(clock)
    store.put(USER, make_passport([permission()], exp=NOW + 60))
    clock.now = NOW + 3600
    decision = gate.require(USER)
    assert isinstance(decision, PassportNeeded)
    assert decision.reason == PassportNeededReason.EXPIRED
    assert "expired" in decision.message


def test_needed_when_stored_passport_invalid():
    vault, _, gate = _gate()
    vault.write_secret(f"user/3/{PASSPORT_VAULT_KEY}", sign(passport_claims([]), key=OTHER_KEY))
    decision = gate.require(USER)
    assert isinstance(decision, PassportNeeded)
    assert decision.reason == PassportNeededReason.INVALID


def test_needed_when_keys_unavailable():
    def down(url):
        raise requests.ConnectionError("RAS is down")

    vault, store, gate = _gate()
    store.put(USER, make_passport([]))
    store.issuer_config = IssuerConfig(issuer=ISSUER, jwks=JwksClient("https://jwks.example", fetcher=down))
    decision = gate.require(USER)
    assert isinstance(decision, PassportNeeded)
    assert decision.reason == PassportNeededReason.UNAVAILABLE
    # Logging in to RAS again won't help while the issuer is unreachable.
    assert decision.redirect_url is None
    assert vault.read_secret(f"user/3/{PASSPORT_VAULT_KEY}")


def test_discovery_outage_is_unavailable():
    def down(url):
        raise requests.ConnectionError("RAS is down")

    vault, store, gate = _gate()
    store.put(USER, make_passport([]))
    store.issuer_config = issuer_config_from(RasConfig(issuer=ISSUER), fetcher=down)
    assert store.load(USER).status == PassportStatus.UNAVAILABLE
    assert vault.read_secret(f"user/3/{PASSPORT_VAULT_KEY}")


def test_ok_when_valid():
    _, store, gate = _gate()
    stored = store.put(USER, make_passport([permission("phs000123")]))
    decision = gate.require(USER)
    assert isinstance(decision, PassportOk)
    assert decision.passport == stored
    assert isinstance(gate.require(USER, phs_id="phs000123"), PassportOk)
    assert isinstance(gate.require(USER, phs_id="phs000123", consent_group="c1"), PassportOk)
    assert isinstance(gate.require_for_trans(SimpleNamespace(user=USER), phs_id="phs000123.v2"), PassportOk)


def test_phs_denial_has_distinct_reason():
    _, store, gate = _gate()
    store.put(USER, make_passport([permission("phs000123", consent_group="c1")]))
    for kwargs in ({"phs_id": "phs000999"}, {"phs_id": "phs000123", "consent_group": "c2"}, {"phs_id": "bogus"}):
        decision = gate.require(USER, **kwargs)
        assert isinstance(decision, PassportNeeded)
        assert decision.reason == PassportNeededReason.NO_ACCESS
        assert kwargs["phs_id"] in decision.message
        assert decision.redirect_url


def test_permission_expiry_is_enforced_by_the_gate():
    clock = Clock(NOW)
    _, store, gate = _gate(clock)
    store.put(USER, make_passport([permission("phs000123", expiration=NOW + 100)]))
    assert isinstance(gate.require(USER, phs_id="phs000123"), PassportOk)
    clock.now = NOW + 200
    decision = gate.require(USER, phs_id="phs000123")
    assert isinstance(decision, PassportNeeded)
    assert decision.reason == PassportNeededReason.NO_ACCESS


@pytest.mark.parametrize(
    "return_to,expected",
    [
        (None, GALAXY_URL),
        ("/datasets/list", "https://galaxy.example.org/datasets/list"),
        ("https://galaxy.example.org/histories/view?id=1", "https://galaxy.example.org/histories/view?id=1"),
        ("https://portal.example.org/after", "https://portal.example.org/after"),
        ("https://evil.example/phish", GALAXY_URL),
        ("//evil.example/phish", GALAXY_URL),
        ("/\\evil.example", GALAXY_URL),
        ("javascript:alert(1)", GALAXY_URL),
        ("http://portal.example.org/after", GALAXY_URL),
        # Paths that look like URLs once the leading slash is dropped stay on Galaxy.
        ("/https://evil.example/phish", "https://galaxy.example.org/https://evil.example/phish"),
        ("/javascript:alert(1)", "https://galaxy.example.org/javascript:alert(1)"),
    ],
)
def test_return_address_validation(return_to, expected):
    _, _, gate = _gate()
    decision = gate.require(USER, return_to=return_to)
    assert isinstance(decision, PassportNeeded)
    assert decision.redirect_url
    assert _return_to(decision.redirect_url) == expected


def test_return_path_and_galaxy_prefix():
    _, _, gate = _gate(galaxy_url="https://example.org/galaxy", return_path="/ras/done")
    assert gate.return_address() == "https://example.org/galaxy/ras/done"
    assert gate.return_address("/datasets/list") == "https://example.org/galaxy/datasets/list"


def test_bridge_url_with_existing_query():
    _, store, _ = _gate()
    gate = PassportGate(
        RasConfig(issuer=ISSUER, bridge_url=f"{BRIDGE}?tenant=cfderas", galaxy_url=GALAXY_URL, return_param="next"),
        store,
        GALAXY_URL,
    )
    redirect = gate.bridge_redirect_url()
    assert redirect is not None
    query = parse_qs(urlsplit(redirect).query)
    assert query == {"tenant": ["cfderas"], "next": [GALAXY_URL]}


def test_no_bridge_configured():
    _, store, _ = _gate()
    gate = PassportGate(RasConfig(issuer=ISSUER), store, GALAXY_URL)
    decision = gate.require(USER)
    assert isinstance(decision, PassportNeeded)
    assert decision.redirect_url is None


def test_jwks_uri_from_discovery():
    jwks = jwks_doc((KEY, KID))
    discovery = {"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks.json"}
    calls = []

    def fetch(url):
        calls.append(url)
        return discovery if url.endswith("openid-configuration") else jwks

    config = issuer_config_from(RasConfig(issuer=ISSUER), fetcher=fetch)
    assert config.jwks.get_signing_key(KID)
    assert calls == [f"{ISSUER}/.well-known/openid-configuration", f"{ISSUER}/jwks.json"]


@pytest.mark.parametrize(
    "discovery",
    [
        {"issuer": "https://other.example", "jwks_uri": "https://other.example/jwks.json"},
        {"issuer": ISSUER},
        {"jwks_uri": f"{ISSUER}/jwks.json"},
        {"issuer": None, "jwks_uri": f"{ISSUER}/jwks.json"},
        ["not", "a", "mapping"],
    ],
)
def test_discovery_must_match_the_issuer(discovery):
    jwks = jwks_doc((KEY, KID))
    calls = []

    def fetch(url):
        calls.append(url)
        return discovery if url.endswith("openid-configuration") else jwks

    config = issuer_config_from(RasConfig(issuer=ISSUER), fetcher=fetch)
    with pytest.raises(PassportVerificationError, match="discovery"):
        verify_passport(make_passport([]), config, now=NOW)
    # The keys were never fetched from wherever the bad document pointed.
    assert calls == [f"{ISSUER}/.well-known/openid-configuration"]
