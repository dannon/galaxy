"""GA4GH Passports for DRS (DRS 1.4 PassportAuth): the DRS client, and the DRS file source sending a user's Passport."""

import json
import logging
import os
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace
from typing import cast

import pytest
import responses

from galaxy import exceptions
from galaxy.files import (
    DictFileSourcesUserContext,
    GA4GHPassportCredential,
    ProvidesFileSourcesTransaction,
    ProvidesFileSourcesUserContext,
)
from galaxy.files.sources.util import (
    fetch_drs_to_file,
    retry_and_get,
    RetryOptions,
)
from galaxy.files.uris import redact_url_queries
from ._util import (
    assert_realizes_as,
    configured_file_sources,
    realize_to_temp_file,
    serialize_and_recover,
)

DRS = "https://drs.example.org/ga4gh/drs/v1/objects"
DRS_URI = "drs://drs.example.org/obj1"
PASSPORT = "eyJ.passport.jwt"
SIGNED_URL = "https://my.repository.org/obj1.txt?exp=1700000000&sig=s3cr3t"


def _object(object_id="obj1"):
    return {"id": object_id, "name": "obj1.txt", "access_methods": [{"type": "https", "access_id": "https"}]}


def _passport_handler(body):
    """A DRS endpoint that only answers a POST carrying our passport."""

    def handler(request):
        if json.loads(request.body) != {"passports": [PASSPORT]}:
            return 401, {}, json.dumps({"msg": "no passport", "status_code": 401})
        return 200, {}, json.dumps(body)

    return handler


def _refuse_get(status=401, msg="needs a passport"):
    responses.add(responses.GET, f"{DRS}/obj1", status=status, json={"msg": msg, "status_code": status})


def _controlled_repository(download_status=200):
    """A repository that answers nothing about the object without a passport."""
    _refuse_get()
    responses.add_callback(responses.POST, f"{DRS}/obj1", callback=_passport_handler(_object()))
    responses.add_callback(responses.POST, f"{DRS}/obj1/access/https", callback=_passport_handler({"url": SIGNED_URL}))
    responses.add(responses.GET, SIGNED_URL, body="controlled content", status=download_status)


@responses.activate
def test_passports_are_posted_to_object_and_access(tmp_path):
    """The object is asked for with a plain GET first; the passport goes out once that is refused."""
    _controlled_repository()
    target = tmp_path / "out"

    fetch_drs_to_file(DRS_URI, target, None, passports=[PASSPORT])

    assert target.read_text() == "controlled content"
    assert [(call.request.method, call.request.url) for call in responses.calls] == [
        ("GET", f"{DRS}/obj1"),
        ("POST", f"{DRS}/obj1"),
        ("POST", f"{DRS}/obj1/access/https"),
        ("GET", SIGNED_URL),
    ]


@responses.activate
def test_passports_resent_on_202_retry():
    responses.add(responses.POST, f"{DRS}/obj1", status=202, headers={"Retry-After": "0"})
    responses.add_callback(responses.POST, f"{DRS}/obj1", callback=_passport_handler(_object()))
    retry_options = RetryOptions()
    retry_options.override_retry_after = 0

    response = retry_and_get(f"{DRS}/obj1", retry_options, passports=[PASSPORT])

    assert response.json()["id"] == "obj1"
    assert [json.loads(call.request.body or "") for call in responses.calls] == [{"passports": [PASSPORT]}] * 2


@responses.activate
def test_without_passports_requests_are_gets(tmp_path):
    responses.add(responses.GET, f"{DRS}/obj1", json=_object())
    responses.add(responses.GET, f"{DRS}/obj1/access/https", json={"url": SIGNED_URL})
    responses.add(responses.GET, SIGNED_URL, body="public content")

    fetch_drs_to_file(DRS_URI, tmp_path / "out", None)

    assert {call.request.method for call in responses.calls} == {"GET"}


@pytest.mark.parametrize(
    "passports, status, exception, message",
    [
        (None, 401, exceptions.AuthenticationRequired, "requires authorization .* GA4GH Passport.*: needs a passport"),
        ([PASSPORT], 401, exceptions.AuthenticationRequired, "did not accept the GA4GH Passport: needs a passport"),
        (None, 403, exceptions.ItemAccessibilityException, "denied access to this object: needs a passport"),
        ([PASSPORT], 403, exceptions.ItemAccessibilityException, "does not grant access .*: needs a passport"),
    ],
)
@responses.activate
def test_denied_object(tmp_path, passports, status, exception, message):
    _refuse_get(status)
    if passports:
        responses.add(
            responses.POST, f"{DRS}/obj1", status=status, json={"msg": "needs a passport", "status_code": status}
        )

    with pytest.raises(exception, match=message):
        fetch_drs_to_file(DRS_URI, tmp_path / "out", None, passports=passports)


