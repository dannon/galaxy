import json
import os
import socket
import tempfile
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import cast

import pytest
import responses

from galaxy.exceptions import (
    AuthenticationFailed,
    FileSourceCredentialExpired,
    RasPassportRequiredException,
    UpstreamProxyError,
)
from galaxy.files import (
    DictFileSourcesUserContext,
    ProvidesFileSourcesTransaction,
    ProvidesFileSourcesUserContext,
)
from galaxy.files.models import RealizedSourceMetadata
from galaxy.files.sources.util import sanitize_drs_name
from ._util import (
    assert_realizes_as,
    assert_realizes_contains,
    configured_file_sources,
    serialize_and_recover,
    user_context_fixture,
)

SCRIPT_DIRECTORY = os.path.abspath(os.path.dirname(__file__))
FILE_SOURCES_CONF = os.path.join(SCRIPT_DIRECTORY, "drs_file_sources_conf.yml")
DRS_OIDC_FILE_SOURCES_CONF = os.path.join(SCRIPT_DIRECTORY, "drs_oidc_file_sources_conf.yml")
DRS_RAS_PASSPORT_FILE_SOURCES_CONF = os.path.join(SCRIPT_DIRECTORY, "drs_ras_passport_file_sources_conf.yml")


def test_provides_file_sources_user_context_oidc_access_tokens():
    """ProvidesFileSourcesUserContext.oidc_access_tokens reads all providers from social_auth."""

    class DummyToken:
        def __init__(self, provider, access_token):
            self.provider = provider
            self.extra_data = {"access_token": access_token}

    class DummyUser:
        social_auth = [
            DummyToken("oidc", "oidc-token"),
            DummyToken("keycloak", "keycloak-token"),
            DummyToken("no_token_provider", None),  # skipped — no access_token
        ]

    class DummyTrans:
        user = DummyUser()

    tokens = ProvidesFileSourcesUserContext(cast(ProvidesFileSourcesTransaction, DummyTrans())).oidc_access_tokens
    assert tokens == {"oidc": "oidc-token", "keycloak": "keycloak-token"}


def test_provides_file_sources_user_context_oidc_access_tokens_anonymous():
    """ProvidesFileSourcesUserContext.oidc_access_tokens returns None for anonymous users."""

    class DummyTrans:
        user = None

    assert ProvidesFileSourcesUserContext(cast(ProvidesFileSourcesTransaction, DummyTrans())).oidc_access_tokens is None


def test_drs_http_headers_template_expansion():
    """Dict values in http_headers are expanded as templates during file source serialization."""
    oidc_token = "my-token"
    file_sources = configured_file_sources(DRS_OIDC_FILE_SOURCES_CONF)
    user_context = DictFileSourcesUserContext(
        username="alice",
        email="alice@galaxyproject.org",
        preferences={},
        role_names=set(),
        group_names=set(),
        is_admin=False,
        oidc_access_tokens={"oidc": oidc_token},
    )
    file_sources_dict = file_sources.to_dict(for_serialization=True, user_context=user_context)
    drs_source = next(s for s in file_sources_dict["file_sources"] if s.get("type") == "drs")
    assert drs_source["http_headers"]["Authorization"] == f"Bearer {oidc_token}"


def test_drs_oidc_token_wrong_provider_raises():
    """Referencing a provider the user doesn't have raises KeyError at serialization time."""
    file_sources = configured_file_sources(DRS_OIDC_FILE_SOURCES_CONF)
    user_context = DictFileSourcesUserContext(
        username="alice",
        email="alice@galaxyproject.org",
        preferences={},
        role_names=set(),
        group_names=set(),
        is_admin=False,
        oidc_access_tokens={"keycloak": "kc-token"},
    )
    with pytest.raises(KeyError):
        file_sources.to_dict(for_serialization=True, user_context=user_context)


def test_drs_oidc_token_no_tokens_raises():
    """A user with no OIDC tokens raises TypeError at serialization time."""
    file_sources = configured_file_sources(DRS_OIDC_FILE_SOURCES_CONF)
    user_context = DictFileSourcesUserContext(
        username="alice",
        email="alice@galaxyproject.org",
        preferences={},
        role_names=set(),
        group_names=set(),
        is_admin=False,
    )
    with pytest.raises(TypeError):
        file_sources.to_dict(for_serialization=True, user_context=user_context)


