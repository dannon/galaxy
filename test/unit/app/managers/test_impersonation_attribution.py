"""Requests made in a session created by impersonation must name the admin as the actor."""

from types import SimpleNamespace
from typing import (
    Any,
    cast,
)

import pytest
from starlette_context import request_cycle_context

from galaxy import model
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
    """Just enough of a transaction to run the real _ensure_valid_session under remote-user auth."""

    def get_cookie(self, name="galaxysession"):
        return self.cookie

    def get_or_create_default_history(self):
        return None


def remote_user_request(trans, galaxy_session, remote_user_email):
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
    GalaxyWebTransaction._ensure_valid_session(cast(Any, stand_in), "galaxysession")
    return stand_in.galaxy_session


def test_remote_user_admin_in_another_users_session_is_recorded(trans, admin, target):
    admin_id, target_id = admin.id, target.id
    target_session = create_new_session(cast(Any, trans), user_for_new_session=target)
    trans.sa_session.add(target_session)
    trans.sa_session.commit()
    kept = remote_user_request(trans, target_session, ADMIN_EMAIL)
    # The proxy says the admin; the cookie is the target's: the session is kept and marked.
    assert kept.id == target_session.id
    later = later_request_session(trans, kept)
    identity = session_identity(later, "remote_user")
    assert (identity.auth_method, identity.user_id, identity.actor_id, identity.switch) == (
        "remote_user",
        target_id,
        admin_id,
        "impersonation",
    )


def test_remote_user_matching_the_session_is_not_marked(trans, target):
    target_session = create_new_session(cast(Any, trans), user_for_new_session=target)
    trans.sa_session.add(target_session)
    trans.sa_session.commit()
    kept = remote_user_request(trans, target_session, TARGET_EMAIL.upper())
    assert kept.id == target_session.id
    assert kept.impersonated_by_user_id is None


def test_impersonation_marker_survives_a_fresh_model_load(trans, admin, target):
    admin_id = admin.id
    trans.app[UserManager].impersonate(trans, target)
    session_id = trans.galaxy_session.id
    trans.sa_session.expunge_all()
    assert trans.sa_session.get(model.GalaxySession, session_id).impersonated_by_user_id == admin_id
