from datetime import (
    datetime,
    timezone,
)

from galaxy.security.ga4gh_passport import (
    DbgapGrant,
    VerifiedPassport,
    Visa,
)
from galaxy.webapps.galaxy.api.ga4gh_passport import _summary

ISSUER = "https://ras.example.org"
NOW = 1_800_000_000


def passport() -> VerifiedPassport:
    grant = DbgapGrant("phs000001", "v1", "p1", "c1", "GRU", "pi", NOW + 86400)
    visa = Visa(ISSUER, "alice", "https://ras.nih.gov/visas/v1.1", "v", "s", None, NOW, NOW, NOW + 3600, True, (grant,))
    return VerifiedPassport(ISSUER, "alice", "txn-1", NOW, NOW + 3600, (visa,), ("bad signature",), token="secret")


def test_summary_reports_usable_until_and_never_the_token():
    summary = _summary(passport(), usable_until=NOW + 600)
    assert summary.usable_until == datetime.fromtimestamp(NOW + 600, tz=timezone.utc)
    assert summary.visas_expire_at == datetime.fromtimestamp(NOW + 3600, tz=timezone.utc)
    assert [g.authorization for v in summary.visas for g in v.grants] == ["phs000001.c1"]
    assert summary.rejected_visas == 1
    assert "secret" not in summary.model_dump_json()