@responses.activate
def test_file_source_drs_http():
    def drs_repo_handler(request):
        assert request.headers["Authorization"] == "Bearer IBearTokens"
        data = {
            "id": "314159",
            "name": "hello-314159",
            "access_methods": [
                {
                    "type": "https",
                    "access_id": "1234",
                }
            ],
        }
        return (200, {}, json.dumps(data))

    def access_handler(request):
        assert request.headers["Authorization"] == "Bearer IBearTokens"
        access_data = {
            "url": "https://my.repository.org/myfile.txt",
            "headers": ["Authorization: Basic Z2E0Z2g6ZHJz"],
        }
        return (200, {}, json.dumps(access_data))

    responses.add_callback(
        responses.GET,
        "https://drs.example.org/ga4gh/drs/v1/objects/314159",
        callback=drs_repo_handler,
        content_type="application/json",
    )

    responses.add_callback(
        responses.GET,
        "https://drs.example.org/ga4gh/drs/v1/objects/314159/access/1234",
        callback=access_handler,
        content_type="application/json",
    )

    test_url = "drs://drs.example.org/314159"

    def check_specific_header(request):
        assert request.headers["Authorization"] == "Basic Z2E0Z2g6ZHJz"
        return 200, {}, "hello drs world"

    responses.add_callback(
        responses.GET,
        "https://my.repository.org/myfile.txt",
        callback=check_specific_header,
    )
    user_context = user_context_fixture()
    file_sources = configured_file_sources(FILE_SOURCES_CONF)
    file_source_pair = file_sources.get_file_source_path(test_url)

    assert file_source_pair.path == test_url
    assert file_source_pair.file_source.id == "test1"

    assert_realizes_as(file_sources, test_url, "hello drs world", user_context=user_context)

    # The DRS object's own name is reported back through metadata_out so callers can
    # name the dataset something better than the identifier in the URI.
    metadata_out: RealizedSourceMetadata = {}
    with tempfile.NamedTemporaryFile(mode="r") as temp:
        file_source_pair.file_source.realize_to(
            file_source_pair.path, temp.name, user_context=user_context, metadata_out=metadata_out
        )
    assert metadata_out["name"] == "hello-314159"


@responses.activate
def test_file_source_drs_omits_unusable_name_from_metadata():
    """A DRS object with no usable name leaves metadata_out empty so callers keep their own fallback."""

    def drs_repo_handler(request):
        data = {
            "id": "271828",
            "access_methods": [{"type": "https", "access_url": {"url": "https://my.repository.org/myfile.txt"}}],
        }
        return (200, {}, json.dumps(data))

    responses.add_callback(
        responses.GET,
        "https://drs.example.org/ga4gh/drs/v1/objects/271828",
        callback=drs_repo_handler,
        content_type="application/json",
    )

    responses.add(responses.GET, "https://my.repository.org/myfile.txt", body="hello drs world")

    test_url = "drs://drs.example.org/271828"

    user_context = user_context_fixture()
    file_sources = configured_file_sources(FILE_SOURCES_CONF)
    file_source_pair = file_sources.get_file_source_path(test_url)
    metadata_out: RealizedSourceMetadata = {}
    with tempfile.NamedTemporaryFile(mode="r") as temp:
        file_source_pair.file_source.realize_to(
            file_source_pair.path, temp.name, user_context=user_context, metadata_out=metadata_out
        )
    assert metadata_out == {}


