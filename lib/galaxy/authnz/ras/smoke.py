"""Exercise the RAS passport code against a real RAS server, without a Galaxy server.

RAS only redirects to a client's registered callbacks, which for the CWIC client
are Tapis URLs Galaxy can't receive, so this runs in two steps::

    RAS_CLIENT_ID=... python scripts/ras_passport_smoke.py url
    # open the printed URL, log in, copy the URL the browser ends up on
    RAS_CLIENT_ID=... RAS_CLIENT_SECRET=... \\
        python scripts/ras_passport_smoke.py exchange '<redirected URL>' --phs phs000123

``exchange`` redeems the code, verifies the ID token, pulls the passport from
v1.1 userinfo, stores it in a throwaway in-memory vault through
``VaultPassportStore`` and prints a summary plus the ``PassportGate`` decision.
Tokens are never printed.
"""

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO
from urllib.parse import (
    parse_qs,
    urlsplit,
)

from galaxy.authnz.ras.config import RasConfig
from galaxy.authnz.ras.gate import (
    PassportGate,
    PassportOk,
)
from galaxy.authnz.ras.jwks import (
    JsonFetcher,
    JwksClient,
    requests_json_fetcher,
)
from galaxy.authnz.ras.models import Passport
from galaxy.authnz.ras.policy import permissions_for
from galaxy.authnz.ras.sources import (
    DEFAULT_SCOPES,
    DirectRasPassportSource,
    HttpClient,
    PassportSourceError,
)
from galaxy.authnz.ras.store import (
    InMemoryVault,
    VaultPassportStore,
)
from galaxy.authnz.ras.verify import (
    IssuerConfig,
    PassportVerificationError,
)

DEFAULT_ISSUER = "https://stsstg.nih.gov"
# The CWIC client's develop-tenant callback: least likely to have a live Tapis
# session that redeems the code before we do.
DEFAULT_REDIRECT_URI = "https://cfderas.develop.tapis.io/v3/oauth2/extensions/oa2/callback"
DEFAULT_STATE_FILE = Path(tempfile.gettempdir()) / "ras_passport_smoke_state.json"


class SmokeError(Exception):
    pass


def parse_callback(value: str) -> dict[str, str]:
    """Pull ``code``/``state``/``error`` out of a pasted redirect URL, or take a bare code."""
    value = value.strip()
    if not value:
        raise SmokeError("Nothing pasted")
    if "://" not in value:
        return {"code": value}
    url = urlsplit(value)
    params = parse_qs(url.query) or parse_qs(url.fragment)
    result = {
        key: values[0] for key, values in params.items() if key in ("code", "state", "error", "error_description")
    }
    if not result.get("code") and not result.get("error"):
        raise SmokeError("No code or error in the pasted URL")
    return result


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SmokeError(f"Set ${name}")
    return value


def _when(timestamp: int | None) -> str:
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _source(
    args: argparse.Namespace, http: HttpClient | None, fetcher: JsonFetcher, secret: str
) -> DirectRasPassportSource:
    source = DirectRasPassportSource(
        args.issuer, _env("RAS_CLIENT_ID"), secret, args.redirect_uri, scopes=args.scopes, http=http
    )
    source.jwks = JwksClient(lambda: source.discovery()["jwks_uri"], fetcher=fetcher)
    return source


def cmd_url(args: argparse.Namespace, http: HttpClient | None, fetcher: JsonFetcher, out: TextIO) -> int:
    request = _source(args, http, fetcher, secret="unused").new_authorization_request()
    state_file = Path(args.state_file)
    state_file.write_text(json.dumps({"state": request.state, "nonce": request.nonce}))
    state_file.chmod(0o600)
    print(request.url, file=out)
    print(
        f"\nstate saved to {state_file}; open the URL, log in, then run 'exchange' with the URL you land on.", file=out
    )
    return 0


def summarize(passport: Passport, out: TextIO) -> None:
    print(f"subject:   {passport.subject}", file=out)
    print(f"issuer:    {passport.issuer}", file=out)
    print(f"issued:    {_when(passport.issued_at)}", file=out)
    print(f"expires:   {_when(passport.expires_at)}", file=out)
    print(f"visas:     {len(passport.visas)}", file=out)
    for visa in passport.visas:
        print(f"  - {visa.visa_type} from {visa.source} by {visa.by}, expires {_when(visa.expires_at)}", file=out)
        print(f"    dbGaP permissions: {len(visa.dbgap_permissions)}", file=out)
        for permission in visa.dbgap_permissions:
            print(
                f"      {permission.phs_id} {permission.version or ''} {permission.consent_group or ''} "
                f"{permission.consent_name or ''} role={permission.role} until {_when(permission.expiration)}",
                file=out,
            )
    for dropped in passport.dropped_visas:
        print(f"  - dropped visa {dropped.jti}: {dropped.reason}", file=out)
    granted = permissions_for(passport)
    print(f"grants:    {', '.join(sorted({p.accession.phs_id for p in granted})) or 'nothing'}", file=out)


def cmd_exchange(args: argparse.Namespace, http: HttpClient | None, fetcher: JsonFetcher, out: TextIO) -> int:
    callback = parse_callback(args.callback)
    state_file = Path(args.state_file)
    if not state_file.exists():
        raise SmokeError(f"No saved state at {state_file}; run 'url' first")
    saved = json.loads(state_file.read_text())
    if "state" not in callback:
        print("WARNING: bare code pasted, skipping the state check", file=out)
        callback["state"] = saved["state"]
    handoff = dict(callback, expected_state=saved["state"], nonce=saved["nonce"])

    source = _source(args, http, fetcher, secret=_env("RAS_CLIENT_SECRET"))
    # Codes are single use; don't let a retry reuse this state.
    state_file.unlink()
    raw_passport = source.obtain(None, handoff)

    issuer_config = IssuerConfig(issuer=source.issuer, jwks=source.jwks)
    store = VaultPassportStore(InMemoryVault(), issuer_config)
    user = SimpleNamespace(id=0)
    passport = store.put(user, raw_passport)
    print("passport verified and stored", file=out)
    summarize(passport, out)

    gate = PassportGate(RasConfig(issuer=source.issuer), store, galaxy_url="http://localhost:8080")
    decision = gate.require(user, phs_id=args.phs)
    target = args.phs or "any passport"
    if isinstance(decision, PassportOk):
        print(f"gate:      allowed ({target})", file=out)
    else:
        print(f"gate:      denied ({target}): {decision.reason.value} -- {decision.message}", file=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issuer", default=DEFAULT_ISSUER)
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    parser.add_argument("--scopes", default=DEFAULT_SCOPES)
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("url", help="print the RAS authorize URL and save state/nonce")
    exchange = sub.add_parser("exchange", help="redeem the pasted callback URL and check the passport")
    exchange.add_argument("callback", help="the full URL RAS redirected to (or just the code)")
    exchange.add_argument("--phs", help="dbGaP study to ask the gate about, e.g. phs000123")
    return parser


def main(
    argv: Sequence[str] | None = None,
    http: HttpClient | None = None,
    fetcher: JsonFetcher = requests_json_fetcher,
    out: TextIO = sys.stdout,
) -> int:
    args = build_parser().parse_args(argv)
    command = cmd_url if args.command == "url" else cmd_exchange
    try:
        return command(args, http, fetcher, out)
    except (SmokeError, PassportSourceError, PassportVerificationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
