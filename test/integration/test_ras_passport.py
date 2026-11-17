"""The RAS passport status endpoint against a Galaxy with ras_config_file set.

No network: the gate's JWKS fetcher is swapped for one serving a local test key.
"""

import time
from urllib.parse import (
    parse_qs,
    urlsplit,
)

from galaxy.authnz.ras.store import PASSPORT_VAULT_KEY
from galaxy.model.db.user import get_user_by_email
from galaxy.security.vault import UserVaultWrapper
from galaxy_test.driver import integration_util
from ._ras_passport import ConfiguresRasPassport

BRIDGE = "https://bridge.example.org/ras/login"


class TestRasPassportIntegration(integration_util.IntegrationTestCase, ConfiguresRasPassport):
    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        cls._write_ras_config(config, f"bridge_url: {BRIDGE}\ngalaxy_url: https://galaxy.example.org/\n")

    @classmethod
    def _configure_app(cls):
        cls._install_test_jwks()

    def test_missing_passport(self):
        with self._different_user("ras-missing@test.gx"):
            response = self._get("users/current/ras_passport")
            self._assert_status_code_is(response, 200)
        summary = response.json()
        assert summary["present"] is False
        assert summary["status"] == "missing"
        redirect = urlsplit(summary["redirect_url"])
        assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == BRIDGE
        assert parse_qs(redirect.query)["return_to"] == ["https://galaxy.example.org/"]

    def test_stored_passport(self):
        email = "ras-present@test.gx"
        with self._different_user(email):
            self._store_passport(email, self._passport("phs000123"))
            response = self._get("users/current/ras_passport")
            self._assert_status_code_is(response, 200)
        summary = response.json()
        assert summary["present"] is True
        assert summary["status"] == "ok"
        assert summary["phs_ids"] == ["phs000123"]
        assert summary["redirect_url"] is None
        assert summary["expires_at"]

    def test_expired_passport_is_evicted(self):
        email = "ras-expired@test.gx"
        now = int(time.time())
        with self._different_user(email):
            user = get_user_by_email(self._app.model.session, email)
            user_vault = UserVaultWrapper(self._app.vault, user)
            user_vault.write_secret(PASSPORT_VAULT_KEY, self._passport("phs000123", iat=now - 7200, exp=now - 3600))
            response = self._get("users/current/ras_passport")
            self._assert_status_code_is(response, 200)
            assert not user_vault.read_secret(PASSPORT_VAULT_KEY)
        summary = response.json()
        assert summary["present"] is False
        assert summary["status"] == "expired"
        assert summary["redirect_url"]

    def test_anonymous(self):
        with self._different_user(anon=True):
            response = self._get("users/current/ras_passport")
            self._assert_status_code_is(response, 403)

    def test_login_is_501_without_direct_ras(self):
        with self._different_user("ras-no-direct@test.gx"):
            self._assert_status_code_is(self._post("users/current/ras_passport/login"), 501)
