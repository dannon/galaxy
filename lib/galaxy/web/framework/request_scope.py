"""What Galaxy knows about the HTTP request being served: its id, client and identity.

One mutable :class:`RequestScope` per request lives in a ContextVar. The ASGI
middleware opens it; the FastAPI auth dependencies and the legacy WSGI transaction
fill in :class:`RequestIdentity`; the access log and audit events read it. Keeping a
single record means the two can never disagree about who made a request.

The scope is mutable on purpose: FastAPI runs sync dependencies in worker threads
with a *copy* of the current contextvars, and a2wsgi does the same for the mounted
WSGI app, so neither can rebind the ContextVar for the rest of the request -- but
both can fill in the object every copy shares.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import (
    Any,
    Literal,
)

from sqlalchemy import inspect as sa_inspect

AuthMethod = Literal["session", "remote_user", "api_key", "bootstrap_api_key", "bearer", "anonymous"]
IdentitySwitch = Literal["run_as", "impersonation"]

# User agents are client-chosen and unbounded; the head of the string is the useful part.
MAX_USER_AGENT_LENGTH = 512


@dataclass(frozen=True)
class RequestIdentity:
    """Who authenticated a request and who it acts as.

    ``actor_id`` is the principal the credential belongs to. It differs from
    ``user_id`` only when ``switch`` says how: an admin's ``run_as``, or a session
    created by impersonation. Ids are numeric database ids; the bootstrap admin key
    has none.
    """

    auth_method: AuthMethod
    user_id: int | None = None
    actor_id: int | None = None
    switch: IdentitySwitch | None = None
    # galaxy_session.id for session authentication, the api_keys row for an API key.
    credential_id: int | None = None

    def access_log_fields(self) -> str:
        fields = [f"auth={self.auth_method}"]
        if self.user_id is not None:
            fields.append(f"user={self.user_id}")
        elif self.switch is not None:
            # run_as named a user that doesn't exist; the request carried on without one.
            fields.append("user=anonymous")
        if self.switch is not None and self.actor_id is not None:
            fields.append(f"real_user={self.actor_id}")
        return " ".join(fields)


ANONYMOUS = RequestIdentity("anonymous")


@dataclass
class RequestScope:
    request_id: str | None = None
    remote_addr: str | None = None
    user_agent: str | None = None
    identity: RequestIdentity | None = None


REQUEST_SCOPE: ContextVar[RequestScope | None] = ContextVar("galaxy_request_scope", default=None)


def current_request_scope() -> RequestScope | None:
    return REQUEST_SCOPE.get()


@contextmanager
def request_scope(
    request_id: str | None = None, remote_addr: str | None = None, user_agent: str | None = None
) -> Iterator[RequestScope]:
    if user_agent is not None:
        user_agent = user_agent[:MAX_USER_AGENT_LENGTH]
    scope = RequestScope(request_id=request_id, remote_addr=remote_addr, user_agent=user_agent)
    token = REQUEST_SCOPE.set(scope)
    try:
        yield scope
    finally:
        REQUEST_SCOPE.reset(token)


def current_request_identity() -> RequestIdentity | None:
    scope = REQUEST_SCOPE.get()
    return scope.identity if scope is not None else None


def set_request_identity(identity: RequestIdentity) -> None:
    scope = REQUEST_SCOPE.get()
    if scope is None:
        return
    current = scope.identity
    # A request can resolve its user more than once, and with an anonymous session
    # get_user and get_required_user disagree; never replace a user with nobody.
    if current is None or current.user_id is None or identity.user_id is not None:
        scope.identity = identity


def model_id(instance: Any) -> int | None:
    """The primary key of a mapped instance, read without loading anything.

    Identity is often recorded after a commit has expired the instance, so the id
    comes from the identity key rather than ``instance.id``.
    """
    if instance is None:
        return None
    state = sa_inspect(instance, raiseerr=False)
    if state is None or not state.identity:
        return None
    return int(state.identity[0])


def session_identity(galaxy_session: Any, auth_method: AuthMethod = "session") -> RequestIdentity:
    """Identity for a request authenticated by a Galaxy session cookie."""
    session_id = model_id(galaxy_session)
    user_id = model_id(galaxy_session.user)
    if user_id is None:
        return RequestIdentity("anonymous", credential_id=session_id)
    if impersonated_by := galaxy_session.impersonated_by_user_id:
        return RequestIdentity(
            auth_method, user_id, actor_id=impersonated_by, switch="impersonation", credential_id=session_id
        )
    return RequestIdentity(auth_method, user_id, actor_id=user_id, credential_id=session_id)


def api_key_id(user: Any) -> int | None:
    """The id of the API key a user just authenticated with, if it is already loaded.

    ``UserManager.by_api_key`` only accepts a user's newest key and loads the
    collection to check that, so its first entry is the key that was used.
    """
    state = sa_inspect(user, raiseerr=False)
    if state is None:
        return None
    keys = state.dict.get("api_keys")
    return model_id(keys[0]) if keys else None


def credential_identity(user: Any, auth_method: Literal["api_key", "bearer"]) -> RequestIdentity:
    """Identity for a sessionless request authenticated by an API key or bearer token."""
    if user is None:
        return ANONYMOUS
    if getattr(user, "bootstrap_admin_user", False):
        return RequestIdentity("bootstrap_api_key")
    user_id = model_id(user)
    credential_id = api_key_id(user) if auth_method == "api_key" else None
    return RequestIdentity(auth_method, user_id, actor_id=user_id, credential_id=credential_id)


def note_run_as(user: Any) -> None:
    """Follow a legacy ``run_as`` switch: the request now acts as ``user`` for its actor."""
    scope = REQUEST_SCOPE.get()
    if scope is None or scope.identity is None or scope.identity.actor_id is None:
        return
    current = scope.identity
    scope.identity = RequestIdentity(
        current.auth_method,
        model_id(user),
        actor_id=current.actor_id,
        switch="run_as",
        credential_id=current.credential_id,
    )


def replace_request_identity(identity: RequestIdentity) -> None:
    scope = REQUEST_SCOPE.get()
    if scope is not None:
        scope.identity = identity
