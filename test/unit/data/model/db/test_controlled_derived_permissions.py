"""Outputs derived from controlled data (managed by a ga4gh_grant role) stay controlled."""

import pytest

from galaxy.model import (
    Dataset,
    Role,
)
from galaxy.model.security import GalaxyRBACAgent


@pytest.fixture
def agent(session):
    return GalaxyRBACAgent(session)


@pytest.fixture
def roles(session, make_role):
    return {
        "private": make_role(type=Role.types.PRIVATE),
        "grant1": make_role(name="ga4gh_grant:1:phs000001.c1", type=Role.types.GA4GH_GRANT),
        "grant2": make_role(name="ga4gh_grant:1:phs000002.c1", type=Role.types.GA4GH_GRANT),
        "controlled": make_role(name="ga4gh_controlled_data", type=Role.types.GA4GH_GRANT),
    }


def dataset(session, agent, access, manage):
    d = Dataset()
    session.add(d)
    session.commit()
    actions = agent.permitted_actions
    permissions = {actions.DATASET_MANAGE_PERMISSIONS: manage}
    if access:
        permissions[actions.DATASET_ACCESS] = access
    assert not agent.set_all_dataset_permissions(d, permissions, new=True)  # returns an error message on failure
    return d


def names(roles):
    return sorted(role.name for role in roles)


def at_submit(agent, datasets):
    """Like the tool action: union of every input's (action, role id), then guess_derived_permissions."""
    all_permissions: dict[str, set[int]] = {}
    for d in datasets:
        for action, action_roles in agent.get_permissions(d).items():
            all_permissions.setdefault(action.action, set()).update(role.id for role in action_roles)
    derived = agent.guess_derived_permissions(all_permissions)
    return {action: sorted(role_ids) for action, role_ids in derived.items()}


def test_dataset_is_controlled(session, agent, roles):
    assert agent.dataset_is_controlled(dataset(session, agent, [roles["grant1"]], [roles["controlled"]]))
    assert not agent.dataset_is_controlled(dataset(session, agent, [roles["private"]], [roles["private"]]))
    assert not agent.dataset_is_controlled(dataset(session, agent, [], [roles["private"]]))


@pytest.mark.parametrize("path", ["submit", "finish"])
def test_controlled_plus_own_data_stays_controlled(session, agent, roles, path):
    actions = agent.permitted_actions
    controlled = dataset(session, agent, [roles["grant1"]], [roles["controlled"]])
    own = dataset(session, agent, [], [roles["private"]])  # the user's public upload
    if path == "submit":
        derived = at_submit(agent, [controlled, own])
        assert derived[actions.DATASET_MANAGE_PERMISSIONS] == [roles["controlled"].id]
        assert derived[actions.DATASET_ACCESS] == [roles["grant1"].id]
    else:
        derived = agent.guess_derived_permissions_for_datasets([controlled, own])
        # Without the fix the manage roles intersect to nothing and the output gets no permissions at all.
        assert names(derived[actions.DATASET_MANAGE_PERMISSIONS]) == ["ga4gh_controlled_data"]
        assert names(derived[actions.DATASET_ACCESS]) == ["ga4gh_grant:1:phs000001.c1"]


@pytest.mark.parametrize("path", ["submit", "finish"])
def test_two_controlled_inputs_need_both_grants(session, agent, roles, path):
    actions = agent.permitted_actions
    one = dataset(session, agent, [roles["grant1"]], [roles["controlled"]])
    two = dataset(session, agent, [roles["grant2"]], [roles["controlled"]])
    if path == "submit":
        derived = at_submit(agent, [one, two])
        assert derived[actions.DATASET_ACCESS] == sorted([roles["grant1"].id, roles["grant2"].id])
        assert derived[actions.DATASET_MANAGE_PERMISSIONS] == [roles["controlled"].id]
    else:
        derived = agent.guess_derived_permissions_for_datasets([one, two])
        assert names(derived[actions.DATASET_ACCESS]) == ["ga4gh_grant:1:phs000001.c1", "ga4gh_grant:1:phs000002.c1"]
        assert names(derived[actions.DATASET_MANAGE_PERMISSIONS]) == ["ga4gh_controlled_data"]


def test_output_of_controlled_data_can_be_set_and_is_not_manageable_by_the_owner(session, agent, roles):
    controlled = dataset(session, agent, [roles["grant1"]], [roles["controlled"]])
    own = dataset(session, agent, [], [roles["private"]])
    output = Dataset()
    session.add(output)
    session.commit()
    derived = agent.guess_derived_permissions_for_datasets([controlled, own])
    assert not agent.set_all_dataset_permissions(output, derived, new=True)
    owner_roles = [roles["private"], roles["grant1"]]
    assert agent.can_access_dataset(owner_roles, output)
    assert not agent.can_manage_dataset(owner_roles, output)
    assert not agent.can_access_dataset([roles["private"]], output)
    assert agent.dataset_is_controlled(output)


@pytest.mark.parametrize("path", ["submit", "finish"])
def test_no_controlled_input_unchanged(session, agent, roles, path):
    actions = agent.permitted_actions
    own = dataset(session, agent, [roles["private"]], [roles["private"]])
    if path == "submit":
        assert at_submit(agent, [own]) == {
            actions.DATASET_ACCESS: [roles["private"].id],
            actions.DATASET_MANAGE_PERMISSIONS: [roles["private"].id],
        }
    else:
        derived = agent.guess_derived_permissions_for_datasets([own])
        assert names(derived[actions.DATASET_MANAGE_PERMISSIONS]) == names([roles["private"]])
