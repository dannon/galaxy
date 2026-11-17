import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization

from galaxy.authnz.ras.jwks import (
    JwksClient,
    JwksError,
    JwksUnavailableError,
)
from galaxy.authnz.ras.verify import (
    IssuerConfig,
    PassportVerificationError,
    TokenExpiredError,
    VerificationUnavailableError,
    verify_passport,
)
from .util import (
    CountingFetcher,
    FIXTURES_ENV,
    ISSUER,
    issuer_config,
    jwks_doc,
    KEY,
    KID,
    make_passport,
    NOW,
    OTHER_KEY,
    OTHER_KID,
    passport_claims,
    permission,
    public_jwk,
    sign,
    SUBJECT,
    visa_claims,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_valid_passport_parses_visas_and_permissions():
    raw = make_passport([permission("phs000123"), {"phs_id": "phs000456", "future_field": 7}])
    passport = verify_passport(raw, issuer_config(), now=NOW)
    assert passport.subject == SUBJECT
    assert passport.issuer == ISSUER
    assert passport.raw == raw
    assert passport.dropped_visas == ()
    (visa,) = passport.visas
    assert visa.is_ras_visa
    assert visa.source == "https://ncbi.nlm.nih.gov/gap"
    first, second = visa.dbgap_permissions
    assert (first.phs_id, first.version, first.consent_group, first.role) == ("phs000123", "v2", "c1", "pi")
    assert first.expiration == NOW + 30 * 86400
    # Tolerant parsing: missing optional fields are fine and unknown ones are kept.
    assert (second.phs_id, second.version, second.expiration) == ("phs000456", None, None)
    assert second.extra == {"future_field": 7}


def test_empty_permissions():
    passport = verify_passport(make_passport([]), issuer_config(), now=NOW)
    (visa,) = passport.visas
    assert visa.dbgap_permissions == ()


def test_malformed_permission_entries_are_skipped():
    raw = sign(passport_claims([sign(visa_claims([{"version": "v1"}, "junk", permission()]))]))
    (visa,) = verify_passport(raw, issuer_config(), now=NOW).visas
    assert [p.phs_id for p in visa.dbgap_permissions] == ["phs000123"]


@pytest.mark.parametrize("expiration", ["2020-01-01T00:00:00Z", "junk", {}, [], True])
def test_permission_with_unreadable_expiration_is_skipped(expiration):
    # Treating these as "no expiry" would let a garbled field extend access to the passport's lifetime.
    raw = make_passport([permission("phs000123", expiration=expiration), permission("phs000456")])
    (visa,) = verify_passport(raw, issuer_config(), now=NOW).visas
    assert [p.phs_id for p in visa.dbgap_permissions] == ["phs000456"]


def test_numeric_string_expiration_is_read():
    raw = make_passport([permission("phs000123", expiration=str(NOW + 100))])
    (visa,) = verify_passport(raw, issuer_config(), now=NOW).visas
    assert visa.dbgap_permissions[0].expiration == NOW + 100


def test_passport_with_no_visas():
    passport = verify_passport(sign(passport_claims([])), issuer_config(), now=NOW)
    assert passport.visas == ()


def test_token_without_passport_claim_is_not_a_passport():
    # RAS signs ID and access tokens with the same key; one stored in place of the passport must not pass.
    claims = passport_claims([])
    del claims["ga4gh_passport_v1"]
    with pytest.raises(PassportVerificationError, match="ga4gh_passport_v1"):
        verify_passport(sign(claims), issuer_config(), now=NOW)


def test_passport_claim_must_be_a_list():
    with pytest.raises(PassportVerificationError, match="not a list"):
        verify_passport(sign(passport_claims(ga4gh_passport_v1="visa")), issuer_config(), now=NOW)


def test_future_nbf_rejected():
    with pytest.raises(PassportVerificationError, match="not valid yet"):
        verify_passport(sign(passport_claims([], nbf=NOW + 3600)), issuer_config(), now=NOW)


@pytest.mark.parametrize("claim", ["iat", "sub", "exp", "iss"])
def test_passport_missing_required_claim_rejected(claim):
    claims = passport_claims([])
    del claims[claim]
    with pytest.raises(PassportVerificationError):
        verify_passport(sign(claims), issuer_config(), now=NOW)


def _only_dropped_visa(raw_visa: str):
    """Verify a passport holding just ``raw_visa`` and return its one dropped visa."""
    passport = verify_passport(sign(passport_claims([raw_visa])), issuer_config(), now=NOW)
    assert passport.visas == ()
    (dropped,) = passport.dropped_visas
    return dropped


def test_visa_without_visa_object_is_dropped():
    visa = visa_claims(jti="no-visa-object")
    del visa["ga4gh_visa_v1"]
    dropped = _only_dropped_visa(sign(visa))
    assert "ga4gh_visa_v1" in dropped.reason
    assert dropped.jti == "no-visa-object"


def test_expired_passport_rejected():
    raw = make_passport(iat=NOW - 13 * 3600, exp=NOW - 3600)
    with pytest.raises(TokenExpiredError):
        verify_passport(raw, issuer_config(), now=NOW)


def test_leeway_allows_just_expired_passport():
    raw = make_passport(exp=NOW - 30)
    verify_passport(raw, issuer_config(leeway=60), now=NOW)
    with pytest.raises(TokenExpiredError):
        verify_passport(raw, issuer_config(leeway=0), now=NOW)


def test_future_iat_rejected():
    raw = make_passport(iat=NOW + 600)
    with pytest.raises(PassportVerificationError, match="future"):
        verify_passport(raw, issuer_config(), now=NOW)


def test_missing_exp_rejected():
    claims = passport_claims([])
    del claims["exp"]
    with pytest.raises(PassportVerificationError):
        verify_passport(sign(claims), issuer_config(), now=NOW)


def test_expired_visa_is_dropped_not_fatal():
    good = sign(visa_claims([permission("phs000001")]))
    stale = sign(visa_claims([permission("phs000002")], iat=NOW - 7200, exp=NOW - 3600, jti="stale-visa"))
    passport = verify_passport(sign(passport_claims([good, stale])), issuer_config(), now=NOW)
    assert [v.dbgap_permissions[0].phs_id for v in passport.visas] == ["phs000001"]
    (dropped,) = passport.dropped_visas
    assert dropped.jti == "stale-visa"
    assert dropped.expires_at == NOW - 3600


def test_passport_bad_signature():
    raw = sign(passport_claims([]), key=OTHER_KEY)
    with pytest.raises(PassportVerificationError, match="failed verification"):
        verify_passport(raw, issuer_config(), now=NOW)


def test_tampered_payload_rejected():
    header, _, signature = make_passport([]).split(".")
    forged = _b64(json.dumps(passport_claims([], sub="someone-else")).encode())
    with pytest.raises(PassportVerificationError):
        verify_passport(f"{header}.{forged}.{signature}", issuer_config(), now=NOW)


def test_visa_with_bad_signature_is_dropped_and_the_rest_kept():
    good = sign(visa_claims([permission("phs000001")]))
    forged = sign(visa_claims([permission("phs000002")]), key=OTHER_KEY)
    passport = verify_passport(sign(passport_claims([forged, good])), issuer_config(), now=NOW)
    assert [v.dbgap_permissions[0].phs_id for v in passport.visas] == ["phs000001"]
    (dropped,) = passport.dropped_visas
    assert dropped.reason.startswith("visa 0")
    # Nothing in a visa that failed its signature can be believed, not even its id.
    assert dropped.jti is None and dropped.expires_at is None
    assert forged not in repr(passport)


def test_visa_is_not_dropped_when_the_keys_cannot_be_fetched():
    """An outage at the issuer must read as "can't check", not as a visa that failed."""
    visa = sign(visa_claims([permission()]), kid=OTHER_KID)
    fetcher = OutageFetcher(jwks_doc((KEY, KID)))
    config = issuer_config(fetcher, min_refresh_interval=0)
    verify_passport(make_passport(), config, now=NOW)
    fetcher.down = True
    with pytest.raises(VerificationUnavailableError):
        verify_passport(sign(passport_claims([visa])), config, now=NOW)


def test_wrong_issuer():
    with pytest.raises(PassportVerificationError, match="wrong issuer"):
        verify_passport(make_passport(iss="https://evil.example"), issuer_config(), now=NOW)


def test_visa_from_another_issuer_is_dropped():
    dropped = _only_dropped_visa(sign(visa_claims(iss="https://evil.example")))
    assert dropped.reason == "visa 0 has the wrong issuer"


def test_alg_none_rejected():
    raw = jwt.encode(passport_claims([]), "", algorithm="none")
    with pytest.raises(PassportVerificationError, match="disallowed algorithm"):
        verify_passport(raw, issuer_config(), now=NOW)


def test_visa_alg_none_is_dropped():
    dropped = _only_dropped_visa(jwt.encode(visa_claims(), "", algorithm="none"))
    assert "disallowed algorithm" in dropped.reason


def test_hs256_with_public_key_as_secret_rejected():
    # The classic RS256 -> HS256 confusion: sign with the issuer's *public* key as
    # an HMAC secret. PyJWT refuses to build this, so assemble it by hand.
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = _b64(json.dumps(passport_claims([])).encode())
    signature = _b64(hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    with pytest.raises(PassportVerificationError, match="disallowed algorithm"):
        verify_passport(f"{header}.{payload}.{signature}", issuer_config(), now=NOW)


def test_rs512_rejected():
    raw = sign(passport_claims([]), algorithm="RS512")
    with pytest.raises(PassportVerificationError, match="disallowed algorithm"):
        verify_passport(raw, issuer_config(), now=NOW)


def test_missing_kid_rejected():
    with pytest.raises(PassportVerificationError, match="no kid"):
        verify_passport(sign(passport_claims([]), kid=None), issuer_config(), now=NOW)


def test_not_a_jwt():
    with pytest.raises(PassportVerificationError):
        verify_passport("not-a-token", issuer_config(), now=NOW)


def test_visa_for_another_subject_is_dropped():
    dropped = _only_dropped_visa(sign(visa_claims(sub="another-subject", jti="someone-elses")))
    assert "subject" in dropped.reason
    assert dropped.jti == "someone-elses"


def test_expired_visa_for_another_subject_is_dropped_for_the_subject():
    visa = sign(visa_claims(sub="another-subject", iat=NOW - 7200, exp=NOW - 3600))
    assert "subject" in _only_dropped_visa(visa).reason


def test_kid_miss_triggers_one_jwks_refresh():
    # The issuer rotates to a new key after Galaxy cached the old set.
    fetcher = CountingFetcher(jwks_doc((KEY, KID)), jwks_doc((KEY, KID), (OTHER_KEY, OTHER_KID)))
    config = issuer_config(fetcher=fetcher, min_refresh_interval=0)
    verify_passport(make_passport([]), config, now=NOW)
    assert len(fetcher.calls) == 1
    rotated = sign(passport_claims([sign(visa_claims(), key=OTHER_KEY, kid=OTHER_KID)]), key=OTHER_KEY, kid=OTHER_KID)
    verify_passport(rotated, config, now=NOW)
    assert len(fetcher.calls) == 2
    # Cached now, no further fetches.
    verify_passport(rotated, config, now=NOW)
    assert len(fetcher.calls) == 2


def test_unknown_kid_after_refresh_fails():
    fetcher = CountingFetcher(jwks_doc((KEY, KID)))
    config = issuer_config(fetcher=fetcher, min_refresh_interval=0)
    with pytest.raises(PassportVerificationError, match="No signing key"):
        verify_passport(sign(passport_claims([]), key=OTHER_KEY, kid="unknown"), config, now=NOW)


def test_kid_miss_refresh_is_rate_limited():
    clock = [1000.0]
    fetcher = CountingFetcher(jwks_doc((KEY, KID)))
    client = JwksClient("https://jwks.example", fetcher=fetcher, min_refresh_interval=60, clock=lambda: clock[0])
    client.get_signing_key(KID)
    for _ in range(5):
        with pytest.raises(JwksError):
            client.get_signing_key("made-up")
    assert len(fetcher.calls) == 1
    clock[0] += 61
    with pytest.raises(JwksError):
        client.get_signing_key("made-up")
    assert len(fetcher.calls) == 2


def test_jwks_cache_expires():
    clock = [1000.0]
    fetcher = CountingFetcher(jwks_doc((KEY, KID)))
    client = JwksClient("https://jwks.example", fetcher=fetcher, cache_ttl=3600, clock=lambda: clock[0])
    client.get_signing_key(KID)
    clock[0] += 3601
    client.get_signing_key(KID)
    assert len(fetcher.calls) == 2


def test_jwks_skips_non_rsa_keys():
    doc = jwks_doc((KEY, KID))
    doc["keys"].append({"kty": "oct", "kid": "hmac", "k": _b64(b"secret")})
    doc["keys"].append(dict(public_jwk(OTHER_KEY, "encryption"), use="enc"))
    client = JwksClient("https://jwks.example", fetcher=CountingFetcher(doc))
    client.get_signing_key(KID)
    with pytest.raises(JwksError):
        client.get_signing_key("hmac")
    with pytest.raises(JwksError):
        client.get_signing_key("encryption")


class OutageFetcher:
    def __init__(self, doc):
        self.doc = doc
        self.down = False
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        if self.down:
            raise requests.ConnectionError("RAS is down")
        return self.doc


def test_failed_jwks_fetch_is_not_retried_on_every_lookup():
    clock = [1000.0]
    fetcher = OutageFetcher(jwks_doc((KEY, KID)))
    fetcher.down = True
    client = JwksClient("https://jwks.example", fetcher=fetcher, min_refresh_interval=60, clock=lambda: clock[0])
    for _ in range(5):
        with pytest.raises(JwksUnavailableError):
            client.get_signing_key(KID)
    assert fetcher.calls == 1
    clock[0] += 61
    fetcher.down = False
    assert client.get_signing_key(KID)
    assert fetcher.calls == 2


def test_expired_jwks_cache_is_kept_while_the_issuer_is_down():
    clock = [1000.0]
    fetcher = OutageFetcher(jwks_doc((KEY, KID)))
    client = JwksClient("https://jwks.example", fetcher=fetcher, cache_ttl=3600, clock=lambda: clock[0])
    client.get_signing_key(KID)
    clock[0] += 3601
    fetcher.down = True
    for _ in range(5):
        assert client.get_signing_key(KID)
    assert fetcher.calls == 2
    # An unknown kid during the outage might be a rotation we couldn't fetch, not a forgery.
    with pytest.raises(JwksUnavailableError):
        client.get_signing_key("rotated")


def test_jwks_skips_malformed_entries():
    doc = jwks_doc((KEY, KID))
    doc["keys"][:0] = ["not-a-key", 1, None]
    client = JwksClient("https://jwks.example", fetcher=CountingFetcher(doc))
    assert client.get_signing_key(KID)


def test_jwks_fetch_failure_is_verification_error():
    def broken(url):
        raise ValueError("bad json")

    config = IssuerConfig(issuer=ISSUER, jwks=JwksClient("https://jwks.example", fetcher=broken))
    with pytest.raises(PassportVerificationError, match="Could not fetch JWKS"):
        verify_passport(make_passport([]), config, now=NOW)


@pytest.mark.skipif(not os.environ.get(FIXTURES_ENV), reason=f"set {FIXTURES_ENV} to the RAS fixtures directory")
def test_real_ras_staging_passport():
    fixtures = Path(os.environ[FIXTURES_ENV])
    jwks = json.loads((fixtures / "stsstg-jwks.json").read_text())
    userinfo = json.loads((fixtures / "userinfo-v11.json").read_text())
    raw = userinfo["passport_jwt_v11"]
    issued_at = jwt.decode(raw, options={"verify_signature": False})["iat"]
    config = IssuerConfig(
        issuer="https://stsstg.nih.gov",
        jwks=JwksClient("https://stsstg.nih.gov/openid/connect/jwks.json", fetcher=lambda url: jwks),
    )
    passport = verify_passport(raw, config, now=issued_at)
    assert passport.subject == userinfo["sub"]
    (visa,) = passport.visas
    assert visa.is_ras_visa
    assert visa.dbgap_permissions == ()


@pytest.mark.parametrize("bad_doc", [{"keys": []}, {"error": "temporarily_unavailable"}, ["not", "a", "dict"]])
def test_jwks_refresh_with_no_usable_keys_keeps_the_cached_ones(bad_doc):
    clock = [1000.0]
    fetcher = CountingFetcher(jwks_doc((KEY, KID)), bad_doc)
    client = JwksClient("https://jwks.example", fetcher=fetcher, cache_ttl=3600, clock=lambda: clock[0])
    good = client.get_signing_key(KID)
    clock[0] += 3601
    assert client.get_signing_key(KID) is good
    assert len(fetcher.calls) == 2


@pytest.mark.parametrize("bad_doc", [{"keys": []}, {"error": "temporarily_unavailable"}])
def test_jwks_with_no_usable_keys_is_unavailable(bad_doc):
    client = JwksClient("https://jwks.example", fetcher=CountingFetcher(bad_doc))
    with pytest.raises(JwksUnavailableError):
        client.get_signing_key(KID)


def test_repr_does_not_include_the_signed_tokens():
    raw = make_passport([permission()])
    passport = verify_passport(raw, issuer_config(), now=NOW)
    text = repr(passport)
    assert raw not in text
    assert passport.visas[0].raw not in text
    assert SUBJECT in text
