"""Exercise the RAS passport code against a real RAS server, without a Galaxy server.

RAS only redirects to a client's registered callbacks, which are usually not
an address this script can listen on, so this runs in two steps::

    RAS_CLIENT_SECRET=... python scripts/ras_passport_smoke.py --ras-config ras_config.yml url
    # open the printed URL, log in, copy the URL the browser ends up on
    RAS_CLIENT_SECRET=... python scripts/ras_passport_smoke.py --ras-config ras_config.yml \\
        exchange '<redirected URL>' --phs phs000123

``--ras-config`` takes the issuer and the ``direct_ras`` client settings
(including ``redirect_uri``) from Galaxy's RAS config. Without it, set
``RAS_CLIENT_ID`` and pass ``--redirect-uri``; command-line flags always win.

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

from galaxy.authnz.ras.config import (
    DEFAULT_ISSUER,
    DirectRasSettings,
    load_ras_config,
    RasConfig,
    RasConfigError,
)
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
    parse_callback,
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

DEFAULT_STATE_FILE = Path(tempfile.gettempdir()) / "ras_passport_smoke_state.json"


class SmokeError(Exception):
    pass


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SmokeError(f"Set ${name}")
    return value


def _when(timestamp: int | None) -> str:
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _settings(args: argparse.Namespace) -> tuple[str, DirectRasSettings]:
    """The issuer and client settings: flags, then ``--ras-config``, then the defaults."""
    config = None
    if args.ras_config:
        config = load_ras_config(args.ras_config)
        if config is None:
            raise SmokeError(f"No RAS config at {args.ras_config}")
    direct = config.direct_ras if config else None
    issuer = args.issuer or (config.issuer if config else DEFAULT_ISSUER)
    client_id = direct.client_id if direct else _env("RAS_CLIENT_ID")
    redirect_uri = args.redirect_uri or (direct.redirect_uri if direct else None)
    if not redirect_uri:
        raise SmokeError("No redirect URI: pass --redirect-uri or a --ras-config with direct_ras.redirect_uri")
    settings = DirectRasSettings(
        client_id=client_id,
        client_secret=direct.client_secret if direct else None,
        client_secret_env=direct.client_secret_env if direct else "RAS_CLIENT_SECRET",
        redirect_uri=redirect_uri,
        scopes=args.scopes or (direct.scopes if direct else DEFAULT_SCOPES),
    )
    return issuer, settings


def _source(
    args: argparse.Namespace, http: HttpClient | None, fetcher: JsonFetcher, need_secret: bool
) -> DirectRasPassportSource:
    issuer, settings = _settings(args)
    secret = settings.resolve_client_secret() if need_secret else "unused"
    source = DirectRasPassportSource(
        issuer, settings.client_id, secret, settings.redirect_uri, scopes=settings.scopes, http=http
    )
    source.jwks = JwksClient(lambda: source.discovery()["jwks_uri"], fetcher=fetcher)
    return source


def _write_private(path: Path, text: str) -> None:
    # The default path is predictable in a possibly shared temp dir: never follow a
    # planted symlink, never write into someone else's file, and never leave the
    # state readable even briefly.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as e:
        raise SmokeError(f"Could not write state to {path}: {e}") from e
    with os.fdopen(fd, "w") as f:
        if hasattr(os, "getuid") and os.fstat(fd).st_uid != os.getuid():
            raise SmokeError(f"{path} belongs to another user")
        os.fchmod(fd, 0o600)
        f.truncate()
        f.write(text)


def cmd_url(args: argparse.Namespace, http: HttpClient | None, fetcher: JsonFetcher, out: TextIO) -> int:
    request = _source(args, http, fetcher, need_secret=False).new_authorization_request()
    state_file = Path(args.state_file)
    _write_private(state_file, json.dumps({"state": request.state, "nonce": request.nonce}))
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

    source = _source(args, http, fetcher, need_secret=True)
    # Codes are single use; don't let a retry reuse this state.
    state_file.unlink()
    raw_passport = source.obtain(None, handoff)

    issuer_config = IssuerConfig(issuer=source.issuer, jwks=source.jwks)
    store = VaultPassportStore(InMemoryVault(), issuer_config)
    user = SimpleNamespace(id=0)
    passport = store.put(user, raw_passport)
    print("passport verified and stored", file=out)
    summarize(passport, out)

    gate = PassportGate(RasConfig(issuer=source.issuer), store)
    decision = gate.require(user, phs_id=args.phs)
    target = args.phs or "any passport"
    if isinstance(decision, PassportOk):
        print(f"gate:      allowed ({target})", file=out)
    else:
        print(f"gate:      denied ({target}): {decision.reason.value} -- {decision.message}", file=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ras-config", help="read the issuer and direct_ras settings from this ras_config.yml")
    parser.add_argument("--issuer", help=f"default {DEFAULT_ISSUER}")
    parser.add_argument(
        "--redirect-uri", help="a redirect URI registered for the client; default direct_ras.redirect_uri"
    )
    parser.add_argument("--scopes", help=f"default {DEFAULT_SCOPES!r}")
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
    except (SmokeError, RasConfigError, PassportSourceError, PassportVerificationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
