"""The direct RAS login endpoints against a Galaxy with a direct_ras block.

No network: the JWKS fetcher and the source's HTTP client are swapped for local fakes.
The test database is shared across tests and a RAS identity binds to one Galaxy user,
so each test logs in as its own RAS subject.
"""

import json
import time
from typing import Any
from urllib.parse import (
    parse_qs,
    urlsplit,
)

from galaxy.authnz.ras.login import LOGIN_REQUEST_VAULT_KEY
from galaxy.model.db.user import get_user_by_email
from galaxy.security.vault import UserVaultWrapper
from galaxy_test.driver import integration_util
from ._ras_passport import (
    ConfiguresRasPassport,
    ISSUER,
    make_passport,
    Signer,
)

STATUS = "users/current/ga4gh_passport"
CLIENT_ID = "integration-client"
REDIRECT_URI = "https://callback.example.org/cb"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/auth/oauth/v2/authorize",
    "token_endpoint": f"{ISSUER}/auth/oauth/v2/token",
    "jwks_uri": f"{ISSUER}/jwks.json",
}


class FakeRas:
    """Serves discovery, the token endpoint and v1.1 userinfo with tokens signed by the test key."""

    def __init__(self, sign: Signer):
        self.sign = sign
        self.nonce = ""
        self.subject = ""

    def get_json(self, url: str, bearer: str | None = None) -> tuple[int, Any]:
        if url.endswith("/.well-known/openid-configuration"):
            return 200, DISCOVERY
        if url.endswith("/openid/connect/v1.1/userinfo"):
            passport = make_passport(self.sign, "phs000123", subject=self.subject)
            return 200, {"sub": self.subject, "passport_jwt_v11": passport}
        return 404, {}

    def post_form(self, url: str, data: Any, basic_auth: tuple[str, str]) -> tuple[int, Any]:
        now = int(time.time())
        id_token = self.sign(
            {
                "iss": ISSUER,
                "sub": self.subject,
                "aud": CLIENT_ID,
                "azp": CLIENT_ID,
                "iat": now - 10,
                "exp": now + 3600,
                "nonce": self.nonce,
            }
        )
        return 200, {"access_token": "access-1", "id_token": id_token}


class TestRasPassportLoginIntegration(integration_util.IntegrationTestCase, ConfiguresRasPassport):
    ras: FakeRas

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        cls._write_ras_config(
            config,
            "direct_ras:\n"
            f"  client_id: {CLIENT_ID}\n"
            "  client_secret: integration-secret\n"
            f"  redirect_uri: {REDIRECT_URI}\n",
        )

    @classmethod
    def _configure_app(cls):
        cls._install_test_jwks()
        login = cls._ras_gate().direct_login
        assert login is not None
        cls.ras = FakeRas(cls._sign)
        login.source.http = cls.ras

    def _start(self, email: str, subject: str) -> str:
        """Start a login as the current user, to come back as RAS ``subject``; return the state."""
        response = self._post(f"{STATUS}/ras_login")
        self._assert_status_code_is(response, 200)
        body = response.json()
        assert body["expires_in"] == 600
        assert body["authorization_url"].startswith(DISCOVERY["authorization_endpoint"])
        user = get_user_by_email(self._app.model.session, email)
        raw_saved = UserVaultWrapper(self._app.vault, user).read_secret(LOGIN_REQUEST_VAULT_KEY)
        assert raw_saved is not None
        saved = json.loads(raw_saved)
        self.ras.nonce = saved["nonce"]
        self.ras.subject = subject
        return parse_qs(urlsplit(body["authorization_url"]).query)["state"][0]

    def _callback(self, state: str):
        return self._post(
            f"{STATUS}/ras_login/callback",
            data={"callback_url": f"{REDIRECT_URI}?code=abc&state={state}"},
            json=True,
        )

    def test_login_round_trip_binds_the_identity(self):
        email = "ras-login@test.gx"
        with self._different_user(email):
            response = self._callback(self._start(email, "sub-round-trip"))
            self._assert_status_code_is(response, 200)
            assert response.json()["passport_status"] == "ok"
            status = self._get(STATUS).json()
        assert status["passport_status"] == "ok"
        assert status["passport"]["sub"] == "sub-round-trip"
        assert status["identity"]["issuer"] == ISSUER
        assert status["identity"]["sub"] == "sub-round-trip"
        assert [access["authorization"] for access in status["data_access"]] == ["phs000123.c1"]

    def test_second_user_with_the_same_ras_identity_is_refused(self):
        with self._different_user("ras-login-first@test.gx"):
            self._assert_status_code_is(self._callback(self._start("ras-login-first@test.gx", "sub-shared")), 200)
        second = "ras-login-second@test.gx"
        with self._different_user(second):
            response = self._callback(self._start(second, "sub-shared"))
            self._assert_status_code_is(response, 400)
            status = self._get(STATUS).json()
        assert response.json()["ga4gh_passport_error"] == "identity_in_use"
        assert "another Galaxy account" in response.json()["err_msg"]
        assert status["identity"] is None
        assert status["passport"] is None
        assert status["passport_status"] == "missing"
        assert status["data_access"] == []

    def test_callback_with_wrong_state_is_400_and_stores_nothing(self):
        email = "ras-login-bad-state@test.gx"
        with self._different_user(email):
            self._start(email, "sub-bad-state")
            response = self._callback("not-the-state")
            self._assert_status_code_is(response, 400)
            status = self._get(STATUS).json()
        assert status["passport_status"] == "missing"
        assert status["identity"] is None

    def test_delete_clears_passport_and_keeps_the_identity(self):
        email = "ras-login-delete@test.gx"
        with self._different_user(email):
            self._assert_status_code_is(self._callback(self._start(email, "sub-delete")), 200)
            self._assert_status_code_is(self._delete(STATUS), 204)
            status = self._get(STATUS).json()
        assert status["passport_status"] == "missing"
        assert status["identity"]["sub"] == "sub-delete"

    def test_login_requires_a_user(self):
        with self._different_user(anon=True):
            self._assert_status_code_is(self._post(f"{STATUS}/ras_login"), 403)