@pytest.mark.parametrize(
    "raw_name, expected",
    [
        ("sample.bed", "sample.bed"),
        ("L1000_LINCS_DCIC_PBIOA020_HCC515_24H_G06.tsv.gz", "L1000_LINCS_DCIC_PBIOA020_HCC515_24H_G06.tsv.gz"),
        ("  padded.txt  ", "padded.txt"),
        ("ünïcödé.txt", "ünïcödé.txt"),
        ("../../../etc/passwd", "passwd"),
        ("/etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("sample\t\r\n.bed", "sample.bed"),
        ("sample\x00.bed", "sample.bed"),
        # Bidi overrides and zero-width characters are how a name gets to look like
        # something it isn't, so they go too.
        ("‮gnp.exe", "gnp.exe"),
        ("file​name.txt", "filename.txt"),
        ("a" * 300, "a" * 255),
        ("", None),
        ("   ", None),
        (".", None),
        ("..", None),
        ("some/dir/", None),
        ("\x00\x01", None),
        (None, None),
        (12345, None),
        (["sample.bed"], None),
        ({"value": "sample.bed"}, None),
    ],
)
def test_sanitize_drs_name(raw_name, expected):
    assert sanitize_drs_name(raw_name) == expected


@pytest.mark.parametrize(
    "raw_name",
    [
        "a" * 300,
        "\U0001f600" * 255,  # 255 characters, 1020 UTF-8 bytes
        "ü" * 200,
        "🎉ünïcödé" * 40,
    ],
)
def test_sanitize_drs_name_caps_encoded_bytes(raw_name):
    """The cap has to be on bytes -- POSIX NAME_MAX is a byte limit, so 255 emoji would overrun it."""
    sanitized = sanitize_drs_name(raw_name)
    assert sanitized
    assert len(sanitized.encode("utf-8")) <= 255
    # Truncation keeps a prefix and never leaves a mangled partial character behind.
    assert raw_name.startswith(sanitized)
    assert sanitized.encode("utf-8").decode("utf-8") == sanitized


@responses.activate
def test_file_source_drs_s3():
    def drs_repo_handler(request):
        assert request.headers["Authorization"] == "Bearer IBearTokens"
        data = {
            "id": "314160",
            "name": "hello-314160",
            "access_methods": [
                {
                    "type": "s3",
                    "access_id": "1234",
                    "region": "us-east-1",
                }
            ],
        }
        return (200, {}, json.dumps(data))

    def access_handler(request):
        assert request.headers["Authorization"] == "Bearer IBearTokens"
        access_data = {
            "url": "s3://ga4gh-demo-data/phenopackets/Cao-2018-TGFBR2-Patient_4.json",
        }
        return (200, {}, json.dumps(access_data))

    responses.add_callback(
        responses.GET,
        "https://drs.example.org/ga4gh/drs/v1/objects/314160",
        callback=drs_repo_handler,
        content_type="application/json",
    )

    responses.add_callback(
        responses.GET,
        "https://drs.example.org/ga4gh/drs/v1/objects/314160/access/1234",
        callback=access_handler,
        content_type="application/json",
    )

    test_url = "drs://drs.example.org/314160"
    file_sources = configured_file_sources(FILE_SOURCES_CONF)
    user_context = user_context_fixture(file_sources=file_sources)
    file_source_pair = file_sources.get_file_source_path(test_url)

    assert file_source_pair.path == test_url
    assert file_source_pair.file_source.id == "test1"

    assert_realizes_contains(
        file_sources, test_url, "PMID:30101859-Cao-2018-TGFBR2-Patient_4", user_context=user_context
    )


@responses.activate
def test_file_source_drs_attach_oidc_token():
    """When http_headers is configured with a template referencing the user's OIDC token, it is sent as a Bearer header."""
    oidc_token = "MyOIDCAccessToken"

    def drs_repo_handler(request):
        assert request.headers["Authorization"] == f"Bearer {oidc_token}"
        data = {
            "id": "999",
            "name": "oidc-test-file",
            "access_methods": [
                {
                    "type": "https",
                    "access_id": "abc",
                }
            ],
        }
        return (200, {}, json.dumps(data))

    def access_handler(request):
        assert request.headers["Authorization"] == f"Bearer {oidc_token}"
        access_data = {
            "url": "https://my.repository.org/oidcfile.txt",
            "headers": [],
        }
        return (200, {}, json.dumps(access_data))

    responses.add_callback(
        responses.GET,
        "https://drs.oidc-example.org/ga4gh/drs/v1/objects/999",
        callback=drs_repo_handler,
        content_type="application/json",
    )
    responses.add_callback(
        responses.GET,
        "https://drs.oidc-example.org/ga4gh/drs/v1/objects/999/access/abc",
        callback=access_handler,
        content_type="application/json",
    )

    test_url = "drs://drs.oidc-example.org/999"

    responses.add(responses.GET, "https://my.repository.org/oidcfile.txt", body="hello oidc world")

    user_context = DictFileSourcesUserContext(
        username="alice",
        email="alice@galaxyproject.org",
        preferences={},
        role_names=set(),
        group_names=set(),
        is_admin=False,
        oidc_access_tokens={"oidc": oidc_token},
    )
    file_sources = configured_file_sources(DRS_OIDC_FILE_SOURCES_CONF)
    assert_realizes_as(file_sources, test_url, "hello oidc world", user_context=user_context)


@responses.activate
def test_drs_202_retry_keeps_headers(signed_url_dns):
    """A 202 on the object request is retried with the configured headers still attached."""
    object_url = "https://drs.example.org/ga4gh/drs/v1/objects/202202"
    data = {"id": "202202", "access_methods": [{"type": "https", "access_url": {"url": SIGNED_URL}}]}
    responses.add(responses.GET, object_url, status=202, headers={"Retry-After": "0"})
    responses.add(responses.GET, object_url, json=data)
    responses.add(responses.GET, SIGNED_URL, body="hello drs world")

    file_sources = configured_file_sources(FILE_SOURCES_CONF)
    assert_realizes_as(
        file_sources, "drs://drs.example.org/202202", "hello drs world", user_context=user_context_fixture()
    )

    object_calls = [call for call in responses.calls if call.request.url == object_url]
    assert len(object_calls) == 2
    for call in object_calls:
        assert call.request.headers["Authorization"] == "Bearer IBearTokens"


PASSPORT = "aaa.bbb.ccc"
RAS_DRS_URI = "drs://drs.ras-example.org/obj1"
RAS_OBJECT_URL = "https://drs.ras-example.org/ga4gh/drs/v1/objects/obj1"
RAS_ACCESS_URL = f"{RAS_OBJECT_URL}/access/gs"
STOCK_DRS_URI = "drs://drs.stock-example.org/obj2"
STOCK_OBJECT_URL = "https://drs.stock-example.org/ga4gh/drs/v1/objects/obj2"
STOCK_ACCESS_URL = f"{STOCK_OBJECT_URL}/access/gs"
SIGNED_URL = "https://signed.example.org/obj.txt?signature=xyz"
RAS_OBJECT = {"id": "obj1", "access_methods": [{"type": "https", "access_id": "gs"}]}
PASSPORT_BODY = {"passports": [PASSPORT]}


@pytest.fixture
def signed_url_dns(monkeypatch):
    """Resolve the signed download host to a public address without asking real DNS."""
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwds):
        if host == "signed.example.org":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
        return real_getaddrinfo(host, port, *args, **kwds)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def _passport_expiry(hours: int = 1) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)


