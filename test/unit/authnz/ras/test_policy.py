import pytest

from galaxy.authnz.ras.policy import (
    effective_expiry,
    granted_phs_ids,
    grants_access,
    InvalidAccessionError,
    normalize_phs_id,
    parse_accession,
    permissions_for,
)
from galaxy.authnz.ras.verify import verify_passport
from .util import (
    issuer_config,
    make_passport,
    NOW,
    passport_claims,
    permission,
    RAS_VISA,
    sign,
    visa_claims,
)


def _passport(permissions=(), **overrides):
    return verify_passport(make_passport(permissions, **overrides), issuer_config(), now=NOW)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("phs000123", ("phs000123", None, None, None)),
        ("PHS000123", ("phs000123", None, None, None)),
        ("phs123", ("phs000123", None, None, None)),
        (" phs000123.v2 ", ("phs000123", "v2", None, None)),
        ("phs000123.V2.P1.C3", ("phs000123", "v2", "p1", "c3")),
        ("phs000123.c1", ("phs000123", None, None, "c1")),
    ],
)
def test_parse_accession(text, expected):
    accession = parse_accession(text)
    assert (accession.phs_id, accession.version, accession.participant_set, accession.consent_group) == expected


@pytest.mark.parametrize("text", ["", "phs", "123", "phsabc", "phs0001234567", "phs000123.x2", "egad000123"])
def test_parse_accession_rejects(text):
    with pytest.raises(InvalidAccessionError):
        parse_accession(text)


def test_normalize_phs_id():
    assert normalize_phs_id("PHS42.v1") == "phs000042"


def test_match():
    passport = _passport([permission("phs000123")])
    assert grants_access(passport, "phs000123", now=NOW)
    assert grants_access(passport, "PHS123", now=NOW)
    assert grants_access(passport, "phs000123", version="v2", consent_group="c1", now=NOW)
    assert grants_access(passport, "phs000123", version=2, consent_group=1, now=NOW)
    assert grants_access(passport, "phs000123.v2.p1.c1", now=NOW)
    assert granted_phs_ids(passport, now=NOW) == ["phs000123"]


def test_other_study_not_granted():
    passport = _passport([permission("phs000123")])
    assert not grants_access(passport, "phs000124", now=NOW)


def test_version_mismatch():
    passport = _passport([permission("phs000123", version="v2")])
    assert not grants_access(passport, "phs000123", version="v3", now=NOW)
    assert not grants_access(passport, "phs000123.v1", now=NOW)


def test_consent_group_mismatch():
    passport = _passport([permission("phs000123", consent_group="c1")])
    assert not grants_access(passport, "phs000123", consent_group="c2", now=NOW)
    assert not grants_access(passport, "phs000123.v2.c2", now=NOW)


def test_expired_permission():
    passport = _passport([permission("phs000123", expiration=NOW - 1)])
    assert not grants_access(passport, "phs000123", now=NOW)
    assert permissions_for(passport, now=NOW) == []


def test_permission_with_no_expiration_follows_passport():
    passport = _passport([permission("phs000123", expiration=None)])
    (granted,) = permissions_for(passport, now=NOW)
    assert granted.expires_at == passport.expires_at


def test_no_visas():
    passport = verify_passport(sign(passport_claims([])), issuer_config(), now=NOW)
    assert not grants_access(passport, "phs000123", now=NOW)
    assert granted_phs_ids(passport, now=NOW) == []


def test_empty_permissions():
    assert not grants_access(_passport([]), "phs000123", now=NOW)


def test_multiple_visas():
    first = sign(visa_claims([permission("phs000001", consent_group="c1")]))
    second = sign(visa_claims([permission("phs000002"), permission("phs000001", consent_group="c2")]))
    passport = verify_passport(sign(passport_claims([first, second])), issuer_config(), now=NOW)
    assert granted_phs_ids(passport, now=NOW) == ["phs000001", "phs000002"]
    assert grants_access(passport, "phs000001", consent_group="c1", now=NOW)
    assert grants_access(passport, "phs000001", consent_group="c2", now=NOW)
    assert not grants_access(passport, "phs000001", consent_group="c3", now=NOW)


def test_non_ras_visa_grants_nothing():
    visa = sign(visa_claims([permission("phs000123")], ga4gh_visa_v1=dict(RAS_VISA, type="ControlledAccessGrants")))
    passport = verify_passport(sign(passport_claims([visa])), issuer_config(), now=NOW)
    assert not grants_access(passport, "phs000123", now=NOW)


def test_effective_expiry_is_the_minimum():
    visa = sign(visa_claims([permission("phs000123", expiration=NOW + 100)], exp=NOW + 200))
    passport = verify_passport(sign(passport_claims([visa], exp=NOW + 300)), issuer_config(), now=NOW)
    (granted,) = permissions_for(passport, now=NOW)
    assert granted.expires_at == NOW + 100
    assert effective_expiry(passport) == NOW + 300
    assert effective_expiry(passport, granted.visa) == NOW + 200
    assert effective_expiry(passport, granted.visa, granted.permission) == NOW + 100
    # Once the permission lapses the passport no longer grants the study.
    assert grants_access(passport, "phs000123", now=NOW + 99)
    assert not grants_access(passport, "phs000123", now=NOW + 100)


def test_access_ends_with_the_visa():
    visa = sign(visa_claims([permission("phs000123", expiration=None)], exp=NOW + 200))
    passport = verify_passport(sign(passport_claims([visa], exp=NOW + 300)), issuer_config(), now=NOW)
    assert not grants_access(passport, "phs000123", now=NOW + 250)


def test_unparseable_permission_ignored():
    passport = _passport([permission("not-a-study"), permission("phs000123", version="latest")])
    assert permissions_for(passport, now=NOW) == []
