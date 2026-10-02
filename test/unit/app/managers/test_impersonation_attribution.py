"""Requests made in a session created by impersonation must name the admin as the actor."""

from types import SimpleNamespace
from typing import (
    Any,
    cast,
)

import pytest
from starlette_context import request_cycle_context

from galaxy import (
    app as galaxy_app,
    model,
)
from galaxy.app_unittest_utils import galaxy_mock
from galaxy.managers.session import GalaxySessionManager
from galaxy.managers.users import UserManager
from galaxy.web.framework.request_scope import (
    current_request_identity,
    request_scope,
    RequestIdentity,
    session_identity,
)
from galaxy.webapps.base.webapp import (
    create_new_session,
    GalaxyWebTransaction,
)
from galaxy.webapps.galaxy.api import get_user

ADMIN_EMAIL = "admin@example.org"
TARGET_EMAIL = "target@example.org"
PASSWORD = "not-logged-123"


class SessionSwitchingTrans(galaxy_mock.MockTrans):
    """MockTrans whose logout/login create chained GalaxySession rows the way GalaxyWebTransaction does."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.request.remote_addr = "192.0.2.10"
        self.request.remote_host = "192.0.2.10"

    def _switch_session(self, user=None, impersonated_by_user_id=None):
        prev = self.galaxy_session
        assert prev is not None
        prev.is_valid = False
        self.galaxy_session = create_new_session(cast(Any, self), prev, user)
        self.galaxy_session.impersonated_by_user_id = impersonated_by_user_id
        self.sa_session.add_all((prev, self.galaxy_session))
        self.sa_session.commit()

    def handle_user_logout(self, logout_all=False):
        self._switch_session()

    def handle_user_login(self, user, impersonated_by_user_id=None):
        self._switch_session(user, impersonated_by_user_id)


@pytest.fixture
def trans():
    trans = SessionSwitchingTrans(admin_users=ADMIN_EMAIL, admin_users_list=[ADMIN_EMAIL])
    trans.app.config.allow_user_impersonation = True
    admin = trans.app[UserManager].create(email=ADMIN_EMAIL, username="admin", password=PASSWORD)
    trans.galaxy_session = create_new_session(cast(Any, trans), user_for_new_session=admin)
    trans.sa_session.add(trans.galaxy_session)
    trans.sa_session.commit()
    return trans


@pytest.fixture
def admin(trans):
    return trans.galaxy_session.user


@pytest.fixture
def target(trans):
    return trans.app[UserManager].create(email=TARGET_EMAIL, username="target", password=PASSWORD)


def later_request_session(trans, galaxy_session):
    """Load a session the way a later request would: by its key, from a clean session."""
    session_key = galaxy_session.session_key
    trans.sa_session.expunge_all()
    loaded = GalaxySessionManager(trans.app.model).get_session_from_session_key(session_key)
    assert loaded is not None
    return loaded


def test_impersonation_marks_the_new_session_with_the_admin(trans, admin, target):
    admin_id, admin_session_id = admin.id, trans.galaxy_session.id
    trans.app[UserManager].impersonate(trans, target)
    impersonated = trans.galaxy_session
    assert impersonated.id != admin_session_id
    assert impersonated.user_id == target.id
    assert impersonated.impersonated_by_user_id == admin_id


def test_later_requests_in_the_impersonated_session_name_the_admin(trans, admin, target):
    admin_id, target_id = admin.id, target.id
    trans.app[UserManager].impersonate(trans, target)
    later = later_request_session(trans, trans.galaxy_session)
    assert session_identity(later) == RequestIdentity(
        "session", target_id, actor_id=admin_id, switch="impersonation", credential_id=later.id
    )


def test_later_fastapi_request_records_the_admin_as_actor(trans, admin, target):
    admin_id, target_id = admin.id, target.id
    trans.app[UserManager].impersonate(trans, target)
    later = later_request_session(trans, trans.galaxy_session)
    with request_scope(), request_cycle_context():
        selected = get_user(galaxy_session=later, api_user=None)
        assert selected is not None and selected.id == target_id
        identity = current_request_identity()
    assert identity is not None
    assert (identity.user_id, identity.actor_id, identity.switch) == (target_id, admin_id, "impersonation")


def test_later_legacy_request_records_the_admin_as_actor(trans, admin, target):
    admin_id, target_id = admin.id, target.id
    trans.app[UserManager].impersonate(trans, target)
    later = later_request_session(trans, trans.galaxy_session)
    stand_in = SimpleNamespace(galaxy_session=later, app=trans.app)
    stand_in._note_identity = lambda identity: GalaxyWebTransaction._note_identity(cast(Any, stand_in), identity)
    with request_scope():
        GalaxyWebTransaction._note_session_identity(cast(Any, stand_in))
        identity = current_request_identity()
    assert identity == RequestIdentity(
        "session", target_id, actor_id=admin_id, switch="impersonation", credential_id=later.id
    )


def test_logging_out_of_an_impersonated_session_ends_the_attribution(trans, admin, target):
    trans.app[UserManager].impersonate(trans, target)
    trans.handle_user_logout()
    assert trans.galaxy_session.impersonated_by_user_id is None
    assert session_identity(later_request_session(trans, trans.galaxy_session)).auth_method == "anonymous"


def test_an_ordinary_login_is_not_marked(trans, admin, target):
    target_id = target.id
    trans.handle_user_logout()
    trans.handle_user_login(target)
    later = later_request_session(trans, trans.galaxy_session)
    identity = session_identity(later)
    assert identity.switch is None
    assert identity.actor_id == identity.user_id == target_id


class RemoteUserTrans(SimpleNamespace):
    """Just enough of a transaction to run the real session and identity code under remote-user auth."""

    _note_identity = GalaxyWebTransaction._note_identity

    def get_cookie(self, name="galaxysession"):
        return self.cookie

    def get_or_create_default_history(self):
        return None


def remote_user_request(trans, galaxy_session, remote_user_email):
    """One legacy request: the proxy names remote_user_email, the browser sends galaxy_session's cookie."""
    trans.app.config.use_remote_user = True
    trans.app.config.remote_user_header = "HTTP_REMOTE_USER"
    stand_in = RemoteUserTrans(
        app=trans.app,
        cookie=trans.security.encode_guid(galaxy_session.session_key),
        security=trans.security,
        session_manager=GalaxySessionManager(trans.app.model),
        user_manager=trans.app[UserManager],
        environ={"HTTP_REMOTE_USER": remote_user_email},
        sa_session=trans.sa_session,
        webapp=SimpleNamespace(name="galaxy"),
        galaxy_session=None,
    )
    with request_scope():
        GalaxyWebTransaction._ensure_valid_session(cast(Any, stand_in), "galaxysession")
        GalaxyWebTransaction._note_session_identity(cast(Any, stand_in))
        identity = current_request_identity()
    return stand_in.galaxy_session, identity


