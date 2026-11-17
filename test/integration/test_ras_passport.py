"""The GA4GH Passport status endpoint against a Galaxy with ras_config_file set and no broker.

No network: the gate's JWKS fetcher is swapped for one serving a local test key.
"""

import time

from galaxy.authnz.ras.store import PASSPORT_VAULT_KEY
from galaxy.model.db.user import get_user_by_email
from galaxy.security.vault import UserVaultWrapper
from galaxy_test.driver import integration_util
from ._ras_passport import (
    ConfiguresRasPassport,
    SUBJECT,
)

STATUS = "users/current/ga4gh_passport"


class TestRasPassportIntegration(integration_util.IntegrationTestCase, ConfiguresRasPassport):
    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        cls._write_ras_config(config)

    @classmethod
    def _configure_app(cls):
        cls._install_test_jwks()

    def test_missing_passport(self):
        with self._different_user("ras-missing@test.gx"):
            response = self._get(STATUS)
            self._assert_status_code_is(response, 200)
        status = response.json()
        assert status["passport"] is None
        assert status["passport_status"] == "missing"
        assert status["identity"] is None

    def test_stored_passport(self):
        email = "ras-present@test.gx"
        with self._different_user(email):
            self._store_passport(email, self._passport("phs000123"))
            response = self._get(STATUS)
            self._assert_status_code_is(response, 200)
        status = response.json()
        assert status["passport_status"] == "ok"
        passport = status["passport"]
        assert passport["sub"] == SUBJECT
        assert passport["expires_at"] and passport["usable_until"]
        assert [visa["recognized"] for visa in passport["visas"]] == [True]

    def test_expired_passport_is_evicted(self):
        email = "ras-expired@test.gx"
        now = int(time.time())
        with self._different_user(email):
            user = get_user_by_email(self._app.model.session, email)
            user_vault = UserVaultWrapper(self._app.vault, user)
            user_vault.write_secret(PASSPORT_VAULT_KEY, self._passport("phs000123", iat=now - 7200, exp=now - 3600))
            response = self._get(STATUS)
            self._assert_status_code_is(response, 200)
            assert not user_vault.read_secret(PASSPORT_VAULT_KEY)
            # Reading it dropped it, so the next read finds nothing.
            again = self._get(STATUS).json()
        status = response.json()
        assert status["passport"] is None
        assert status["passport_status"] == "expired"
        assert again["passport_status"] == "missing"

    def test_anonymous(self):
        with self._different_user(anon=True):
            response = self._get(STATUS)
            self._assert_status_code_is(response, 403)

    def test_login_is_501_without_direct_ras(self):
        with self._different_user("ras-no-direct@test.gx"):
            self._assert_status_code_is(self._post(f"{STATUS}/ras_login"), 501)
            response = self._post(f"{STATUS}/ras_login/callback", data={"callback_url": "https://x/cb"}, json=True)
            self._assert_status_code_is(response, 501)
