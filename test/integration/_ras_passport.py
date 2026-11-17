"""Shared setup for the RAS passport integration tests.

A per-class RSA key stands in for NIH's: the gate's JWKS fetcher serves its public half,
and passports are signed with it, so no test needs the network.
"""

import json
import os
import time
from collections.abc import Callable
from typing import (
    Any,
    ClassVar,
)

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.authnz.ras.gate import PassportGate
from galaxy.authnz.ras.store import VaultPassportStore
from galaxy.model.db.user import get_user_by_email
from galaxy_test.driver.driver_util import GalaxyTestDriver
from galaxy_test.driver.integration_util import ConfiguresDatabaseVault

ISSUER = "https://sts.example.nih.gov"
KID = "INTEGRATION-TEST"
SUBJECT = "ras-sub"

Signer = Callable[[dict[str, Any]], str]


def make_passport(
    sign: Signer, phs_id: str, subject: str = SUBJECT, iat: int | None = None, exp: int | None = None
) -> str:
    """A passport whose one visa grants ``phs_id`` (version v1, consent group c1)."""
    now = int(time.time())
    common = {
        "iss": ISSUER,
        "sub": subject,
        "iat": now - 60 if iat is None else iat,
        "exp": now + 3600 if exp is None else exp,
    }
    visa = sign(
        dict(
            common,
            ga4gh_visa_v1={"type": "https://ras.nih.gov/visas/v1.1", "source": "https://ncbi.nlm.nih.gov/gap"},
            ras_dbgap_permissions=[{"phs_id": phs_id, "version": "v1", "consent_group": "c1"}],
        )
    )
    return sign(dict(common, ga4gh_passport_v1=[visa]))


class ConfiguresRasPassport(ConfiguresDatabaseVault):
    """Mix into an IntegrationTestCase; call ``_write_ras_config`` from ``handle_galaxy_config_kwds``."""

    _test_driver: GalaxyTestDriver
    key: ClassVar[rsa.RSAPrivateKey]

    @classmethod
    def _write_ras_config(cls, config: dict[str, Any], body: str = "") -> str:
        """Write a ras_config.yml for the test issuer plus ``body``; returns its (cleaned up) directory."""
        cls._configure_database_vault(config)
        config_dir = cls._test_driver.mkdtemp()
        ras_config_file = os.path.join(config_dir, "ras_config.yml")
        with open(ras_config_file, "w") as f:
            f.write(f"issuer: {ISSUER}\njwks_uri: {ISSUER}/jwks.json\n{body}")
        config["ras_config_file"] = ras_config_file
        return config_dir

    @classmethod
    def _install_test_jwks(cls) -> None:
        """Call from ``_configure_app``. The direct RAS source shares the store's JwksClient."""
        # One key for the whole class: the app's JwksClient caches keys by kid, so a
        # fresh key per test under the same kid would be checked against the first one.
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        jwk.update({"kid": KID, "use": "sig"})
        store = cls._ras_gate().store
        assert isinstance(store, VaultPassportStore)
        store.issuer_config.jwks._fetcher = lambda url: {"keys": [jwk]}

    @classmethod
    def _ras_gate(cls) -> PassportGate:
        app = cls._test_driver.app
        assert app is not None
        gate = app.ras_passport_gate
        assert gate is not None
        return gate

    @classmethod
    def _sign(cls, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, cls.key, algorithm="RS256", headers={"kid": KID})

    def _passport(self, phs_id: str, iat: int | None = None, exp: int | None = None) -> str:
        return make_passport(self._sign, phs_id, iat=iat, exp=exp)

    def _store_passport(self, email: str, passport: str) -> None:
        gate = self._ras_gate()
        app = self._test_driver.app
        assert app is not None
        gate.store.put(get_user_by_email(app.model.session, email), passport)
