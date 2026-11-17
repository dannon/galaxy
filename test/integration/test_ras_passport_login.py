"""The direct RAS login endpoints against a Galaxy with a direct_ras block.

No network: the JWKS fetcher and the source's HTTP client are swapped for local fakes.
"""

import json
import os
import tempfile
import time
from typing import Any
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.authnz.ras.login import LOGIN_REQUEST_VAULT_KEY
from galaxy.model.db.user import get_user_by_email
from galaxy.security.vault import UserVaultWrapper
from galaxy_test.driver import integration_util

ISSUER = "https://sts.example.nih.gov"
KID = "INTEGRATION-TEST"
CLIENT_ID = "integration-client"
REDIRECT_URI = "https://callback.example.org/cb"
SUBJECT = "ras-sub"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/auth/oauth/v2/authorize",
    "token_endpoint": f"{ISSUER}/auth/oauth/v2/token",
    "jwks_uri": f"{ISSUER}/jwks.json",
}


class FakeRas:
    """Serves discovery, the token endpoint and v1.1 userinfo with tokens signed by the test key."""

    def __init__(self, sign):
        self.sign = sign
        self.nonce = ""

    def _passport(self) -> str:
        now = int(time.time())
        common = {"iss": ISSUER, "sub": SUBJECT, "iat": now - 60, "exp": now + 3600}
        visa = self.sign(
            dict(
                common,
                ga4gh_visa_v1={"type": "https://ras.nih.gov/visas/v1.1", "source": "https://ncbi.nlm.nih.gov/gap"},
                ras_dbgap_permissions=[{"phs_id": "phs000123", "version": "v1", "consent_group": "c1"}],
            )
        )
        return self.sign(dict(common, ga4gh_passport_v1=[visa]))

    def get_json(self, url: str, bearer: str | None = None) -> tuple[int, Any]:
        if url.endswith("/.well-known/openid-configuration"):
            return 200, DISCOVERY
        if url.endswith("/openid/connect/v1.1/userinfo"):
            return 200, {"sub": SUBJECT, "passport_jwt_v11": self._passport()}
        return 404, {}

    def post_form(self, url: str, data: Any, basic_auth: tuple[str, str]) -> tuple[int, Any]:
        now = int(time.time())
        id_token = self.sign(
            {
                "iss": ISSUER,
                "sub": SUBJECT,
                "aud": CLIENT_ID,
                "azp": CLIENT_ID,
                "iat": now - 10,
                "exp": now + 3600,
                "nonce": self.nonce,
            }
        )
        return 200, {"access_token": "access-1", "id_token": id_token}


class TestRasPassportLoginIntegration(integration_util.IntegrationTestCase, integration_util.ConfiguresDatabaseVault):
    key: rsa.RSAPrivateKey
    ras: FakeRas

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
                "direct_ras:\n"
                f"  client_id: {CLIENT_ID}\n"
                "  client_secret: integration-secret\n"
                f"  redirect_uri: {REDIRECT_URI}\n"
            )
        config["ras_config_file"] = ras_config_file

    @classmethod
    def _configure_app(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        jwk.update({"kid": KID, "use": "sig"})
        app = cls._test_driver.app
        assert app is not None
        gate = app.ras_passport_gate
        assert gate is not None and gate.direct_login is not None
        gate.direct_login.source.jwks._fetcher = lambda url: {"keys": [jwk]}
        cls.ras = FakeRas(lambda claims: jwt.encode(claims, cls.key, algorithm="RS256", headers={"kid": KID}))
        gate.direct_login.source.http = cls.ras

    def _start(self, email: str) -> str:
        """Start a login as the current user and return the state from the authorization URL."""
        response = self._post("users/current/ras_passport/login")
        self._assert_status_code_is(response, 200)
        body = response.json()
        assert body["expires_in"] == 600
        assert body["authorization_url"].startswith(DISCOVERY["authorization_endpoint"])
        user = get_user_by_email(self._app.model.session, email)
        saved = json.loads(UserVaultWrapper(self._app.vault, user).read_secret(LOGIN_REQUEST_VAULT_KEY))
        self.ras.nonce = saved["nonce"]
        return parse_qs(urlsplit(body["authorization_url"]).query)["state"][0]

    def _callback(self, state: str):
        return self._post(
            "users/current/ras_passport/login/callback",
            data={"callback_url": f"{REDIRECT_URI}?code=abc&state={state}"},
            json=True,
        )

    def test_login_round_trip(self):
        email = "ras-login@test.gx"
        with self._different_user(email):
            state = self._start(email)
            response = self._callback(state)
            self._assert_status_code_is(response, 200)
            assert response.json()["present"] is True
            summary = self._get("users/current/ras_passport").json()
            assert summary["present"] is True
            assert summary["phs_ids"] == ["phs000123"]

    def test_callback_with_wrong_state_is_400_and_stores_nothing(self):
        email = "ras-login-bad-state@test.gx"
        with self._different_user(email):
            self._start(email)
            response = self._callback("not-the-state")
            self._assert_status_code_is(response, 400)
            summary = self._get("users/current/ras_passport").json()
            assert summary["present"] is False
            assert summary["status"] == "missing"

    def test_delete_clears_passport(self):
        email = "ras-login-delete@test.gx"
        with self._different_user(email):
            self._assert_status_code_is(self._callback(self._start(email)), 200)
            self._assert_status_code_is(self._delete("users/current/ras_passport"), 204)
            summary = self._get("users/current/ras_passport").json()
            assert summary["present"] is False
            assert summary["status"] == "missing"

    def test_login_requires_a_user(self):
        with self._different_user(anon=True):
            self._assert_status_code_is(self._post("users/current/ras_passport/login"), 403)
