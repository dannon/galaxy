from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from galaxy.authnz.ras.config import RasConfig
from galaxy.authnz.ras.gate import (
    issuer_config_from,
    PassportGate,
)
from galaxy.authnz.ras.store import (
    InMemoryVault,
    PASSPORT_VAULT_KEY,
    VaultPassportStore,
)
from galaxy.exceptions import RasPassportRequiredException
from galaxy.webapps.galaxy.services._fetch_util import require_passports_for_targets
from galaxy.work.context import SessionRequestContext
from ..authnz.ras.util import (
    ISSUER,
    issuer_config,
    make_passport,
    NOW,
    permission,
)

USER = SimpleNamespace(id=3)
GALAXY_URL = "https://galaxy.example.org/galaxy"
CONNECT_URL = f"{GALAXY_URL}/ga4gh_passport/connect"
URL = "drs://protected.example.org/obj1"
SOURCES = [{"url_regex": r"^drs://protected\.example\.org/", "phs_id": "phs000123"}]


def _gate():
    vault = InMemoryVault()
    store = VaultPassportStore(vault, issuer_config(), clock=lambda: NOW)
    config = RasConfig(issuer=ISSUER, protected_sources=SOURCES)
    return vault, store, PassportGate(config, store, clock=lambda: NOW)


def _trans(gate, user=USER, broker=True):
    trans = mock.Mock(spec=SessionRequestContext)
    trans.app = SimpleNamespace(ras_passport_gate=gate, ga4gh_passport_manager=SimpleNamespace(enabled=broker))
    trans.user = user
    trans.request = SimpleNamespace(url_path=GALAXY_URL)
    return trans


def _refusal(trans, targets=None):
    with pytest.raises(RasPassportRequiredException) as excinfo:
        require_passports_for_targets(trans, targets or _targets())
    return excinfo.value.extra_error_info


def _targets(url=URL):
    return [{"destination": {"type": "hdas"}, "elements": [{"src": "url", "url": url}]}]


def test_noop_when_feature_is_off():
    require_passports_for_targets(_trans(None), _targets())


def test_unprotected_and_non_url_items_pass():
    _, _, gate = _gate()
    targets = _targets("drs://public.example.org/obj") + [{"elements": [{"src": "path", "path": URL}]}]
    require_passports_for_targets(_trans(gate), targets)


def test_anonymous_user_gets_anonymous_reason():
    _, _, gate = _gate()
    info = _refusal(_trans(gate, user=None))
    assert info["ras_passport_reason"] == "anonymous"
    # The connect endpoint sends an anonymous user to log in first.
    assert info["ras_passport_redirect_url"] == CONNECT_URL


def test_nested_elements_are_checked():
    _, _, gate = _gate()
    targets = [
        {
            "destination": {"type": "hdca"},
            "elements": [{"name": "outer", "elements": [{"name": "inner", "src": "url", "url": URL}]}],
        }
    ]
    with pytest.raises(RasPassportRequiredException) as excinfo:
        require_passports_for_targets(_trans(gate), targets)
    assert excinfo.value.extra_error_info["ras_passport_reason"] == "missing"


def test_unavailable_keys_report_unavailable():
    def down(url):
        raise requests.ConnectionError("RAS is down")

    vault, store, gate = _gate()
    store.put(USER, make_passport([permission("phs000123")]))
    store.issuer_config = issuer_config_from(RasConfig(issuer=ISSUER), fetcher=down)
    info = _refusal(_trans(gate))
    assert info["ras_passport_reason"] == "unavailable"
    assert info["ras_passport_redirect_url"] is None
    assert vault.read_secret(f"user/3/{PASSPORT_VAULT_KEY}")


def test_first_refusal_carries_reason_and_redirect():
    _, _, gate = _gate()
    with pytest.raises(RasPassportRequiredException) as excinfo:
        require_passports_for_targets(_trans(gate), _targets())
    exc = excinfo.value
    assert exc.status_code == 403
    assert exc.err_code.code == 403009
    assert exc.extra_error_info["ras_passport_reason"] == "missing"
    assert exc.extra_error_info["ras_passport_redirect_url"] == CONNECT_URL


def test_no_access_points_at_the_connect_flow():
    _, store, gate = _gate()
    store.put(USER, make_passport([permission("phs000999")]))
    info = _refusal(_trans(gate))
    assert info["ras_passport_reason"] == "no_access"
    assert info["ras_passport_redirect_url"] == CONNECT_URL


def test_no_redirect_without_a_broker():
    _, _, gate = _gate()
    info = _refusal(_trans(gate, broker=False))
    assert info["ras_passport_reason"] == "missing"
    assert info["ras_passport_redirect_url"] is None


def test_passport_for_the_study_lets_the_fetch_through():
    _, store, gate = _gate()
    store.put(USER, make_passport([permission("phs000123")]))
    require_passports_for_targets(_trans(gate), _targets())
