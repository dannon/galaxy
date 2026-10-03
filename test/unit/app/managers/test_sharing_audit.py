"""Audit events for sharing changes, dataset permission changes and cross-user copies."""

import json
import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from galaxy import (
    exceptions,
    model,
)
from galaxy.managers import hdas
from galaxy.managers.audit import (
    AUDIT_LOGGER_NAME,
    AuditService,
)
from galaxy.managers.audit_actions.sharing import describe_sharable
from galaxy.managers.histories import (
    HistoryDeserializer,
    HistoryManager,
    HistorySerializer,
)
from galaxy.schema.fields import Security
from galaxy.schema.schema import (
    SetSlugPayload,
    ShareWithPayload,
)
from galaxy.web.framework.request_scope import (
    request_scope,
    RequestIdentity,
    set_request_identity,
)
from galaxy.webapps.galaxy.services.sharable import ShareableService
from .base import BaseTestCase

default_password = "123456"


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events: list[dict] = []

    def emit(self, record):
        self.events.append(json.loads(record.getMessage()))


class AuditTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        logger = logging.getLogger(AUDIT_LOGGER_NAME)
        self._saved_logger = (logger.handlers[:], logger.level, logger.propagate)
        self.handler = CapturingHandler()
        logger.handlers = [self.handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        # Response models encode ids through the process-global Security.security.
        self._security_patch = mock.patch.object(Security, "security", self.trans.security, create=True)
        self._security_patch.start()

    def tearDown(self):
        self._security_patch.stop()
        logger = logging.getLogger(AUDIT_LOGGER_NAME)
        logger.handlers, logger.level, logger.propagate = self._saved_logger
        super().tearDown()

    @property
    def events(self) -> list[dict]:
        return self.handler.events

    def make_audit(self, enabled=True, include_names=False) -> AuditService:
        config = SimpleNamespace(
            audit_log={"enabled": enabled, "include_names": include_names},
            server_name="main.web.1",
            galaxy_infrastructure_url=None,
        )
        return AuditService(config, self.app.security, self.trans.sa_session)  # type: ignore[arg-type]

    def create_user(self, name: str) -> model.User:
        return self.user_manager.create(email=f"{name}@example.org", username=name, password=default_password)

    def as_user(self, user: model.User, actor: model.User | None = None):
        """Run as ``user`` inside a request scope, optionally with an admin impersonating them."""
        self.trans.set_user(user)
        # MockTrans treats every user as an admin unless told otherwise.
        self.mock_trans.user_is_admin = user == self.admin_user
        scope = request_scope(request_id="req-1", remote_addr="192.0.2.10")
        identity = RequestIdentity(
            "session",
            user.id,
            actor_id=actor.id if actor else user.id,
            switch="impersonation" if actor else None,
        )

        class _Scope:
            def __enter__(self):
                scope.__enter__()
                set_request_identity(identity)

            def __exit__(self, *exc):
                scope.__exit__(*exc)

        return _Scope()


class TestSharingAudit(AuditTestCase):
    def set_up_managers(self):
        super().set_up_managers()
        self.history_manager = self.app[HistoryManager]
        self.history_manager.audit = self.make_audit()
        self.service = ShareableService(self.history_manager, self.app[HistorySerializer], mock.MagicMock())
        self.owner = self.create_user("owner")
        self.other = self.create_user("other")
        self.history = self.history_manager.create(name="secret history", user=self.owner)
        self.hda = self.add_hda(self.history)

    def add_hda(self, history: model.History) -> model.HistoryDatasetAssociation:
        hda_manager = self.app[hdas.HDAManager]
        hda = hda_manager.create(history=history, dataset=hda_manager.dataset_manager.create())
        self.trans.sa_session.commit()
        return hda

    def test_publish_records_before_and_after_with_both_identities(self):
        admin = self.admin_user
        with self.as_user(self.owner, actor=admin):
            self.service.publish(self.trans, self.history.id)

        (event,) = self.events
        assert event["action"] == "history.share"
        assert event["outcome"] == "success"
        assert event["actor"]["id"] == admin.id
        assert event["effective_user"]["id"] == self.owner.id
        assert event["auth"]["switch"] == "impersonation"
        assert event["object"]["type"] == "history"
        assert event["object"]["id"] == self.history.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["object"]["name"] is None
        details = event["details"]
        assert details["change"] == "publish"
        assert details["importable_before"] is False and details["importable_after"] is True
        assert details["published_before"] is False and details["published_after"] is True
        # The slug is generated from the history's name, so it stays out by default.
        assert "slug_after" not in details

    def test_repeating_a_change_records_nothing(self):
        with self.as_user(self.owner):
            self.service.publish(self.trans, self.history.id)
            self.service.publish(self.trans, self.history.id)
        assert [event["details"]["change"] for event in self.events] == ["publish"]

    def test_unpublish_and_link_access(self):
        with self.as_user(self.owner):
            self.service.enable_link_access(self.trans, self.history.id)
            self.service.publish(self.trans, self.history.id)
            self.service.unpublish(self.trans, self.history.id)
            self.service.disable_link_access(self.trans, self.history.id)

        changes = [
            (e["details"]["change"], e["details"]["importable_after"], e["details"]["published_after"])
            for e in self.events
        ]
        assert changes == [
            ("enable_link_access", True, False),
            ("publish", True, True),
            ("unpublish", True, False),
            ("disable_link_access", False, False),
        ]

    def test_share_with_users_records_each_user_added_and_removed(self):
        third = self.create_user("third")
        with self.as_user(self.owner):
            self.service.share_with_users(self.trans, self.history.id, ShareWithPayload(user_ids=[self.other.email]))
            self.service.share_with_users(self.trans, self.history.id, ShareWithPayload(user_ids=[third.email]))
            self.service.share_with_users(self.trans, self.history.id, ShareWithPayload(user_ids=[]))

        first, second, third_event = (event["details"] for event in self.events)
        assert first["change"] == "share_with_users"
        assert first["users_added"] == [self.other.id]
        assert "users_removed" not in first
        assert second["users_added"] == [third.id]
        assert second["users_removed"] == [self.other.id]
        assert third_event["users_removed"] == [third.id]
        assert "users_added" not in third_event

    def test_slug_change_carries_slugs_only_with_include_names(self):
        with self.as_user(self.owner):
            self.service.set_slug(self.trans, self.history.id, SetSlugPayload(new_slug="first-slug"))
        assert self.events[-1]["details"] == {
            "change": "set_slug",
            "importable_before": False,
            "importable_after": False,
            "published_before": False,
            "published_after": False,
        }

        self.history_manager.audit = self.make_audit(include_names=True)
        with self.as_user(self.owner):
            self.service.set_slug(self.trans, self.history.id, SetSlugPayload(new_slug="second-slug"))
        details = self.events[-1]["details"]
        assert details["slug_before"] == "first-slug"
        assert details["slug_after"] == "second-slug"
        assert self.events[-1]["object"]["name"] == "secret history"

    def test_refusal_is_recorded_as_denied_with_the_requested_id(self):
        with self.as_user(self.other):
            with pytest.raises(exceptions.ItemOwnershipException):
                self.service.publish(self.trans, self.history.id)

        (event,) = self.events
        assert event["action"] == "history.share"
        assert event["outcome"] == "denied"
        assert event["reason"] == "not_accessible"
        assert event["effective_user"]["id"] == self.other.id
        assert event["object"]["id"] == self.history.id
        assert event["object"]["encoded_id"] == self.app.security.encode_id(self.history.id)
        # Nothing about the history beyond what the request named.
        assert event["object"]["owner_id"] is None
        assert event["details"] == {"change": "publish"}
        self.trans.sa_session.refresh(self.history)
        assert not self.history.published

    def test_reading_the_sharing_status_records_nothing(self):
        with self.as_user(self.other):
            with pytest.raises(exceptions.ItemOwnershipException):
                self.service.sharing(self.trans, self.history.id)
        assert self.events == []

    def test_update_route_records_sharing_keys(self):
        deserializer = self.app[HistoryDeserializer]
        deserializer.manager.audit = self.history_manager.audit
        with self.as_user(self.owner):
            deserializer.deserialize(self.history, {"name": "renamed"}, user=self.owner, trans=self.trans)
            assert self.events == []
            deserializer.deserialize(
                self.history,
                {"importable": True, "users_shared_with": [self.app.security.encode_id(self.other.id)]},
                user=self.owner,
                trans=self.trans,
            )

        (event,) = self.events
        assert event["action"] == "history.share"
        assert event["details"]["change"] == "update"
        assert event["details"]["importable_after"] is True
        assert event["details"]["users_added"] == [self.other.id]

    def test_disabled_auditing_skips_the_share_query(self):
        self.history_manager.audit = self.make_audit(enabled=False)
        assert self.history_manager.sharing_state(self.history) is None
        with mock.patch.object(self.history_manager, "get_share_assocs") as spy:
            with self.as_user(self.owner):
                self.service.publish(self.trans, self.history.id)
        spy.assert_not_called()
        assert self.events == []


class TestDatasetPermissionsAudit(AuditTestCase):
    def set_up_managers(self):
        super().set_up_managers()
        self.audit = self.make_audit()
        self.history_manager = self.app[HistoryManager]
        self.hda_manager = self.history_manager.hda_manager
        self.hda_manager.dataset_manager.audit = self.audit
        self.history_manager.audit = self.audit
        self.service = ShareableService(self.history_manager, self.app[HistorySerializer], mock.MagicMock())

    def set_up_trans(self):
        super().set_up_trans()
        # MockTrans has no role lookup; the permission routes need the current user's.
        self.mock_trans.get_current_user_roles = lambda: self.trans.user.all_roles()  # type: ignore[attr-defined]
        self.owner = self.create_user("owner")
        self.other = self.create_user("other")
        self.history = self.history_manager.create(name="secret history", user=self.owner)
        self.hda = self.hda_manager.create(history=self.history, dataset=self.hda_manager.dataset_manager.create())
        self.trans.sa_session.commit()
        security_agent = self.app.security_agent
        self.private_role = security_agent.get_private_user_role(self.owner)
        actions = security_agent.permitted_actions
        security_agent.set_all_dataset_permissions(
            self.hda.dataset,
            {actions.DATASET_MANAGE_PERMISSIONS: [self.private_role], actions.DATASET_ACCESS: [self.private_role]},
        )

    def permission_events(self) -> list[dict]:
        return [event for event in self.events if event["action"] == "dataset.permissions"]

    def test_making_a_private_dataset_public_is_flagged_as_widening(self):
        with self.as_user(self.owner, actor=self.admin_user):
            self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")

        (event,) = self.events
        assert event["action"] == "dataset.permissions"
        assert event["outcome"] == "success"
        assert (event["actor"]["id"], event["effective_user"]["id"]) == (self.admin_user.id, self.owner.id)
        assert event["object"]["type"] == "hda"
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["dataset_id"] == self.hda.dataset_id
        assert event["object"]["owner_id"] == self.owner.id
        details = event["details"]
        assert details["change"] == "remove_restrictions"
        assert details["access_roles_before"] == [self.private_role.id]
        assert "access_roles_after" not in details
        assert details["manage_roles_before"] == details["manage_roles_after"] == [self.private_role.id]
        assert details["may_widen_access"] is True

    def test_narrowing_is_recorded_without_the_widening_flag(self):
        with self.as_user(self.owner):
            self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")
            self.hda_manager.update_permissions(self.trans, self.hda, action="make_private")

        narrowing = self.events[-1]["details"]
        assert narrowing["change"] == "make_private"
        assert narrowing["access_roles_after"] == [self.private_role.id]
        assert "may_widen_access" not in narrowing

    def test_a_request_that_changes_nothing_records_nothing(self):
        with self.as_user(self.owner):
            self.hda_manager.update_permissions(self.trans, self.hda, action="make_private")
        assert self.events == []

    def test_refusal_is_recorded_as_denied(self):
        with self.as_user(self.other):
            with pytest.raises(exceptions.InsufficientPermissionsException):
                self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")

        (event,) = self.events
        assert (event["outcome"], event["reason"]) == ("denied", "not_accessible")
        assert event["effective_user"]["id"] == self.other.id
        assert event["object"]["id"] == self.hda.id
        assert event["details"] == {"change": "remove_restrictions"}

    def test_invalid_roles_are_recorded_as_an_error(self):
        # Two private roles together would lock everyone out, so Galaxy refuses the combination.
        other_private = self.app.security_agent.get_private_user_role(self.other)
        with self.as_user(self.owner):
            with pytest.raises(exceptions.RequestParameterInvalidException):
                self.hda_manager.update_permissions(
                    self.trans, self.hda, action="set_permissions", access=[self.private_role.id, other_private.id]
                )

        (event,) = self.events
        assert (event["outcome"], event["reason"]) == ("error", "invalid_request")
        assert event["details"] == {"change": "set_permissions"}

    def test_publishing_a_history_records_each_dataset_it_made_public(self):
        with self.as_user(self.owner):
            self.service.publish(self.trans, self.history.id)

        assert [event["action"] for event in self.events] == ["dataset.permissions", "history.share"]
        details = self.permission_events()[0]["details"]
        assert (details["change"], details["via"], details["may_widen_access"]) == (
            "make_public",
            "history_sharing",
            True,
        )
        # Both events belong to one request, so they can be joined.
        assert {event["request_id"] for event in self.events} == {"req-1"}

    def test_sharing_options_that_open_datasets_are_recorded(self):
        with self.as_user(self.owner):
            self.service.share_with_users(
                self.trans,
                self.history.id,
                ShareWithPayload(user_ids=[self.other.email], share_option="make_accessible_to_shared"),
            )

        (permission_event,) = self.permission_events()
        details = permission_event["details"]
        assert details["change"] == "share_privately"
        assert details["via"] == "history_sharing"
        # A private role swapped for a sharing role that also holds the recipient.
        assert details["access_roles_before"] == [self.private_role.id]
        assert details["access_roles_after"] != [self.private_role.id]
        assert self.events[-1]["details"]["users_added"] == [self.other.id]

    def test_disabled_auditing_takes_no_snapshots(self):
        self.hda_manager.dataset_manager.audit = self.make_audit(enabled=False)
        assert self.hda_manager.dataset_manager.permissions_snapshot(self.hda.dataset) is None
        with self.as_user(self.owner):
            self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")
        assert self.events == []


@pytest.mark.parametrize(
    "model_class,attribute,expected_type",
    [
        (model.History, "name", "history"),
        (model.StoredWorkflow, "name", "workflow"),
        (model.Page, "title", "page"),
        (model.Visualization, "title", "visualization"),
    ],
)
def test_every_sharable_type_is_described(model_class, attribute, expected_type):
    item = model_class()
    item.id = 5
    item.user_id = 9
    setattr(item, attribute, "x" * 300)
    service = SimpleNamespace(security=SimpleNamespace(encode_id=lambda i: f"enc{i}"), include_names=True)

    described = describe_sharable(service, item)

    assert described is not None
    assert described.type == expected_type
    assert (described.id, described.encoded_id, described.owner_id) == (5, "enc5", 9)
    assert described.name == "x" * 256
    without_names = describe_sharable(SimpleNamespace(include_names=False, security=service.security), item)
    assert without_names is not None and without_names.name is None


def test_unknown_objects_are_left_to_other_describers():
    assert describe_sharable(SimpleNamespace(), model.User()) is None
