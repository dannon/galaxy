"""The RAS passport status endpoint against a Galaxy with ras_config_file set.

No network: the gate's JWKS fetcher is swapped for one serving a local test key.
"""

import json
import os
import tempfile
import time
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.authnz.ras.store import (
    PASSPORT_VAULT_KEY,
    VaultPassportStore,
)
from galaxy.model.db.user import get_user_by_email
from galaxy.security.vault import UserVaultWrapper
from galaxy_test.driver import integration_util

ISSUER = "https://sts.example.nih.gov"
BRIDGE = "https://bridge.example.org/ras/login"
KID = "INTEGRATION-TEST"


class TestRasPassportIntegration(integration_util.IntegrationTestCase, integration_util.ConfiguresDatabaseVault):
    key: rsa.RSAPrivateKey

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        cls._configure_database_vault(config)
        config_dir = tempfile.mkdtemp()
        ras_config_file = os.path.join(config_dir, "ras_config.yml")
        with open(ras_config_file, "w") as f:
            f.write(
                f"issuer: {ISSUER}\n"
                f"jwks_uri: {ISSUER}/jwks.json\n"
                f"bridge_url: {BRIDGE}\n"
                "galaxy_url: https://galaxy.example.org/\n"
            )
        config["ras_config_file"] = ras_config_file

    @classmethod
    def _configure_app(cls):
        # One key for the whole class: the app's JwksClient caches keys by kid, so a
        # fresh key per test under the same kid would be checked against the first one.
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        jwk.update({"kid": KID, "use": "sig"})
        app = cls._test_driver.app
        assert app is not None
        gate = app.ras_passport_gate
        assert gate is not None
        assert isinstance(gate.store, VaultPassportStore)
        gate.store.issuer_config.jwks._fetcher = lambda url: {"keys": [jwk]}

    def _store(self) -> VaultPassportStore:
        gate = self._app.ras_passport_gate
        assert gate is not None
        assert isinstance(gate.store, VaultPassportStore)
        return gate.store

    def _sign(self, claims):
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": KID})

    def _passport(self, phs_id, iat=None, exp=None):
        now = int(time.time())
        iat = now - 60 if iat is None else iat
        common = {"iss": ISSUER, "sub": "ras-sub", "iat": iat, "exp": now + 3600 if exp is None else exp}
        visa = self._sign(
            dict(
                common,
                ga4gh_visa_v1={"type": "https://ras.nih.gov/visas/v1.1", "source": "https://ncbi.nlm.nih.gov/gap"},
                ras_dbgap_permissions=[{"phs_id": phs_id, "version": "v1", "consent_group": "c1"}],
            )
        )
        return self._sign(dict(common, ga4gh_passport_v1=[visa]))

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
            user = get_user_by_email(self._app.model.session, email)
            self._store().put(user, self._passport("phs000123"))
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
