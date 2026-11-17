from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace

import pytest

from galaxy.authnz.ras.models import (
    DbgapPermission,
    DroppedVisa,
    Passport,
    Visa,
)
from galaxy.authnz.ras.store import (
    PassportLookup,
    PassportStatus,
)
from galaxy.schema.ga4gh_passport import (
    GA4GHPassportStatus,
    GA4GHStoredPassportStatus,
)
from galaxy.webapps.galaxy.api.ga4gh_passport import (
    _summary,
    FastAPIGA4GHPassport,
)

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


class FakeManager:
    """Answers what ``show`` asks; counts store reads."""

    enabled = True

    def __init__(self, lookup: PassportLookup):
        self._lookup = lookup
        self.loads = 0

    def lookup(self, user):
        self.loads += 1
        return self._lookup

    def current(self, user):
        raise AssertionError("show reads the store once, through lookup")

    def identity(self, user):
        return None

    def fresh_until(self, passport):
        return NOW + 600

    def data_access(self, user):
        return {}

    def ended_access(self, user):
        return {}


def show(lookup: PassportLookup) -> tuple[GA4GHPassportStatus, FakeManager]:
    manager = FakeManager(lookup)
    api = FastAPIGA4GHPassport.__new__(FastAPIGA4GHPassport)
    api.manager = manager  # type: ignore[assignment]
    return api.show(user=SimpleNamespace(id=1)), manager  # type: ignore[arg-type]


def test_show_reports_ok_with_the_passport():
    status, manager = show(PassportLookup(PassportStatus.OK, passport()))
    assert status.passport_status == GA4GHStoredPassportStatus.OK
    assert status.passport is not None and status.passport.sub == "alice"
    assert manager.loads == 1


@pytest.mark.parametrize("store_status", ["missing", "expired", "invalid", "unavailable"])
def test_show_reports_why_there_is_no_passport(store_status):
    status, manager = show(PassportLookup(PassportStatus(store_status)))
    assert status.passport_status.value == store_status
    assert status.passport is None
    assert manager.loads == 1


def test_summary_reports_usable_until_and_never_the_token():
    summary = _summary(passport(), usable_until=NOW + 600)
    assert summary.usable_until == datetime.fromtimestamp(NOW + 600, tz=timezone.utc)
    assert summary.visas_expire_at == datetime.fromtimestamp(NOW + 3600, tz=timezone.utc)
    assert [g.authorization for v in summary.visas for g in v.grants] == ["phs000001.c1"]
    assert summary.rejected_visas == 1
    assert "secret" not in summary.model_dump_json()