@pytest.fixture
def target_session(trans, target):
    galaxy_session = create_new_session(cast(Any, trans), user_for_new_session=target)
    trans.sa_session.add(galaxy_session)
    trans.sa_session.commit()
    return galaxy_session


def test_remote_user_admin_in_another_users_session_is_the_actor(trans, admin, target, target_session):
    admin_id, target_id = admin.id, target.id
    kept, identity = remote_user_request(trans, target_session, ADMIN_EMAIL)
    # The proxy says the admin; the cookie is the target's: the session is kept.
    assert kept.id == target_session.id
    assert identity == RequestIdentity(
        "remote_user", target_id, actor_id=admin_id, switch="impersonation", credential_id=kept.id
    )


def test_remote_user_impersonation_is_not_stored_on_the_session(trans, admin, target, target_session):
    target_id = target.id
    kept, _ = remote_user_request(trans, target_session, ADMIN_EMAIL)
    assert later_request_session(trans, kept).impersonated_by_user_id is None
    # The real user resumes the same session later: they act as themselves.
    resumed, identity = remote_user_request(trans, kept, TARGET_EMAIL)
    assert resumed.id == kept.id
    assert identity == RequestIdentity("remote_user", target_id, actor_id=target_id, credential_id=kept.id)


def test_remote_user_matching_the_session_case_insensitively_is_not_a_switch(trans, target, target_session):
    target_id = target.id
    kept, identity = remote_user_request(trans, target_session, TARGET_EMAIL.upper())
    assert kept.id == target_session.id
    assert identity is not None
    assert (identity.actor_id, identity.switch) == (target_id, None)


