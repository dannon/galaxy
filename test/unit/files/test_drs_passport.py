"""DRS 1.4 PassportAuth in the DRS client: POSTing GA4GH Passports, errors, signed URL redaction."""

import json
import logging

import pytest
import responses

from galaxy import exceptions
from galaxy.files.sources.util import (
    fetch_drs_to_file,
    retry_and_get,
    RetryOptions,
)
from galaxy.files.uris import redact_url_queries

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


def _controlled_repository(download_status=200):
    responses.add_callback(responses.POST, f"{DRS}/obj1", callback=_passport_handler(_object()))
    responses.add_callback(responses.POST, f"{DRS}/obj1/access/https", callback=_passport_handler({"url": SIGNED_URL}))
    responses.add(responses.GET, SIGNED_URL, body="controlled content", status=download_status)


@responses.activate
def test_passports_are_posted_to_object_and_access(tmp_path):
    _controlled_repository()
    target = tmp_path / "out"

    fetch_drs_to_file(DRS_URI, target, None, passports=[PASSPORT])

    assert target.read_text() == "controlled content"
    assert [(call.request.method, call.request.url) for call in responses.calls] == [
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
    method = responses.POST if passports else responses.GET
    responses.add(method, f"{DRS}/obj1", status=status, json={"msg": "needs a passport", "status_code": status})

    with pytest.raises(exception, match=message):
        fetch_drs_to_file(DRS_URI, tmp_path / "out", None, passports=passports)


@responses.activate
def test_denied_access_url(tmp_path):
    """The object may be readable while its access URL is not (or the grant expired in between)."""
    responses.add(responses.POST, f"{DRS}/obj1", json=_object())
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
