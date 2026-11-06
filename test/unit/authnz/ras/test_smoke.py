import io
import json
import time
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import pytest

from galaxy.authnz.ras.smoke import (
    main,
    parse_callback,
    SmokeError,
)
from .util import (
    CLIENT_ID,
    CountingFetcher,
    FakeHttp,
    id_token,
    ISSUER,
    jwks_doc,
    KEY,
    KID,
    make_passport,
    NONCE,
    permission,
    SUBJECT,
)


def test_parse_callback_url():
    url = "https://cfderas.develop.tapis.io/v3/oauth2/extensions/oa2/callback?code=abc&state=xyz&extra=1"
    assert parse_callback(url) == {"code": "abc", "state": "xyz"}


def test_parse_callback_bare_code():
    assert parse_callback("  abc123\n") == {"code": "abc123"}


def test_parse_callback_error():
    assert parse_callback("https://cb.example/?error=access_denied&state=s") == {
        "error": "access_denied",
        "state": "s",
    }


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://cb.example/?state=only",
        # A pasted URL must carry the state; only a bare code skips the check.
        "https://cb.example/?code=abc",
        "https://cb.example/?code=abc&state=",
        "https://cb.example/?code=abc&state=s1&state=s2",
    ],
)
def test_parse_callback_rejects(value):
    with pytest.raises(SmokeError):
        parse_callback(value)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("RAS_CLIENT_SECRET", "test-secret")
    return tmp_path / "state.json"


def _common(state_file):
    return ["--issuer", ISSUER, "--state-file", str(state_file)]


def _live_http(permissions):
    now = int(time.time())
    passport = make_passport(permissions, iat=now - 60)
    token = id_token(iat=now - 10, exp=now + 3600)
    return FakeHttp(
        token_response={"access_token": "access-1", "id_token": token},
        userinfo={"sub": SUBJECT, "passport_jwt_v11": passport},
    )


def _run(argv, http):
    out = io.StringIO()
    code = main(argv, http=http, fetcher=CountingFetcher(jwks_doc((KEY, KID))), out=out)
    return code, out.getvalue()


def test_url_saves_state(env):
    code, output = _run(_common(env) + ["url"], FakeHttp())
    assert code == 0
    saved = json.loads(env.read_text())
    query = parse_qs(urlsplit(output.splitlines()[0]).query)
    assert query["state"] == [saved["state"]]
    assert query["nonce"] == [saved["nonce"]]
    assert query["client_id"] == [CLIENT_ID]
    assert oct(env.stat().st_mode & 0o777) == "0o600"


def test_url_needs_client_id(env, monkeypatch, capsys):
    monkeypatch.delenv("RAS_CLIENT_ID")
    code, _ = _run(_common(env) + ["url"], FakeHttp())
    assert code == 1
    assert "RAS_CLIENT_ID" in capsys.readouterr().err


def test_url_reads_direct_ras_from_ras_config(env, monkeypatch, tmp_path):
    monkeypatch.delenv("RAS_CLIENT_ID")
    config = tmp_path / "ras_config.yml"
    config.write_text(
        f"issuer: {ISSUER}\n"
        "direct_ras:\n"
        "  client_id: from-config\n"
        "  redirect_uri: https://configured.example/cb\n"
        "  scopes: openid ga4gh_passport_v1\n"
    )
    code, output = _run(["--ras-config", str(config), "--state-file", str(env), "url"], FakeHttp())
    assert code == 0
    url = urlsplit(output.splitlines()[0])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}" == ISSUER
    assert query["client_id"] == ["from-config"]
    assert query["redirect_uri"] == ["https://configured.example/cb"]
    assert query["scope"] == ["openid ga4gh_passport_v1"]


def test_missing_ras_config(env, capsys, tmp_path):
    code, _ = _run(["--ras-config", str(tmp_path / "nope.yml"), "--state-file", str(env), "url"], FakeHttp())
    assert code == 1
    assert "nope.yml" in capsys.readouterr().err


def _save_state(state_file, state="state-1"):
    state_file.write_text(json.dumps({"state": state, "nonce": NONCE}))


def test_exchange_no_permissions(env):
    _save_state(env)
    callback = "https://cb.example/callback?code=code-1&state=state-1"
    code, output = _run(_common(env) + ["exchange", callback, "--phs", "phs000123"], _live_http([]))
    assert code == 0
    assert f"subject:   {SUBJECT}" in output
    assert "visas:     1" in output
    assert "dbGaP permissions: 0" in output
    assert "grants:    nothing" in output
    assert "gate:      denied (phs000123): no_access" in output
    assert "access-1" not in output
    assert not env.exists()


def test_exchange_with_permission(env):
    _save_state(env)
    callback = "https://cb.example/callback?code=code-1&state=state-1"
    http = _live_http([permission("phs000123", expiration=int(time.time()) + 86400)])
    code, output = _run(_common(env) + ["exchange", callback, "--phs", "phs000123"], http)
    assert code == 0
    assert "grants:    phs000123" in output
    assert "gate:      allowed (phs000123)" in output


def test_exchange_state_mismatch(env, capsys):
    _save_state(env, state="expected")
    callback = "https://cb.example/callback?code=code-1&state=forged"
    http = _live_http([])
    code, _ = _run(_common(env) + ["exchange", callback], http)
    assert code == 1
    assert "State" in capsys.readouterr().err
    assert not [call for call in http.calls if call[0] == "POST"]


def test_exchange_without_url_step(env, capsys):
    code, _ = _run(_common(env) + ["exchange", "code-1"], _live_http([]))
    assert code == 1
    assert "run 'url' first" in capsys.readouterr().err


def test_exchange_needs_secret(env, monkeypatch, capsys):
    monkeypatch.delenv("RAS_CLIENT_SECRET")
    _save_state(env)
    code, _ = _run(_common(env) + ["exchange", "https://cb.example/?code=c&state=state-1"], _live_http([]))
    assert code == 1
    assert "RAS_CLIENT_SECRET" in capsys.readouterr().err