@pytest.mark.parametrize("remote_user_email", [ADMIN_EMAIL, TARGET_EMAIL])
def test_fastapi_remote_user_identity_is_derived_per_request(
    trans, admin, target, target_session, monkeypatch, remote_user_email
):
    expected_actor = admin.id if remote_user_email == ADMIN_EMAIL else target.id
    config = SimpleNamespace(use_remote_user=True, remote_user_header="HTTP_REMOTE_USER")
    monkeypatch.setattr(
        galaxy_app, "app", SimpleNamespace(config=config, user_manager=trans.app[UserManager]), raising=False
    )
    later = later_request_session(trans, target_session)
    headers = [(b"remote-user", remote_user_email.encode())]
    with request_scope(headers=headers), request_cycle_context():
        get_user(galaxy_session=later, api_user=None)
        identity = current_request_identity()
    assert identity is not None
    assert (identity.auth_method, identity.actor_id) == ("remote_user", expected_actor)
    assert identity.switch == ("impersonation" if remote_user_email == ADMIN_EMAIL else None)


def real_web_trans(mock_trans):
    """A GalaxyWebTransaction running its real login/logout code over the mock app's database."""
    web_trans: Any = object.__new__(GalaxyWebTransaction)
    web_trans._app = mock_trans.app
    web_trans.galaxy_session = mock_trans.galaxy_session
    web_trans._GalaxyWebTransaction__user = None
    web_trans.environ = {}
    # The tool shed branch of handle_user_login skips history association, which needs a full app.
    web_trans.webapp = SimpleNamespace(name="tool_shed")
    web_trans.user_checks = lambda user: None
    web_trans._GalaxyWebTransaction__create_new_session = lambda prev=None, user=None: create_new_session(
        cast(Any, mock_trans), prev, user
    )
    web_trans._GalaxyWebTransaction__update_session_cookie = lambda name="galaxysession": None
    return web_trans


def test_real_login_code_marks_only_the_impersonated_session(trans, admin, target):
    admin_id = admin.id
    other = trans.app[UserManager].create(email="other@example.org", username="other", password=PASSWORD)
    other_id = other.id
    web_trans = real_web_trans(trans)
    trans.app[UserManager].impersonate(web_trans, target)
    impersonated = web_trans.galaxy_session
    assert later_request_session(trans, impersonated).impersonated_by_user_id == admin_id
    # Logging out, then a new login on the same browser, leaves no marker behind.
    web_trans.handle_user_logout()
    assert later_request_session(trans, web_trans.galaxy_session).impersonated_by_user_id is None
    web_trans.handle_user_login(trans.sa_session.get(model.User, other_id))
    identity = session_identity(later_request_session(trans, web_trans.galaxy_session))
    assert (identity.user_id, identity.actor_id, identity.switch) == (other_id, other_id, None)


def test_impersonation_marker_survives_a_fresh_model_load(trans, admin, target):
    admin_id = admin.id
    trans.app[UserManager].impersonate(trans, target)
    session_id = trans.galaxy_session.id
    trans.sa_session.expunge_all()
    assert trans.sa_session.get(model.GalaxySession, session_id).impersonated_by_user_id == admin_id