def _ras_user_context(
    passport: str | None = PASSPORT, expires_at: datetime | None = None
) -> DictFileSourcesUserContext:
    return DictFileSourcesUserContext(
        username="alice",
        email="alice@galaxyproject.org",
        preferences={},
        role_names=set(),
        group_names=set(),
        is_admin=False,
        ras_passport=passport,
        ras_passport_expires_at=(expires_at or _passport_expiry()) if passport else None,
    )


def _add_signed_download():
    responses.add(responses.GET, SIGNED_URL, body="hello passport world")


def _json_body(call) -> dict | None:
    body = call.request.body
    return json.loads(body) if body else None


def _assert_passport_not_in(call):
    assert PASSPORT not in str(call.request.headers)
    body = call.request.body or b""
    assert PASSPORT.encode() not in (body if isinstance(body, bytes) else body.encode())


@responses.activate
def test_drs_passport_posted_to_access_only(signed_url_dns):
    responses.add(responses.GET, RAS_OBJECT_URL, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, json={"url": SIGNED_URL})
    _add_signed_download()

    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    assert_realizes_as(file_sources, RAS_DRS_URI, "hello passport world", user_context=_ras_user_context())

    object_call, access_call, download_call = responses.calls
    assert object_call.request.method == "GET"
    assert object_call.request.url == RAS_OBJECT_URL
    _assert_passport_not_in(object_call)
    assert access_call.request.method == "POST"
    assert access_call.request.url == RAS_ACCESS_URL
    assert _json_body(access_call) == PASSPORT_BODY
    for call in (object_call, access_call):
        assert call.request.headers["X-Galaxy-Test"] == "ras"
    assert download_call.request.url == SIGNED_URL
    _assert_passport_not_in(download_call)


