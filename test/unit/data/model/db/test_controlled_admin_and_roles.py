"""No admin override for controlled data; GA4GH grant roles are assigned by Galaxy only."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from galaxy import exceptions
from galaxy.managers.roles import RoleManager
from galaxy.model import (
    Dataset,
    HistoryDatasetAssociation,
    Role,
    UserRoleAssociation,
)
from galaxy.model.orm.now import now
from galaxy.model.security import GalaxyRBACAgent
from galaxy.schema.schema import RoleUpdatePayload


@pytest.fixture
def agent(session):
    return GalaxyRBACAgent(session)


@pytest.fixture
def grant(make_role):
    return make_role(name="ga4gh_grant:1:phs000001.c1", type=Role.types.GA4GH_GRANT)


@pytest.fixture
def controlled_manage(make_role):
    return make_role(name="ga4gh_controlled_data", type=Role.types.GA4GH_GRANT)


def new_dataset(session, agent, access, manage):
    d = Dataset(external_filename="/data/x")  # external: shareable without an object store
    session.add(d)
    session.commit()
    actions = agent.permitted_actions
    permissions = {actions.DATASET_MANAGE_PERMISSIONS: manage}
    if access:
        permissions[actions.DATASET_ACCESS] = access
    assert not agent.set_all_dataset_permissions(d, permissions, new=True)
    return d


def test_admin_override_only_for_data_that_is_not_controlled(session, agent, make_role, grant, controlled_manage):
    private = make_role(type=Role.types.PRIVATE)
    controlled = new_dataset(session, agent, [grant], [controlled_manage])
    ordinary = new_dataset(session, agent, [private], [private])
    assert controlled.is_controlled and not ordinary.is_controlled
    assert not agent.admin_override_allowed(True, controlled)
    assert agent.admin_override_allowed(True, ordinary)
    assert not agent.admin_override_allowed(False, ordinary)
    assert agent.action_tuples_are_controlled([("access", grant.id), ("manage permissions", controlled_manage.id)])
    assert not agent.action_tuples_are_controlled([("manage permissions", private.id)])


def test_controlled_data_cannot_enter_a_library(session, agent, make_user, grant, controlled_manage):
    controlled = new_dataset(session, agent, [grant], [controlled_manage])
    hda = HistoryDatasetAssociation(dataset=controlled, sa_session=session)
    with pytest.raises(exceptions.ItemAccessibilityException, match="can't be added to a library"):
        hda.to_library_dataset_dataset_association(
            SimpleNamespace(user=make_user()),  # type: ignore[arg-type]
            target_folder=None,
        )


def test_setting_user_roles_keeps_grant_memberships_and_adds_none(session, agent, make_user_and_role, make_role, grant):
    user, _ = make_user_and_role()
    other_grant = make_role(name="ga4gh_grant:1:phs000002.c1", type=Role.types.GA4GH_GRANT)
    ordinary = make_role(type=Role.types.ADMIN)
    until = now() + timedelta(days=3)
    agent.associate_user_role(user, grant, expiration_time=until)
    # As the admin user-roles form would: the current roles plus another grant role.
    agent.set_user_group_and_role_associations(user, role_ids=[ordinary.id, grant.id, other_grant.id])
    memberships = {
        ura.role_id: ura.expiration_time
        for ura in session.scalars(select(UserRoleAssociation).filter_by(user_id=user.id))
    }
    assert memberships[grant.id] == until  # untouched, expiration kept
    assert other_grant.id not in memberships  # not added by hand
    assert memberships[ordinary.id] is None


def test_grant_role_members_cannot_be_set(session, agent, make_user, grant):
    with pytest.raises(exceptions.RequestParameterInvalidException, match="assigned by Galaxy"):
        agent.set_role_user_and_group_associations(grant, user_ids=[make_user().id])


def test_grant_roles_cannot_be_given_to_groups(session, agent, make_group, grant):
    with pytest.raises(exceptions.RequestParameterInvalidException, match="assigned by Galaxy"):
        agent.set_group_user_and_role_associations(make_group(), role_ids=[grant.id])


@pytest.mark.parametrize("operation", ["update", "delete", "purge", "undelete"])
def test_role_manager_refuses_grant_roles(session, grant, operation):
    manager = RoleManager(SimpleNamespace(model=SimpleNamespace(context=session)))  # type: ignore[arg-type]
    trans = SimpleNamespace(sa_session=session)
    grant.deleted = operation in ("purge", "undelete")
    with pytest.raises(exceptions.RequestParameterInvalidException, match="assigned by Galaxy"):
        if operation == "update":
            manager.update_role(trans, grant, RoleUpdatePayload(user_ids=[]))  # type: ignore[arg-type]
        else:
            getattr(manager, operation)(trans, grant)
