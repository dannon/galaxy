"""No admin override for controlled data."""

from types import SimpleNamespace

import pytest

from galaxy import exceptions
from galaxy.model import (
    Dataset,
    HistoryDatasetAssociation,
    Role,
)
from galaxy.model.security import GalaxyRBACAgent


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
        hda.to_library_dataset_dataset_association(SimpleNamespace(user=make_user()), target_folder=None)
