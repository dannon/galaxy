"""Scoped, expiring API tokens and the credential a request resolves to.

A credential is what authenticated the current request: a browser session, the
legacy API key, an OIDC bearer token, or one of the scoped tokens defined here.
Sessions and legacy keys are unscoped and behave exactly as before. A scoped
token carries a set of ``resource:action`` scopes checked at the route (and MCP
op) layer, plus optional constraints checked where objects are loaded, so that
"only the histories this token created" holds on every route that reaches a
history rather than only the ones somebody remembered to annotate.
"""

import hashlib
import logging
import secrets
import string
from collections.abc import (
    Iterable,
    Mapping,
)
from contextvars import (
    ContextVar,
    Token,
)
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
)
from typing import (
    Any,
    TYPE_CHECKING,
)

from sqlalchemy import (
    or_,
    select,
)
from sqlalchemy.orm import (
    scoped_session,
    Session,
)
from sqlalchemy.sql.expression import ColumnElement

from galaxy import (
    exceptions,
    model,
)
from galaxy.model.orm.now import now
from galaxy.structured_app import BasicSharedApp

if TYPE_CHECKING:
    from galaxy.tools import Tool

log = logging.getLogger(__name__)

TOKEN_PREFIX = "gxy_"
TOKEN_SECRET_LENGTH = 40
TOKEN_HINT_LENGTH = 8
_ALPHABET = string.ascii_letters + string.digits

# resource -> the actions it understands. ``write`` implies ``read`` on the same resource.
RESOURCES: dict[str, frozenset[str]] = {
    "histories": frozenset({"read", "write", "share", "purge"}),
    "datasets": frozenset({"read", "write", "purge"}),
    "jobs": frozenset({"read", "write"}),
    "tools": frozenset({"read", "run"}),
    "workflows": frozenset({"read", "write", "run", "share"}),
    "invocations": frozenset({"read", "write"}),
    "pages": frozenset({"read", "write", "share"}),
    "libraries": frozenset({"read", "write"}),
    "account": frozenset({"read", "write"}),
    "admin": frozenset({"*"}),
}
ALL_ACTIONS: frozenset[str] = frozenset().union(*RESOURCES.values()) | {"*"}
SURFACES: frozenset[str] = frozenset({"api", "mcp"})
CONSTRAINT_KEYS: frozenset[str] = frozenset({"histories", "tools", "workflows", "surfaces"})
TOKEN_SOURCES: frozenset[str] = frozenset({"ui", "device", "oauth", "job"})
# Tools every upload path runs; a token that may write datasets may run them without listing them.
UPLOAD_TOOL_IDS: frozenset[str] = frozenset({"__DATA_FETCH__", "upload1"})
# Marks an operation any credential may call, scoped or not (server info and the like).
PUBLIC_SCOPE = "public"

PRESETS: dict[str, str] = {
    "read-only": "*:read",
    "agent": "*:read histories:write datasets:write jobs:write tools:run workflows:run invocations:write pages:write",
    "run-jobs": "account:read histories:read histories:write datasets:read datasets:write tools:read tools:run jobs:read",
    "everything": "*:*",
}


def parse_scopes(scopes: str | Iterable[str]) -> frozenset[str]:
    """Split and validate a scope string (or iterable) into a set of ``resource:action`` scopes."""
    if isinstance(scopes, str):
        scopes = scopes.split()
    parsed = set()
    for scope in scopes:
        resource, sep, action = scope.partition(":")
        if not sep or not resource or not action:
            raise exceptions.RequestParameterInvalidException(f"Invalid scope '{scope}': expected resource:action.")
        if resource != "*" and resource not in RESOURCES:
            raise exceptions.RequestParameterInvalidException(f"Invalid scope '{scope}': unknown resource.")
        allowed = ALL_ACTIONS if resource == "*" else RESOURCES[resource] | {"*"}
        if action not in allowed:
            raise exceptions.RequestParameterInvalidException(f"Invalid scope '{scope}': unknown action.")
        parsed.add(scope)
    if not parsed:
        raise exceptions.RequestParameterInvalidException("At least one scope is required.")
    return frozenset(parsed)


