from datetime import (
    datetime,
    timezone,
)

from galaxy.authnz.ras.models import (
    DbgapPermission,
    DroppedVisa,
    Passport,
    Visa,
)
from galaxy.webapps.galaxy.api.ga4gh_passport import _summary

ISSUER = "https://ras.example.org"
NOW = 1_800_000_000


def passport() -> Passport:
    grant = DbgapPermission("phs000001", "v1", "p1", "c1", "GRU", "pi", NOW + 86400)
    visa = Visa(
        raw="visa-secret",
        issuer=ISSUER,
        subject="alice",
        issued_at=NOW,
        expires_at=NOW + 3600,
        jti=None,
        visa_type="https://ras.nih.gov/visas/v1.1",
        value="v",
        source="s",
        by=None,
        asserted=NOW,
        dbgap_permissions=(grant,),
    )
    return Passport(
        raw="secret",
        issuer=ISSUER,
        subject="alice",
        issued_at=NOW,
        expires_at=NOW + 3600,
        jti=None,
        txn="txn-1",
        visas=(visa,),
        dropped_visas=(DroppedVisa(jti=None, expires_at=NOW - 60, reason="expired"),),
    )


def test_summary_reports_usable_until_and_never_the_token():
    summary = _summary(passport(), usable_until=NOW + 600)
    assert summary.usable_until == datetime.fromtimestamp(NOW + 600, tz=timezone.utc)
    assert summary.visas_expire_at == datetime.fromtimestamp(NOW + 3600, tz=timezone.utc)
    assert [g.authorization for v in summary.visas for g in v.grants] == ["phs000001.c1"]
    assert summary.rejected_visas == 1
    assert "secret" not in summary.model_dump_json()
