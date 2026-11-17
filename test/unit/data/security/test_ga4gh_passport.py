import time
from typing import Any
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from galaxy.security.ga4gh_passport import (
    InvalidPassport,
    OidcDiscoveryKeys,
    PassportVerifier,
    RAS_DBGAP_VISA_TYPE,
)

RAS = "https://ras.example.org"
OTHER = "https://other-broker.example.org"
SUB = "user-sub-1"
NOW = int(time.time())


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


RAS_KEY, OTHER_KEY, ROGUE_KEY = new_key(), new_key(), new_key()


class StaticKeys:
    """Stands in for OIDC discovery: one key per issuer; records every lookup."""

    def __init__(self, keys: dict[str, Any]):
        self.keys = keys
        self.lookups: list[str] = []

    def signing_key(self, issuer: str, token: str) -> Any:
        self.lookups.append(issuer)
        return self.keys[issuer].public_key()


def sign(claims: dict[str, Any], key=RAS_KEY, headers=None) -> str:
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


def grant(phs_id="phs000001", consent_group="c1", **overrides) -> dict[str, Any]:
    return {
        "consent_name": "General Research Use",
        "phs_id": phs_id,
        "version": "v1",
        "participant_set": "p1",
        "consent_group": consent_group,
        "role": "pi",
        "expiration": NOW + 180 * 86400,
        **overrides,
    }


def visa(grants=None, iss=RAS, key=RAS_KEY, exp=NOW + 3600, type_=RAS_DBGAP_VISA_TYPE, headers=None, **extra) -> str:
    claims = {
        "iss": iss,
        "sub": SUB,
        "iat": NOW,
        "exp": exp,
        "scope": "openid ga4gh_passport_v1",
        "txn": "txn-1",
        "ga4gh_visa_v1": {
            "type": type_,
            "asserted": NOW,
            "value": f"{iss}/passport/dbgap/v1.1",
            "source": "https://ncbi.nlm.nih.gov/gap",
            "by": "dac",
        },
        **extra,
    }
    if type_ == RAS_DBGAP_VISA_TYPE and grants is not None:
        claims["ras_dbgap_permissions"] = grants
    return sign(claims, key, headers)


def passport(visas: list[str], iss=RAS, key=RAS_KEY, exp=NOW + 3600, drop=(), **extra) -> str:
    claims = {"iss": iss, "sub": SUB, "iat": NOW, "exp": exp, "txn": "txn-1", "ga4gh_passport_v1": visas, **extra}
    for name in drop:
        claims.pop(name)
    return sign(claims, key)


@pytest.fixture
def keys():
    return StaticKeys({RAS: RAS_KEY, OTHER: OTHER_KEY})


@pytest.fixture
def verifier(keys):
    return PassportVerifier([RAS], keys=keys)


# --- valid passports -----------------------------------------------------------------


def test_single_dbgap_visa(verifier):
    result = verifier.verify(passport([visa([grant()])]))
    assert (result.issuer, result.sub, result.txn) == (RAS, SUB, "txn-1")
    assert result.rejected_visas == ()
    (only,) = result.grants
    assert only.authorization == "phs000001.c1"
    assert only.expiration == NOW + 180 * 86400
    assert result.visas_expire_at == NOW + 3600


def test_multi_visa_passport(verifier):
    # Shape of mock user "dave": two dbGaP visas plus a non-dbGaP one.
    token = passport(
        [
            visa([grant()], exp=NOW + 7200),
            visa([grant("phs000002"), grant("phs000003")], exp=NOW + 3600),
            visa(type_="AcceptedTermsAndPolicies"),
        ]
    )
    result = verifier.verify(token)
    assert len(result.visas) == 3
    assert [g.authorization for g in result.grants] == ["phs000001.c1", "phs000002.c1", "phs000003.c1"]
    assert [v.recognized for v in result.visas] == [True, True, False]
    assert result.visas_expire_at == NOW + 3600  # minimum across visas


def test_empty_passport_is_valid(verifier):
    result = verifier.verify(passport([]))
    assert result.visas == () and result.grants == () and result.visas_expire_at is None


def test_unknown_visa_type_is_kept_without_grants(verifier):
    result = verifier.verify(passport([visa(type_="https://example.org/custom/v1")]))
    (only,) = result.visas
    assert not only.recognized and only.grants == ()


