import json
import os
from pathlib import Path
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import jwt
import pytest

from galaxy.authnz.ras.jwks import JwksClient
from galaxy.authnz.ras.sources import (
    BridgeAppPassportSource,
    DirectRasPassportSource,
    PassportSourceError,
)
from .util import (
    CLIENT_ID,
    CLIENT_SECRET,
    CountingFetcher,
    DISCOVERY,
    FakeHttp,
    FIXTURES_ENV,
    id_token,
    ISSUER,
    jwks_doc,
    KEY,
    KID,
    make_passport,
    NONCE,
    NOW,
    OTHER_KEY,
    REDIRECT_URI,
    SUBJECT,
)


def _source(http=None):
    http = http or FakeHttp()
    jwks = JwksClient(DISCOVERY["jwks_uri"], fetcher=CountingFetcher(jwks_doc((KEY, KID))))
    source = DirectRasPassportSource(
        ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, http=http, jwks=jwks, clock=lambda: NOW
    )
    return http, source


def _handoff(**overrides):
    handoff = {"code": "code-1", "state": "state-1", "expected_state": "state-1", "nonce": NONCE}
    handoff.update(overrides)
    return handoff


def test_authorization_url():
    _, source = _source()
    request = source.new_authorization_request()
    url = urlsplit(request.url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == DISCOVERY["authorization_endpoint"]
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query == {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email ga4gh_passport_v1",
        "state": request.state,
        "nonce": request.nonce,
        "prompt": "login",
    }
    assert "%20" in url.query  # spaces as %20, not "+"
    assert request.state != source.new_authorization_request().state


def test_happy_path():
    http, source = _source()
    passport = source.obtain(None, _handoff())
    assert passport == http.userinfo["passport_jwt_v11"]
    post = next(call for call in http.calls if call[0] == "POST")
    assert post[1] == DISCOVERY["token_endpoint"]
    data, basic_auth = post[2]
    assert data == {"grant_type": "authorization_code", "code": "code-1", "redirect_uri": REDIRECT_URI}
    assert basic_auth == (CLIENT_ID, CLIENT_SECRET)
    # The passport comes from v1.1 userinfo, called with the access token.
    assert ("GET", f"{ISSUER}/openid/connect/v1.1/userinfo", "access-1") in http.calls


def test_discovery_issuer_mismatch():
    class BadDiscovery(FakeHttp):
        def get_json(self, url, bearer=None):
            return 200, dict(DISCOVERY, issuer="https://evil.example")

    _, source = _source(BadDiscovery())
    with pytest.raises(PassportSourceError, match="issuer"):
        source.new_authorization_request()


def test_token_endpoint_error():
    http = FakeHttp(token_status=400, token_response={"error": "invalid_grant", "error_description": "used code"})
    _, source = _source(http)
    with pytest.raises(PassportSourceError, match="HTTP 400: invalid_grant used code"):
        source.obtain(None, _handoff())


def test_token_response_missing_tokens():
    _, source = _source(FakeHttp(token_response={"access_token": "a"}))
    with pytest.raises(PassportSourceError, match="missing"):
        source.obtain(None, _handoff())


def test_missing_passport_in_userinfo():
    _, source = _source(FakeHttp(userinfo={"sub": SUBJECT, "email": "x@example.org"}))
    with pytest.raises(PassportSourceError, match="passport_jwt_v11"):
        source.obtain(None, _handoff())


def test_userinfo_error():
    _, source = _source(FakeHttp(userinfo_status=401, userinfo={"error": "invalid_token"}))
    with pytest.raises(PassportSourceError, match="HTTP 401"):
        source.obtain(None, _handoff())


def test_userinfo_subject_mismatch():
    _, source = _source(FakeHttp(userinfo={"sub": "someone-else", "passport_jwt_v11": make_passport()}))
    with pytest.raises(PassportSourceError, match="subject"):
        source.obtain(None, _handoff())


def test_passport_subject_mismatch():
    _, source = _source(FakeHttp(userinfo={"sub": SUBJECT, "passport_jwt_v11": make_passport(sub="someone-else")}))
    with pytest.raises(PassportSourceError, match="Passport subject"):
        source.obtain(None, _handoff())


def test_id_token_nonce_mismatch():
    _, source = _source(FakeHttp(token_response={"access_token": "a", "id_token": id_token(nonce="other")}))
    with pytest.raises(PassportSourceError, match="nonce"):
        source.obtain(None, _handoff())


def test_id_token_missing_nonce():
    _, source = _source(FakeHttp(token_response={"access_token": "a", "id_token": id_token(nonce=None)}))
    with pytest.raises(PassportSourceError, match="nonce"):
        source.obtain(None, _handoff())


def test_id_token_audience_mismatch():
    _, source = _source(FakeHttp(token_response={"access_token": "a", "id_token": id_token(aud="other-client")}))
    with pytest.raises(PassportSourceError, match="ID token failed verification"):
        source.obtain(None, _handoff())


def test_id_token_multiple_audiences_needs_azp():
    token = id_token(aud=[CLIENT_ID, "other-client"], azp="other-client")
    _, source = _source(FakeHttp(token_response={"access_token": "a", "id_token": token}))
    with pytest.raises(PassportSourceError, match="azp"):
        source.obtain(None, _handoff())


def test_id_token_bad_signature():
    _, source = _source(FakeHttp(token_response={"access_token": "a", "id_token": id_token(_key=OTHER_KEY)}))
    with pytest.raises(PassportSourceError, match="ID token"):
        source.obtain(None, _handoff())


def test_id_token_expired():
    token = id_token(iat=NOW - 7200, exp=NOW - 3600)
    _, source = _source(FakeHttp(token_response={"access_token": "a", "id_token": token}))
    with pytest.raises(PassportSourceError, match="expired"):
        source.obtain(None, _handoff())


def test_state_mismatch():
    http, source = _source()
    with pytest.raises(PassportSourceError, match="State"):
        source.obtain(None, _handoff(state="forged"))
    assert not [call for call in http.calls if call[0] == "POST"]


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"code": None}, "No authorization code"),
        ({"expected_state": None}, "saved state"),
        ({"nonce": None}, "saved state"),
        ({"error": "access_denied"}, "access_denied"),
    ],
)
def test_bad_handoff(overrides, match):
    _, source = _source()
    with pytest.raises(PassportSourceError, match=match):
        source.obtain(None, _handoff(**overrides))


def test_bridge_source_is_a_stub():
    with pytest.raises(NotImplementedError):
        BridgeAppPassportSource("https://bridge.example").obtain(None, {})


@pytest.mark.skipif(not os.environ.get(FIXTURES_ENV), reason=f"set {FIXTURES_ENV} to the RAS fixtures directory")
def test_real_ras_staging_id_token():
    fixtures = Path(os.environ[FIXTURES_ENV])
    jwks = json.loads((fixtures / "stsstg-jwks.json").read_text())
    raw = json.loads((fixtures / "token-response-baker1.json").read_text())["id_token"]
    unverified = jwt.decode(raw, options={"verify_signature": False})
    source = DirectRasPassportSource(
        "https://stsstg.nih.gov",
        unverified["aud"],
        "unused",
        REDIRECT_URI,
        http=FakeHttp(),
        jwks=JwksClient("https://stsstg.nih.gov/openid/connect/jwks.json", fetcher=lambda url: jwks),
    )
    claims = source.verify_id_token(raw, unverified["nonce"], now=unverified["iat"])
    assert claims["acr"].endswith("/assurance/iap/high")
    with pytest.raises(PassportSourceError, match="nonce"):
        source.verify_id_token(raw, "wrong-nonce", now=unverified["iat"])