def scopes_allow(scopes: Iterable[str], required: str) -> bool:
    """True if ``required`` (``resource:action``) is granted by ``scopes``.

    ``*`` matches any resource or action, ``write`` implies ``read``, and no wildcard ever
    reaches ``admin``: that resource must be named explicitly.
    """
    resource, _, action = required.partition(":")
    for scope in scopes:
        granted_resource, _, granted_action = scope.partition(":")
        if granted_resource != resource and not (granted_resource == "*" and resource != "admin"):
            continue
        if granted_action in ("*", action) or (action == "read" and granted_action == "write"):
            return True
    return False


@dataclass(frozen=True)
class Credential:
    """What authenticated the current request.

    ``scopes`` is ``None`` for unscoped credentials (sessions, the legacy key, bootstrap admin,
    OIDC ``api:*``), which behave exactly as before this existed.
    """

    kind: str
    scopes: frozenset[str] | None = None
    constraints: Mapping[str, Any] | None = None
    token_id: int | None = None

    @property
    def scoped(self) -> bool:
        return self.scopes is not None

    @property
    def surfaces(self) -> frozenset[str] | None:
        surfaces = (self.constraints or {}).get("surfaces")
        return frozenset(surfaces) if surfaces else None

    def allows(self, scope: str) -> bool:
        return self.scopes is None or scopes_allow(self.scopes, scope)

    def constraint(self, resource: str) -> Mapping[str, Any] | None:
        return (self.constraints or {}).get(resource)


UNSCOPED_SESSION = Credential(kind="session")
UNSCOPED_LEGACY_KEY = Credential(kind="legacy_key")
UNSCOPED_BOOTSTRAP = Credential(kind="bootstrap")
UNSCOPED_OIDC = Credential(kind="oidc")

# The credential behind the request in progress. Managers take a user rather than a
# transaction, and this is what lets constraint checks reach the credential without
# threading a new argument through every call site. ``None`` means unscoped (no request,
# a job handler, a Celery task), which is also what every pre-existing caller gets.
CURRENT_CREDENTIAL: ContextVar[Credential | None] = ContextVar("galaxy_current_credential", default=None)


def current_credential() -> Credential | None:
    return CURRENT_CREDENTIAL.get()


def set_current_credential(credential: Credential | None) -> Token[Credential | None]:
    return CURRENT_CREDENTIAL.set(credential)


def reset_current_credential(token: Token[Credential | None]) -> None:
    CURRENT_CREDENTIAL.reset(token)


def require_scope(credential: Credential | None, scope: str | None, what: str = "This route") -> None:
    """Refuse a scoped credential that doesn't carry ``scope``; unscoped credentials always pass.

    ``scope=None`` means the caller was never annotated, and scoped credentials are refused
    there outright: the allowlist is only as good as what it names.
    """
    if credential is None or not credential.scoped or scope == PUBLIC_SCOPE:
        return
    if scope is None:
        raise exceptions.InsufficientPermissionsException(
            f"{what} is not available to scoped access tokens. Use a session or an unscoped API key."
        )
    if not credential.allows(scope):
        raise exceptions.InsufficientPermissionsException(
            f"{what} requires the '{scope}' scope, which this access token lacks."
        )


def credential_permits_history(history: model.History | None, credential: Credential | None = None) -> bool:
    """Apply the ``histories`` constraint, if the active credential has one."""
    credential = credential if credential is not None else current_credential()
    if credential is None:
        return True
    constraint = credential.constraint("histories")
    if not constraint:
        return True
    created_by_token = bool(constraint.get("created_by_token"))
    ids = constraint.get("ids") or []
    if not created_by_token and not ids:
        return True
    if history is None:
        return False
    if created_by_token and credential.token_id is not None and history.api_token_id == credential.token_id:
        return True
    return history.id in ids


def credential_permits_workflow(workflow: model.StoredWorkflow | None, credential: Credential | None = None) -> bool:
    """Apply the ``workflows`` constraint, if the active credential has one."""
    credential = credential if credential is not None else current_credential()
    if credential is None:
        return True
    ids = (credential.constraint("workflows") or {}).get("ids") or []
    if not ids:
        return True
    return workflow is not None and workflow.id in ids


def credential_permits_item(item: Any, credential: Credential | None = None) -> bool:
    """Dispatch to the constraint check for ``item``'s type; items with no constraint always pass."""
    if isinstance(item, model.History):
        return credential_permits_history(item, credential)
    if isinstance(item, model.StoredWorkflow):
        return credential_permits_workflow(item, credential)
    return True