@responses.activate
def test_denied_access_url(tmp_path):
    """The object may be readable while its access URL is not (or the grant expired in between)."""
    responses.add(responses.GET, f"{DRS}/obj1", json=_object())
    responses.add(
        responses.POST,
        f"{DRS}/obj1/access/https",
        status=403,
        json={"msg": "no visa grants phs000001.c1", "status_code": 403},
    )

    with pytest.raises(exceptions.ItemAccessibilityException, match="no visa grants phs000001.c1"):
        fetch_drs_to_file(DRS_URI, tmp_path / "out", None, passports=[PASSPORT])


@responses.activate
def test_drs_error_without_json_body(tmp_path):
    _refuse_get()
    responses.add(responses.POST, f"{DRS}/obj1", status=401, body="<html>nope</html>")

    with pytest.raises(exceptions.AuthenticationRequired) as e:
        fetch_drs_to_file(DRS_URI, tmp_path / "out", None, passports=[PASSPORT])
    assert str(e.value) == "The DRS server did not accept the GA4GH Passport"


@responses.activate
def test_failed_download_never_reveals_signed_url(tmp_path, caplog):
    _controlled_repository(download_status=403)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(exceptions.MessageException) as e:
        fetch_drs_to_file(DRS_URI, tmp_path / "out", None, passports=[PASSPORT])

    message = str(e.value)
    assert message.startswith(f"Failed to download DRS URI {DRS_URI}: https: ")
    assert "https://my.repository.org/obj1.txt?[redacted]" in message
    assert "s3cr3t" not in message
    assert "s3cr3t" not in caplog.text
    assert PASSPORT not in caplog.text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("no url here", "no url here"),
        ("https://h.org/p", "https://h.org/p"),
        ("https://h.org/p?sig=x&exp=1", "https://h.org/p?[redacted]"),
        ("https://h.org/p#frag", "https://h.org/p?[redacted]"),
        (
            "403 Client Error: Forbidden for url: https://h.org/p?X-Amz-Signature=abc",
            "403 Client Error: Forbidden for url: https://h.org/p?[redacted]",
        ),
        (
            "('https://h.org/a?x=1', 'http://i.org/b?y=2')",
            "('https://h.org/a?[redacted]', 'http://i.org/b?[redacted]')",
        ),
        ("s3://bucket/key?versionId=3 failed", "s3://bucket/key?[redacted] failed"),
    ],
)
def test_redact_url_queries(text, expected):
    assert redact_url_queries(text) == expected


# --- the DRS file source: the user's Passport into jobs ------------------------------------------

FILE_SOURCES_CONF = os.path.join(os.path.dirname(__file__), "drs_passport_file_sources_conf.yml")
PLAIN_FILE_SOURCES_CONF = os.path.join(os.path.dirname(__file__), "drs_file_sources_conf.yml")


def _user(credential: GA4GHPassportCredential | None = None, **kwd) -> DictFileSourcesUserContext:
    return DictFileSourcesUserContext(
        username="alice",
        email="alice@galaxyproject.org",
        preferences={"oidc|bearer_token": "IBearTokens"},
        role_names=set(),
        group_names=set(),
        is_admin=False,
        ga4gh_passport=credential,
        **kwd,
    )


def _credential(fresh_for: int = 600) -> GA4GHPassportCredential:
    return GA4GHPassportCredential(
        passport=PASSPORT, fresh_until=datetime.now(timezone.utc) + timedelta(seconds=fresh_for), txn="txn-1"
    )


def _serialized_drs(conf: str, user_context) -> dict:
    as_dict = configured_file_sources(conf).to_dict(for_serialization=True, user_context=user_context)
    return next(source for source in as_dict["file_sources"] if source["type"] == "drs")


def test_job_gets_the_passport_and_its_freshness_limit():
    credential = _credential()
    user = _user(credential)

    serialized = _serialized_drs(FILE_SOURCES_CONF, user)

    assert serialized["passports"] == [PASSPORT]
    assert datetime.fromisoformat(serialized["auth_expires_at"]) == credential.fresh_until
    assert PASSPORT not in json.dumps(user.to_dict()), "never in the serialized user context"