def test_passport_audience_is_not_enforced(verifier):
    assert verifier.verify(passport([], aud="some-drs-server")).sub == SUB


def test_visa_jku_is_never_followed(verifier, keys):
    token = passport([visa([grant()], headers={"jku": "https://attacker.example.org/jwks"})])
    assert len(verifier.verify(token).grants) == 1
    assert keys.lookups == [RAS, RAS]  # keys came from the trusted issuer, for passport and visa


# --- rejected passports ----------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(passport([], key=ROGUE_KEY), id="wrong-signing-key"),
        pytest.param(passport([], exp=NOW - 3600), id="expired"),
        pytest.param(passport([], drop=["sub"]), id="missing-sub"),
        pytest.param(passport([], drop=["ga4gh_passport_v1"]), id="missing-visa-list"),
        pytest.param(passport([], ga4gh_passport_v1="not-a-list"), id="visa-list-not-a-list"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_invalid_passport(verifier, token):
    with pytest.raises(InvalidPassport):
        verifier.verify(token)


def test_tampered_passport(verifier):
    header, payload, signature = passport([]).split(".")
    with pytest.raises(InvalidPassport):
        verifier.verify(f"{header}.{payload}.{signature[:-4]}AAAA")


def test_unsigned_passport(verifier):
    token = jwt.encode({"iss": RAS, "sub": SUB, "iat": NOW, "exp": NOW + 60, "ga4gh_passport_v1": []}, "", "none")
    with pytest.raises(InvalidPassport):
        verifier.verify(token)


def test_untrusted_issuer_is_rejected_before_any_key_lookup(verifier, keys):
    with pytest.raises(InvalidPassport, match="untrusted issuer"):
        verifier.verify(passport([], iss=OTHER, key=OTHER_KEY))
    assert keys.lookups == []


# --- dropped visas -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_visa",
    [
        pytest.param(visa([grant()], key=ROGUE_KEY), id="wrong-signing-key"),
        pytest.param(visa([grant()], exp=NOW - 3600), id="expired"),
        pytest.param(visa([grant()], iss=OTHER, key=OTHER_KEY), id="untrusted-issuer"),
        pytest.param(visa(None), id="dbgap-type-without-permissions"),
        pytest.param(visa([{"phs_id": "phs000009"}]), id="malformed-permission"),
        pytest.param(visa([grant(expiration="soon")]), id="non-integer-expiration"),
        pytest.param(visa(ga4gh_visa_v1={"type": RAS_DBGAP_VISA_TYPE}), id="incomplete-visa-claim"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_bad_visa_is_dropped_and_the_rest_kept(verifier, bad_visa):
    result = verifier.verify(passport([bad_visa, visa([grant("phs000002")])]))
    assert [g.authorization for g in result.grants] == ["phs000002.c1"]
    assert len(result.rejected_visas) == 1 and result.rejected_visas[0].startswith("visa 0:")


def test_untrusted_visa_issuer_never_looked_up(keys):
    verifier = PassportVerifier([RAS], keys=keys)
    verifier.verify(passport([visa([grant()], iss=OTHER, key=OTHER_KEY)]))
    assert OTHER not in keys.lookups


def test_rejected_visa_reason_never_contains_the_token(verifier):
    bad = visa([grant()], key=ROGUE_KEY)
    result = verifier.verify(passport([bad]))
    assert bad not in result.rejected_visas[0]


def test_token_not_in_repr(verifier):
    token = passport([visa([grant()])])
    assert token not in repr(verifier.verify(token))


# --- OIDC discovery key source ---------------------------------------------------------


def test_discovery_is_fetched_once_per_issuer():
    response = mock.Mock(**{"json.return_value": {"jwks_uri": f"{RAS}/jwks.json"}})
    with (
        mock.patch("galaxy.security.ga4gh_passport.requests.get", return_value=response) as get,
        mock.patch("galaxy.security.ga4gh_passport.jwt.PyJWKClient") as client_class,
    ):
        source = OidcDiscoveryKeys(timeout=5)
        source.signing_key(RAS, "token-1")
        source.signing_key(RAS, "token-2")
    get.assert_called_once_with(f"{RAS}/.well-known/openid-configuration", timeout=5)
    client_class.assert_called_once_with(f"{RAS}/jwks.json", cache_keys=True, timeout=5)
    assert client_class.return_value.get_signing_key_from_jwt.call_count == 2