@responses.activate
def test_drs_object_requiring_passport_is_retried_as_post(signed_url_dns):
    responses.add(responses.GET, RAS_OBJECT_URL, status=401)
    responses.add(responses.POST, RAS_OBJECT_URL, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, json={"url": SIGNED_URL})
    _add_signed_download()

    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    assert_realizes_as(file_sources, RAS_DRS_URI, "hello passport world", user_context=_ras_user_context())

    methods = [(call.request.method, call.request.url) for call in responses.calls]
    assert methods == [
        ("GET", RAS_OBJECT_URL),
        ("POST", RAS_OBJECT_URL),
        ("POST", RAS_ACCESS_URL),
        ("GET", SIGNED_URL),
    ]
    assert _json_body(responses.calls[1]) == PASSPORT_BODY


@responses.activate
def test_drs_without_ras_passport_still_gets(signed_url_dns):
    responses.add(
        responses.GET, STOCK_OBJECT_URL, json={"id": "obj2", "access_methods": [{"type": "https", "access_id": "gs"}]}
    )
    responses.add(responses.GET, STOCK_ACCESS_URL, json={"url": SIGNED_URL})
    _add_signed_download()

    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    assert file_sources.get_file_source_path(STOCK_DRS_URI).file_source.id == "test_stock"
    assert_realizes_as(file_sources, STOCK_DRS_URI, "hello passport world", user_context=_ras_user_context())

    assert [call.request.method for call in responses.calls] == ["GET", "GET", "GET"]
    for call in responses.calls:
        _assert_passport_not_in(call)


@responses.activate
def test_drs_ras_passport_missing_raises_before_any_request():
    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    with pytest.raises(RasPassportRequiredException):
        assert_realizes_as(file_sources, RAS_DRS_URI, "", user_context=_ras_user_context(passport=None))
    with pytest.raises(RasPassportRequiredException):
        assert_realizes_as(file_sources, RAS_DRS_URI, "", user_context=None)

    # A user without a passport serializes without one, so the job refuses too.
    recovered = serialize_and_recover(file_sources, user_context=_ras_user_context(passport=None))
    with pytest.raises(RasPassportRequiredException):
        assert_realizes_as(recovered, RAS_DRS_URI, "", user_context=None)
    assert len(responses.calls) == 0


@pytest.mark.parametrize("status", [401, 403])
@responses.activate
def test_drs_passport_rejected_is_reported(status):
    responses.add(responses.GET, RAS_OBJECT_URL, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, status=status, body=f"bad passport {PASSPORT}")

    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    with pytest.raises(AuthenticationFailed) as excinfo:
        assert_realizes_as(file_sources, RAS_DRS_URI, "", user_context=_ras_user_context())
    assert str(excinfo.value) == f"The DRS server did not accept the NIH RAS passport (HTTP {status})."
    assert PASSPORT not in repr(excinfo.value)


@responses.activate
def test_drs_202_retry_keeps_method_headers_and_passport(signed_url_dns):
    responses.add(responses.GET, RAS_OBJECT_URL, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, status=202, headers={"Retry-After": "0"})
    responses.add(responses.POST, RAS_ACCESS_URL, json={"url": SIGNED_URL})
    _add_signed_download()

    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    assert_realizes_as(file_sources, RAS_DRS_URI, "hello passport world", user_context=_ras_user_context())

    first, second = (call for call in responses.calls if call.request.url == RAS_ACCESS_URL)
    assert second.request.method == "POST"
    assert second.request.headers["X-Galaxy-Test"] == first.request.headers["X-Galaxy-Test"] == "ras"
    assert second.request.headers["Content-Type"] == first.request.headers["Content-Type"]
    assert _json_body(second) == _json_body(first) == PASSPORT_BODY