def test_passport_not_sent_unless_the_source_asks_for_it():
    assert "passports" not in _serialized_drs(PLAIN_FILE_SOURCES_CONF, _user(_credential()))


def test_no_passport_no_passports():
    serialized = _serialized_drs(FILE_SOURCES_CONF, _user())
    assert "passports" not in serialized and "auth_expires_at" not in serialized


def test_anonymous_user_gets_no_passport():
    anonymous = DictFileSourcesUserContext(ga4gh_passport=_credential())
    assert "passports" not in _serialized_drs(FILE_SOURCES_CONF, anonymous)


@responses.activate
def test_job_posts_the_passport(tmp_path):
    """The job side: no user context, only the serialized config."""
    _controlled_repository()
    file_sources = serialize_and_recover(configured_file_sources(FILE_SOURCES_CONF), _user(_credential()))

    assert_realizes_as(file_sources, DRS_URI, "controlled content")
    assert json.loads(responses.calls[1].request.body or "") == {"passports": [PASSPORT]}


def test_job_refuses_a_stale_passport():
    file_sources = serialize_and_recover(configured_file_sources(FILE_SOURCES_CONF), _user(_credential(-1)))

    with pytest.raises(exceptions.FileSourceCredentialExpired, match="too old .* Reconnect your GA4GH Passport"):
        realize_to_temp_file(file_sources, DRS_URI)


@responses.activate
def test_job_without_passport_is_told_to_connect():
    responses.add(responses.GET, f"{DRS}/obj1", status=401, json={"msg": "needs a passport", "status_code": 401})
    file_sources = serialize_and_recover(configured_file_sources(FILE_SOURCES_CONF), _user())

    with pytest.raises(exceptions.AuthenticationRequired, match=r"needs a passport\. Connect your GA4GH Passport"):
        realize_to_temp_file(file_sources, DRS_URI)


@responses.activate
def test_rejected_passport_suggests_reconnecting():
    _refuse_get()
    responses.add(responses.POST, f"{DRS}/obj1", status=401, json={"msg": "visa expired", "status_code": 401})
    file_sources = serialize_and_recover(configured_file_sources(FILE_SOURCES_CONF), _user(_credential()))

    with pytest.raises(exceptions.AuthenticationRequired, match=r"visa expired\. Reconnect your GA4GH Passport"):
        realize_to_temp_file(file_sources, DRS_URI)


@responses.activate
def test_realized_in_process_uses_the_users_passport():
    _controlled_repository()

    assert_realizes_as(
        configured_file_sources(FILE_SOURCES_CONF), DRS_URI, "controlled content", user_context=_user(_credential())
    )
    assert json.loads(responses.calls[1].request.body or "") == {"passports": [PASSPORT]}


def test_realized_in_process_refuses_a_stale_passport():
    with pytest.raises(exceptions.FileSourceCredentialExpired, match="too old"):
        realize_to_temp_file(configured_file_sources(FILE_SOURCES_CONF), DRS_URI, user_context=_user(_credential(-1)))


def test_provides_user_context_reads_the_stored_passport():
    fresh_until = int(datetime.now(timezone.utc).timestamp()) + 600

    class FakePassportManager:
        def current(self, user):
            return SimpleNamespace(raw=PASSPORT, txn="txn-1") if user == "alice" else None

        def fresh_until(self, passport):
            return fresh_until

    def user_context(user, app=None):
        app = app if app is not None else SimpleNamespace(ga4gh_passport_manager=FakePassportManager())
        trans = SimpleNamespace(user=user, app=app)
        return ProvidesFileSourcesUserContext(cast(ProvidesFileSourcesTransaction, trans))

    assert user_context("alice").ga4gh_passport == GA4GHPassportCredential(
        passport=PASSPORT, fresh_until=datetime.fromtimestamp(fresh_until, tz=timezone.utc), txn="txn-1"
    )
    assert user_context("bob").ga4gh_passport is None
    assert user_context(None).ga4gh_passport is None
    # An app without the manager (the Tool Shed, a bare job) has no passports.
    assert user_context("alice", app=SimpleNamespace()).ga4gh_passport is None
    assert user_context("alice", app=SimpleNamespace(ga4gh_passport_manager=None)).ga4gh_passport is None
