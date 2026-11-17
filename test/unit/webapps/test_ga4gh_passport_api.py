from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace

import pytest

from galaxy.authnz.ras.login import PassportLoginError
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
from galaxy.exceptions import (
    AuthenticationRequired,
    RequestParameterInvalidException,
    ServerNotConfiguredForRequest,
)
from galaxy.managers.ga4gh_passports import (
    PassportRefused,
    REFUSAL_MESSAGES,
)
from galaxy.schema.ga4gh_passport import (
    GA4GHPassportStatus,
    GA4GHRasLoginCallback,
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

    def __init__(self, lookup: PassportLookup, refusal: str | None = None):
        self._lookup = lookup
        self._refusal = refusal
        self.loads = 0
        self.accepted: list[tuple[object, str]] = []

    def accept(self, user, token):
        if self._refusal:
            raise PassportRefused(self._refusal)
        self.accepted.append((user, token))

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


def api_for(manager: FakeManager) -> FastAPIGA4GHPassport:
    api = FastAPIGA4GHPassport.__new__(FastAPIGA4GHPassport)
    api.manager = manager  # type: ignore[assignment]
    return api


def show(lookup: PassportLookup) -> tuple[GA4GHPassportStatus, FakeManager]:
    manager = FakeManager(lookup)
    return api_for(manager).show(user=SimpleNamespace(id=1)), manager  # type: ignore[arg-type]


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


# --- the direct RAS login -----------------------------------------------------------------


class FakeLogin:
    def __init__(self, error: str | None = None):
        self.error = error

    def start(self, user):
        return "https://ras.example.org/authorize?state=s"

    def finish(self, user, callback_url):
        if self.error:
            raise PassportLoginError(self.error)
        return "raw-passport"


USER = SimpleNamespace(id=1)


def trans(login=None, user=USER, gate=True):
    ras_gate = SimpleNamespace(direct_login=login) if gate else None
    return SimpleNamespace(app=SimpleNamespace(ras_passport_gate=ras_gate), user=user)


CALLBACK = GA4GHRasLoginCallback(callback_url="https://callback.example.org/cb?code=c&state=s")


def test_ras_login_start():
    response = api_for(FakeManager(PassportLookup(PassportStatus.MISSING))).start_ras_login(trans(FakeLogin()))
    assert response.authorization_url.startswith("https://ras.example.org/authorize")
    assert response.expires_in == 600


@pytest.mark.parametrize("context", [trans(gate=False), trans(login=None)])
def test_ras_login_refused_when_not_configured(context):
    api = api_for(FakeManager(PassportLookup(PassportStatus.MISSING)))
    with pytest.raises(ServerNotConfiguredForRequest):
        api.start_ras_login(context)
    with pytest.raises(ServerNotConfiguredForRequest):
        api.finish_ras_login(CALLBACK, context)


def test_ras_login_needs_a_user():
    api = api_for(FakeManager(PassportLookup(PassportStatus.MISSING)))
    with pytest.raises(AuthenticationRequired):
        api.start_ras_login(trans(FakeLogin(), user=None))


def test_ras_login_callback_hands_the_passport_to_the_manager():
    manager = FakeManager(PassportLookup(PassportStatus.OK, passport()))
    status = api_for(manager).finish_ras_login(CALLBACK, trans(FakeLogin()))
    assert manager.accepted == [(USER, "raw-passport")]
    assert status.passport_status == GA4GHStoredPassportStatus.OK


def test_ras_login_callback_that_fails_is_400():
    manager = FakeManager(PassportLookup(PassportStatus.MISSING))
    with pytest.raises(RequestParameterInvalidException, match="state does not match"):
        api_for(manager).finish_ras_login(CALLBACK, trans(FakeLogin(error="state does not match")))
    assert manager.accepted == []


@pytest.mark.parametrize("code", ["invalid_passport", "issuer_unavailable", "identity_in_use", "identity_mismatch"])
def test_refused_passport_is_400_with_its_code(code):
    manager = FakeManager(PassportLookup(PassportStatus.MISSING), refusal=code)
    with pytest.raises(RequestParameterInvalidException) as refused:
        api_for(manager).finish_ras_login(CALLBACK, trans(FakeLogin()))
    assert refused.value.status_code == 400
    assert refused.value.extra_error_info == {"ga4gh_passport_error": code}
    assert str(refused.value) == REFUSAL_MESSAGES[code]