def test_drs_serialization_carries_passport_and_expiry():
    expires_at = _passport_expiry()
    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    as_dict = file_sources.to_dict(for_serialization=True, user_context=_ras_user_context(expires_at=expires_at))
    by_id = {source["id"]: source for source in as_dict["file_sources"]}
    assert by_id["test_ras"]["passports"] == [PASSPORT]
    assert by_id["test_ras"]["auth_expires_at"] == expires_at.isoformat()
    assert "passports" not in by_id["test_stock"]
    assert "auth_expires_at" not in by_id["test_stock"]
    # The listing an API client sees never carries it.
    assert PASSPORT not in str(file_sources.to_dict(user_context=_ras_user_context()))


@responses.activate
def test_drs_serialized_source_posts_passport_without_user(signed_url_dns):
    responses.add(responses.GET, RAS_OBJECT_URL, status=403)
    responses.add(responses.POST, RAS_OBJECT_URL, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, json={"url": SIGNED_URL})
    _add_signed_download()

    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    recovered = serialize_and_recover(file_sources, user_context=_ras_user_context())
    assert_realizes_as(recovered, RAS_DRS_URI, "hello passport world", user_context=None)

    posts = [call for call in responses.calls if call.request.method == "POST"]
    assert [call.request.url for call in posts] == [RAS_OBJECT_URL, RAS_ACCESS_URL]
    for call in posts:
        assert _json_body(call) == PASSPORT_BODY
        assert call.request.headers["X-Galaxy-Test"] == "ras"


@responses.activate
def test_drs_expired_passport_fails_fast():
    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    recovered = serialize_and_recover(file_sources, user_context=_ras_user_context(expires_at=_passport_expiry(-1)))
    with pytest.raises(FileSourceCredentialExpired):
        assert_realizes_as(recovered, RAS_DRS_URI, "", user_context=None)
    assert len(responses.calls) == 0


REDIRECT_REFUSED_MESSAGE = (
    "The DRS server tried to redirect a request carrying the NIH RAS passport, and Galaxy refused to follow it."
)


def _assert_redirect_refused(target_url: str):
    file_sources = configured_file_sources(DRS_RAS_PASSPORT_FILE_SOURCES_CONF)
    with pytest.raises(UpstreamProxyError) as excinfo:
        assert_realizes_as(file_sources, RAS_DRS_URI, "", user_context=_ras_user_context())
    assert str(excinfo.value) == REDIRECT_REFUSED_MESSAGE
    assert PASSPORT not in repr(excinfo.value)
    assert not [call for call in responses.calls if call.request.url == target_url]
    for call in responses.calls:
        if call.request.url != RAS_ACCESS_URL and call.request.url != RAS_OBJECT_URL:
            _assert_passport_not_in(call)


@pytest.mark.parametrize(
    "target_url",
    [
        "https://elsewhere.example.org/steal",
        "http://drs.ras-example.org/ga4gh/drs/v1/objects/obj1/access/gs",
    ],
)
@responses.activate
def test_drs_access_post_redirect_is_refused(target_url, signed_url_dns):
    responses.add(responses.GET, RAS_OBJECT_URL, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, status=307, headers={"Location": target_url})
    responses.add(responses.POST, target_url, json={"url": SIGNED_URL})
    _add_signed_download()

    _assert_redirect_refused(target_url)


@responses.activate
def test_drs_object_post_redirect_is_refused(signed_url_dns):
    target_url = "https://elsewhere.example.org/steal"
    responses.add(responses.GET, RAS_OBJECT_URL, status=401)
    responses.add(responses.POST, RAS_OBJECT_URL, status=307, headers={"Location": target_url})
    responses.add(responses.POST, target_url, json=RAS_OBJECT)
    responses.add(responses.POST, RAS_ACCESS_URL, json={"url": SIGNED_URL})
    _add_signed_download()

    _assert_redirect_refused(target_url)
