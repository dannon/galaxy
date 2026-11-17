"""Fetching a protected URL through /api/tools/fetch needs a RAS passport.

No network: the gate refuses before any fetch job exists.
"""

import json
import os
import tempfile
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.authnz.ras.store import VaultPassportStore
from galaxy.model.db.user import get_user_by_email
from galaxy_test.base.populators import DatasetPopulator
from galaxy_test.driver import integration_util

ISSUER = "https://sts.example.nih.gov"
BRIDGE = "https://bridge.example.org/ras/login"
KID = "INTEGRATION-TEST"
PROTECTED_URL = "drs://protected.example.org/obj1"
RAS_PASSPORT_REQUIRED = 403009


class TestRasPassportFetchGateIntegration(
    integration_util.IntegrationTestCase, integration_util.ConfiguresDatabaseVault
):
    key: rsa.RSAPrivateKey
    populator: DatasetPopulator

    def setUp(self):
        super().setUp()
        self.populator = DatasetPopulator(self.galaxy_interactor)

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
                "protected_sources:\n"
                "  - url_regex: '^drs://protected\\.example\\.org/'\n"
                "    phs_id: phs000123\n"
            )
        config["ras_config_file"] = ras_config_file

    @classmethod
    def _configure_app(cls):
        # One key per class: the app's JwksClient caches keys by kid.
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        jwk.update({"kid": KID, "use": "sig"})
        app = cls._test_driver.app
        assert app is not None
        gate = app.ras_passport_gate
        assert gate is not None
        assert isinstance(gate.store, VaultPassportStore)
        gate.store.issuer_config.jwks._fetcher = lambda url: {"keys": [jwk]}

    def _sign(self, claims):
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": KID})

    def _passport(self, phs_id):
        now = int(time.time())
        common = {"iss": ISSUER, "sub": "ras-sub", "iat": now - 60, "exp": now + 3600}
        visa = self._sign(
            dict(
                common,
                ga4gh_visa_v1={"type": "https://ras.nih.gov/visas/v1.1", "source": "https://ncbi.nlm.nih.gov/gap"},
                ras_dbgap_permissions=[{"phs_id": phs_id, "version": "v1", "consent_group": "c1"}],
            )
        )
        return self._sign(dict(common, ga4gh_passport_v1=[visa]))

    def _fetch(self, history_id, target_overrides):
        target = {"destination": {"type": "hdas"}}
        target.update(target_overrides)
        return self.populator.fetch({"history_id": history_id, "targets": [target]}, assert_ok=False, wait=False)

    def _assert_refused(self, response, reason, history_id):
        self._assert_status_code_is(response, 403)
        body = response.json()
        assert body["err_code"] == RAS_PASSPORT_REQUIRED
        assert body["ras_passport_reason"] == reason
        assert not self.populator.get_history_contents(history_id)

    def test_fetch_of_protected_url_without_passport_is_403(self):
        with self._different_user("ras-fetch-missing@test.gx"):
            history_id = self.populator.new_history()
            response = self._fetch(history_id, {"elements": [{"src": "url", "url": PROTECTED_URL}]})
            self._assert_refused(response, "missing", history_id)

    def test_fetch_of_protected_url_in_nested_collection_is_403(self):
        with self._different_user("ras-fetch-nested@test.gx"):
            history_id = self.populator.new_history()
            elements = [
                {
                    "name": "outer",
                    "elements": [{"name": "inner", "elements": [{"name": "x", "src": "url", "url": PROTECTED_URL}]}],
                }
            ]
            response = self._fetch(
                history_id, {"destination": {"type": "hdca"}, "collection_type": "list:list:list", "elements": elements}
            )
            self._assert_refused(response, "missing", history_id)

    def test_fetch_with_passport_for_another_study_is_no_access(self):
        email = "ras-fetch-other-study@test.gx"
        with self._different_user(email):
            user = get_user_by_email(self._app.model.session, email)
            gate = self._app.ras_passport_gate
            assert gate is not None
            gate.store.put(user, self._passport("phs000999"))
            history_id = self.populator.new_history()
            response = self._fetch(history_id, {"elements": [{"src": "url", "url": PROTECTED_URL}]})
            self._assert_refused(response, "no_access", history_id)