def history_constraint_clause(credential: Credential | None = None) -> ColumnElement[bool] | None:
    """A SQL clause selecting the histories the credential's constraint allows, or None if unconstrained.

    Listing routes add it so a constrained token never sees a history it couldn't open.
    """
    credential = credential if credential is not None else current_credential()
    if credential is None:
        return None
    constraint = credential.constraint("histories")
    if not constraint:
        return None
    clauses: list[ColumnElement[bool]] = []
    if constraint.get("created_by_token") and credential.token_id is not None:
        clauses.append(model.History.api_token_id == credential.token_id)
    if ids := constraint.get("ids"):
        clauses.append(model.History.id.in_(ids))
    if not clauses:
        return None
    return or_(*clauses)


def credential_permits_tool(tool: "Tool", credential: Credential | None = None) -> bool:
    """Apply the ``tools`` constraint, if the active credential has one. Upload tools always pass."""
    credential = credential if credential is not None else current_credential()
    if credential is None:
        return True
    constraint = credential.constraint("tools")
    ids = (constraint or {}).get("ids") or []
    if not ids:
        return True
    tool_id = tool.id or ""
    candidates = {tool_id, getattr(tool, "old_id", None) or ""}
    if "/" in tool_id:
        # Tool shed ids carry their version as the last path segment; accept the unversioned form too.
        candidates.add(tool_id.rsplit("/", 1)[0])
    return bool(candidates & UPLOAD_TOOL_IDS) or bool(candidates & set(ids))


