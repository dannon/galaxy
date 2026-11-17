from datetime import timedelta

from sqlalchemy import select

from galaxy.model import (
    Dataset,
    UserRoleAssociation,
)
from galaxy.model.orm.now import now
from galaxy.model.security import GalaxyRBACAgent


def test_expired_membership_grants_nothing(session, make_user, make_role, make_user_role_association):
    user, held, expired = make_user(), make_role(), make_role()
    make_user_role_association(user, held)
    make_user_role_association(user, expired).expiration_time = now() - timedelta(seconds=1)
    session.commit()
    assert user.all_roles() == [held]
    assert user.all_roles_exploiting_cache() == [held]


def test_membership_counts_until_it_expires(session, make_user, make_role, make_user_role_association):
    user, role = make_user(), make_role()
    make_user_role_association(user, role).expiration_time = now() + timedelta(hours=1)
    session.commit()
    assert user.all_roles() == [role]


def test_expired_direct_membership_does_not_hide_the_role_held_through_a_group(
    session,
    make_user,
    make_role,
    make_group,
    make_user_role_association,
    make_user_group_association,
    make_group_role_association,
):
    user, role, group = make_user(), make_role(), make_group()
    make_user_role_association(user, role).expiration_time = now() - timedelta(seconds=1)
    make_user_group_association(user, group)
    make_group_role_association(group, role)
    session.commit()
    assert user.all_roles() == [role]


def test_dataset_restricted_to_role_becomes_inaccessible(session, make_user, make_role):
    agent = GalaxyRBACAgent(session)
    user, role = make_user(), make_role()
    assoc = agent.associate_user_role(user, role, expiration_time=now() + timedelta(hours=1))
    dataset = Dataset()
    session.add(dataset)
    session.commit()
    agent.set_all_dataset_permissions(
        dataset,
        {agent.permitted_actions.DATASET_ACCESS: [role], agent.permitted_actions.DATASET_MANAGE_PERMISSIONS: [role]},
        new=True,
    )
    assert agent.can_access_dataset(user.all_roles(), dataset)
    assoc.expiration_time = now() - timedelta(seconds=1)
    session.commit()
    assert not agent.can_access_dataset(user.all_roles(), dataset)


def test_set_user_role_expiration_creates_then_updates(session, make_user, make_role):
    agent = GalaxyRBACAgent(session)
    user, role = make_user(), make_role()
    first, later = now() + timedelta(hours=1), now() + timedelta(days=1)
    agent.set_user_role_expiration(user, role, first)
    agent.set_user_role_expiration(user, role, later)
    rows = session.scalars(select(UserRoleAssociation).filter_by(user_id=user.id, role_id=role.id)).all()
    assert [row.expiration_time for row in rows] == [later]