def validate_constraints(constraints: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Check the shape of a constraints mapping at mint time and drop empty entries."""
    if not constraints:
        return None
    unknown = set(constraints) - CONSTRAINT_KEYS
    if unknown:
        raise exceptions.RequestParameterInvalidException(f"Unknown constraint(s): {', '.join(sorted(unknown))}.")
    cleaned: dict[str, Any] = {}
    histories = constraints.get("histories") or {}
    if histories:
        unknown = set(histories) - {"ids", "created_by_token"}
        if unknown:
            raise exceptions.RequestParameterInvalidException(
                f"Unknown histories constraint(s): {', '.join(sorted(unknown))}."
            )
        entry: dict[str, Any] = {}
        if histories.get("created_by_token"):
            entry["created_by_token"] = True
        if ids := histories.get("ids"):
            if not all(isinstance(i, int) for i in ids):
                raise exceptions.RequestParameterInvalidException("histories.ids must be a list of history ids.")
            entry["ids"] = sorted(set(ids))
        if entry:
            cleaned["histories"] = entry
    for resource in ("tools", "workflows"):
        entry_in = constraints.get(resource) or {}
        if not entry_in:
            continue
        if set(entry_in) - {"ids"}:
            raise exceptions.RequestParameterInvalidException(f"{resource} constraints only accept 'ids'.")
        ids = entry_in.get("ids") or []
        if resource == "tools" and not all(isinstance(i, str) and i for i in ids):
            raise exceptions.RequestParameterInvalidException("tools.ids must be a list of tool ids.")
        if resource == "workflows" and not all(isinstance(i, int) for i in ids):
            raise exceptions.RequestParameterInvalidException("workflows.ids must be a list of workflow ids.")
        if ids:
            cleaned[resource] = {"ids": sorted(set(ids))}
    if surfaces := constraints.get("surfaces"):
        unknown = set(surfaces) - SURFACES
        if unknown:
            raise exceptions.RequestParameterInvalidException(f"Unknown surface(s): {', '.join(sorted(unknown))}.")
        cleaned["surfaces"] = sorted(set(surfaces))
    return cleaned or None


def hash_token_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def is_token_secret(value: str | None) -> bool:
    return value is not None and value.startswith(TOKEN_PREFIX)


class ApiTokenManager:
    """Mint, resolve, list and revoke scoped API tokens."""

    # Don't write last_used_at on every request, just often enough to be useful.
    LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)

    def __init__(self, app: BasicSharedApp):
        self.app = app

    @property
    def session(self) -> "scoped_session[Session]":
        return self.app.model.session

    @property
    def max_lifetime(self) -> timedelta | None:
        """The longest a token may live, or ``None`` when the deployment allows no expiry."""
        days = getattr(self.app.config, "api_token_max_lifetime", 90)
        return timedelta(days=days) if days and days > 0 else None

    def mint_token(
        self,
        user: model.User,
        name: str,
        scopes: str | Iterable[str],
        constraints: Mapping[str, Any] | None = None,
        expires_in: int | None = None,
        source: str = "ui",
    ) -> tuple[model.APIToken, str]:
        """Create a token for ``user`` and return the row with the one-time secret.

        ``expires_in`` is seconds; it is capped by ``api_token_max_lifetime`` and defaults to that
        cap. Only a deployment with the cap set to 0 can mint a token that never expires. This is
        the seam other acquisition flows (the device flow, OAuth, per-job tokens) call into.
        """
        if user is None or getattr(user, "bootstrap_admin_user", False):
            raise exceptions.RealUserRequiredException("Only real users can create access tokens.")
        name = (name or "").strip()
        if not name:
            raise exceptions.RequestParameterInvalidException("A token name is required.")
        if source not in TOKEN_SOURCES:
            raise exceptions.RequestParameterInvalidException(f"Unknown token source '{source}'.")
        scope_set = parse_scopes(scopes)
        if scopes_allow(scope_set, "admin:*") and not self.app.config.is_admin_user(user):
            raise exceptions.RequestParameterInvalidException("Only administrators can mint admin-scoped tokens.")
        expires_at = self._expiry(expires_in)
        secret = TOKEN_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(TOKEN_SECRET_LENGTH))
        token = model.APIToken(
            user=user,
            name=name,
            scopes=" ".join(sorted(scope_set)),
            constraints=validate_constraints(constraints),
            expires_at=expires_at,
            source=source,
            token_hash=hash_token_secret(secret),
            token_hint=secret[len(TOKEN_PREFIX) : len(TOKEN_PREFIX) + TOKEN_HINT_LENGTH],
        )
        self.session.add(token)
        self.session.commit()
        return token, secret

    def _expiry(self, expires_in: int | None) -> datetime | None:
        cap = self.max_lifetime
        if expires_in is not None:
            if expires_in <= 0:
                raise exceptions.RequestParameterInvalidException("expires_in must be a positive number of seconds.")
            lifetime = timedelta(seconds=expires_in)
            if cap is not None and lifetime > cap:
                lifetime = cap
            return now() + lifetime
        return now() + cap if cap is not None else None

    def by_secret(self, secret: str) -> model.APIToken | None:
        stmt = select(model.APIToken).filter_by(token_hash=hash_token_secret(secret))
        return self.session.scalars(stmt).one_or_none()

    def resolve(self, secret: str, surface: str = "api") -> tuple[model.User, Credential]:
        """Turn a presented ``gxy_`` secret into its user and credential, or raise saying why not."""
        token = self.by_secret(secret)
        if token is None:
            raise exceptions.AuthenticationFailed("Provided access token is not valid.")
        if token.revoked:
            raise exceptions.AuthenticationFailed("Provided access token has been revoked.")
        if token.expired:
            raise exceptions.AuthenticationFailed("Provided access token has expired.")
        user = token.user
        if user.deleted:
            raise exceptions.AuthenticationFailed("User account is deactivated, please contact an administrator.")
        credential = self.credential_for(token)
        if credential.surfaces is not None and surface not in credential.surfaces:
            allowed = ", ".join(sorted(credential.surfaces))
            raise exceptions.InsufficientPermissionsException(
                f"This access token is restricted to the {allowed} surface and cannot be used here."
            )
        self.touch(token)
        return user, credential

    def credential_for(self, token: model.APIToken) -> Credential:
        return Credential(
            kind="token",
            scopes=parse_scopes(token.scopes),
            constraints=token.constraints,
            token_id=token.id,
        )

    def touch(self, token: model.APIToken) -> None:
        stamp = now()
        if token.last_used_at is None or stamp - token.last_used_at >= self.LAST_USED_WRITE_INTERVAL:
            token.last_used_at = stamp
            self.session.commit()

    def tokens_for(self, user: model.User, include_inactive: bool = False) -> list[model.APIToken]:
        tokens = list(user.api_tokens)
        if include_inactive:
            return tokens
        return [t for t in tokens if t.active]

    def get_owned(self, user: model.User, token_id: int) -> model.APIToken:
        token: model.APIToken | None = self.session.get(model.APIToken, token_id)
        if token is None or token.user_id != user.id:
            raise exceptions.ObjectNotFound("Access token not found.")
        return token

    def revoke(self, user: model.User, token_id: int) -> model.APIToken:
        token = self.get_owned(user, token_id)
        if not token.revoked:
            token.revoked_at = now()
            self.session.commit()
        return token
