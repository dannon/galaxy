"""Audit events for sharing changes, dataset permission changes and cross-user copies."""

import contextlib
import json
import logging
import os
import tempfile
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from sqlalchemy import (
    event,
    exc,
)
from sqlalchemy.pool import SingletonThreadPool

from galaxy import (
    exceptions,
    model,
)
from galaxy.app_unittest_utils import galaxy_mock
from galaxy.managers import hdas
from galaxy.managers.audit import (
    audit_failures,
    AUDIT_LOGGER_NAME,
    AuditService,
)
from galaxy.managers.audit_actions.sharing import describe_sharable
from galaxy.managers.collections import DatasetCollectionManager
from galaxy.managers.histories import (
    HistoryDeserializer,
    HistoryManager,
    HistorySerializer,
)
from galaxy.managers.sharable import audit_read_session
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
from galaxy.work.context import SessionRequestContext
from .base import (
    admin_users,
    BaseTestCase,
)

default_password = "123456"


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events: list[dict] = []

    def emit(self, record):
        self.events.append(json.loads(record.getMessage()))


class AuditTestCase(BaseTestCase):
    def set_up_mocks(self):
        # A database file rather than in-memory SQLite, whose sessions all share one connection:
        # audit reads go through a session of their own, and these tests need that to be real.
        self._database_dir = tempfile.TemporaryDirectory()
        database = os.path.join(self._database_dir.name, "audit.sqlite")
        admin_users_list = [u for u in admin_users.split(",") if u]
        self.mock_trans = galaxy_mock.MockTrans(
            admin_users=admin_users, admin_users_list=admin_users_list, database_connection=f"sqlite:///{database}"
        )
        self.trans = cast(SessionRequestContext, self.mock_trans)
        self.app = self.trans.app

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
        self.app.model.engine.dispose()
        self._database_dir.cleanup()
        super().tearDown()

    @property
    def events(self) -> list[dict]:
        return self.handler.events

    @contextlib.contextmanager
    def audit_read_fails(self, statement_prefix: str):
        """Drop the connection under the first statement starting with ``statement_prefix``."""
        armed = [True]

        def fail(conn, cursor, statement, parameters, context, executemany):
            if armed[0] and statement.startswith(statement_prefix):
                armed[0] = False
                conn.invalidate()
                raise exc.OperationalError(statement, parameters, Exception("connection lost"))

        engine = self.app.model.engine
        event.listen(engine, "before_cursor_execute", fail)
        try:
            yield
        finally:
            event.remove(engine, "before_cursor_execute", fail)
        assert not armed[0], "the audit read never ran"

    @contextlib.contextmanager
    def request_reads_fail(self, after_commit: bool = False):
        """Fail every SELECT the request's session issues, lazy loads and refreshes included."""
        session = self.trans.sa_session()
        armed = [not after_commit]

        def arm(_session):
            armed[0] = True

        def fail(orm_execute_state):
            if armed[0] and orm_execute_state.is_select:
                raise exc.OperationalError("SELECT", {}, Exception("connection lost"))

        event.listen(session, "do_orm_execute", fail)
        if after_commit:
            event.listen(session, "after_commit", arm)
        try:
            yield
        finally:
            event.remove(session, "do_orm_execute", fail)
            if after_commit:
                event.remove(session, "after_commit", arm)

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

    def test_update_route_records_shares_committed_before_a_later_key_fails(self):
        deserializer = self.app[HistoryDeserializer]
        deserializer.manager.audit = self.history_manager.audit
        with self.as_user(self.owner):
            with pytest.raises(exceptions.RequestParameterInvalidException):
                deserializer.deserialize(
                    self.history,
                    {"users_shared_with": [self.app.security.encode_id(self.other.id)], "published": "garbage"},
                    user=self.owner,
                    trans=self.trans,
                )

        (event,) = self.events
        assert event["details"]["users_added"] == [self.other.id]

    def test_a_rolled_back_update_records_nothing(self):
        deserializer = self.app[HistoryDeserializer]
        deserializer.manager.audit = self.history_manager.audit
        with self.as_user(self.owner):
            # published is set on the item, then importable fails validation; nothing commits.
            with pytest.raises(exceptions.RequestParameterInvalidException):
                deserializer.deserialize(
                    self.history, {"published": True, "importable": None}, user=self.owner, trans=self.trans
                )
        self.trans.sa_session.rollback()
        assert not self.history.published
        assert self.events == []

    def test_an_update_records_keys_that_a_later_commit_carried(self):
        deserializer = self.app[HistoryDeserializer]
        deserializer.manager.audit = self.history_manager.audit
        with self.as_user(self.owner):
            # The share commits everything set before it, published included, then importable fails.
            with pytest.raises(exceptions.RequestParameterInvalidException):
                deserializer.deserialize(
                    self.history,
                    {
                        "published": True,
                        "users_shared_with": [self.app.security.encode_id(self.other.id)],
                        "importable": None,
                    },
                    user=self.owner,
                    trans=self.trans,
                )
        self.trans.sa_session.rollback()

        (event,) = self.events
        assert self.history.published
        assert event["details"]["published_after"] is True
        assert event["details"]["users_added"] == [self.other.id]

    def test_a_failed_audit_read_leaves_the_request_able_to_commit(self):
        with self.audit_read_fails("SELECT history_user_share_association"):
            with self.as_user(self.owner):
                self.service.publish(self.trans, self.history.id)
        self.history.name = "still writable"
        self.trans.sa_session.commit()

        self.trans.sa_session.expire_all()
        assert self.history.published
        assert self.history.name == "still writable"
        # The before-read was lost, so there is nothing to compare against; that is reported, not raised.
        assert self.events == []

    def test_recording_reads_nothing_through_the_request_session(self):
        before = self.history_manager.sharing_state(self.history)
        self.history_manager.publish(self.history)
        with self.as_user(self.owner):
            with self.request_reads_fail():
                self.history_manager.record_sharing_change(self.history, "publish", before)

        (event,) = self.events
        assert event["object"]["id"] == self.history.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"]["published_after"] is True

    def test_disabled_auditing_skips_the_share_query(self):
        self.history_manager.audit = self.make_audit(enabled=False)
        assert self.history_manager.sharing_state(self.history) is None
        with mock.patch.object(self.history_manager, "_read_sharing_state") as spy:
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
        assert details["access_roles_after"] == []
        assert details["manage_roles_before"] == details["manage_roles_after"] == [self.private_role.id]
        assert details["may_widen_access"] is True

    def test_narrowing_is_recorded_without_the_widening_flag(self):
        with self.as_user(self.owner):
            self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")
            self.hda_manager.update_permissions(self.trans, self.hda, action="make_private")

        narrowing = self.events[-1]["details"]
        assert narrowing["change"] == "make_private"
        assert narrowing["access_roles_after"] == [self.private_role.id]
        assert narrowing["may_widen_access"] is False

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
        dataset_manager = self.hda_manager.dataset_manager
        dataset_manager.audit = self.make_audit(enabled=False)
        with mock.patch.object(dataset_manager, "_role_ids", wraps=dataset_manager._role_ids) as spy:
            with self.as_user(self.owner):
                self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")
        spy.assert_not_called()
        assert self.events == []

    def test_a_failure_after_a_partial_commit_still_says_what_changed(self):
        security_agent = self.app.security_agent
        # remove_restrictions commits, then re-checks; make the re-check fail.
        with mock.patch.object(security_agent, "dataset_is_public", return_value=False):
            with self.as_user(self.owner):
                with pytest.raises(exceptions.InternalServerError):
                    self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")

        (event,) = self.events
        assert (event["outcome"], event["reason"]) == ("error", "internal_error")
        assert event["details"]["access_roles_before"] == [self.private_role.id]
        assert event["details"]["access_roles_after"] == []
        assert event["details"]["may_widen_access"] is True

    def test_a_failed_permissions_read_leaves_the_request_able_to_commit(self):
        with self.audit_read_fails("SELECT dataset_permissions.role_id"):
            with self.as_user(self.owner):
                self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")
        self.hda.name = "still writable"
        self.trans.sa_session.commit()

        self.trans.sa_session.expire_all()
        assert self.app.security_agent.dataset_is_public(self.hda.dataset)
        assert self.hda.name == "still writable"
        assert self.events == []

    def test_recording_a_permission_change_reads_nothing_through_the_request_session(self):
        dataset_manager = self.hda_manager.dataset_manager
        before = dataset_manager.permissions_snapshot(self.hda)
        self.app.security_agent.make_dataset_public(self.hda.dataset)
        with self.as_user(self.owner):
            with self.request_reads_fail():
                assert dataset_manager.record_permissions_change(self.hda, "remove_restrictions", before)

        (event,) = self.events
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["dataset_id"] == self.hda.dataset_id
        assert event["details"]["access_roles_after"] == []

    def test_a_failure_that_breaks_the_session_is_still_recorded_without_reading_it(self):
        dataset_manager = self.hda_manager.dataset_manager
        with mock.patch.object(dataset_manager, "_read_permissions", return_value=None):
            with self.as_user(self.other):
                with self.request_reads_fail():
                    with pytest.raises(exc.OperationalError):
                        self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")

        (event,) = self.events
        assert (event["outcome"], event["reason"]) == ("error", "internal_error")
        assert event["object"]["type"] == "hda"
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["encoded_id"] == self.app.security.encode_id(self.hda.id)

    def test_a_refusal_before_the_dataset_loads_names_what_was_asked_for(self):
        with self.as_user(self.other):
            self.hda_manager.record_permissions_denied(self.hda.id, "remove_restrictions")
            self.hda_manager.record_permissions_denied(self.hda.id, "not-an-action")

        named, unnamed = self.events
        assert (named["outcome"], named["reason"]) == ("denied", "not_accessible")
        assert named["object"]["type"] == "hda"
        assert named["object"]["id"] == self.hda.id
        assert named["object"]["owner_id"] is None
        assert named["details"] == {"change": "remove_restrictions"}
        assert unnamed["details"] == {}

    def test_an_in_memory_database_records_nothing_rather_than_reading_uncommitted_state(self):
        engine = mock.Mock(pool=mock.Mock(spec=SingletonThreadPool))
        app = SimpleNamespace(model=SimpleNamespace(engine=engine))
        with pytest.raises(RuntimeError):
            with audit_read_session(app):
                pass

    def test_a_broken_snapshot_never_fails_the_change(self):
        dataset_manager = self.hda_manager.dataset_manager
        failures = audit_failures.count
        with mock.patch.object(dataset_manager, "_role_ids", side_effect=RuntimeError("db gone")):
            with self.as_user(self.owner):
                self.hda_manager.update_permissions(self.trans, self.hda, action="remove_restrictions")
        assert self.app.security_agent.dataset_is_public(self.hda.dataset)
        assert self.events == []
        assert audit_failures.count > failures


class TestCrossUserCopyAudit(AuditTestCase):
    def set_up_managers(self):
        super().set_up_managers()
        self.audit = self.make_audit()
        self.history_manager = self.app[HistoryManager]
        self.history_manager.audit = self.audit
        self.hda_manager = self.history_manager.hda_manager
        self.hda_manager.dataset_manager.audit = self.audit

    def set_up_trans(self):
        super().set_up_trans()
        self.owner = self.create_user("owner")
        self.recipient = self.create_user("recipient")
        self.source_history = self.history_manager.create(name="source", user=self.owner)
        self.hda = self.hda_manager.create(
            history=self.source_history, dataset=self.hda_manager.dataset_manager.create()
        )
        self.trans.sa_session.commit()

    def test_copying_another_users_dataset_names_owner_and_recipient(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            copy = self.hda_manager.copy(self.hda, history=target)

        (event,) = self.events
        assert event["action"] == "dataset.copy"
        assert event["outcome"] == "success"
        assert event["effective_user"]["id"] == self.recipient.id
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["object"]["dataset_id"] == self.hda.dataset_id
        assert event["details"] == {
            "new_hda_id": copy.id,
            "target_history_id": target.id,
            "recipient_id": self.recipient.id,
        }

    def test_copying_within_ones_own_histories_records_nothing(self):
        other_history = self.history_manager.create(name="also mine", user=self.owner)
        with self.as_user(self.owner):
            self.hda_manager.copy(self.hda, history=other_history)
        assert self.events == []

    def test_recording_a_copy_reads_nothing_through_the_request_session(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        source_id, target_id = self.hda.id, target.id
        with self.as_user(self.recipient):
            with self.request_reads_fail(after_commit=True):
                self.hda_manager.copy(self.hda, history=target)

        (event,) = self.events
        assert event["object"]["id"] == source_id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"]["target_history_id"] == target_id

    def test_a_copy_whose_audit_reads_all_fail_still_succeeds(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        failures = audit_failures.count
        with mock.patch.object(hdas, "audit_read_session", side_effect=RuntimeError("db gone")):
            with self.as_user(self.recipient):
                with self.request_reads_fail(after_commit=True):
                    self.hda_manager.copy(self.hda, history=target)
        assert self.events == []
        assert audit_failures.count > failures

    def test_a_refused_copy_is_recorded_as_denied(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            self.hda_manager.record_copy_denied(self.hda.id, target)

        (event,) = self.events
        assert (event["action"], event["outcome"], event["reason"]) == ("dataset.copy", "denied", "not_accessible")
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["owner_id"] is None
        assert event["details"] == {"target_history_id": target.id}

    def build_collection_from(self, target: model.History):
        collections = self.app[DatasetCollectionManager]
        return collections.create(
            self.trans,
            parent=target,
            name="copied",
            collection_type="list",
            element_identifiers=[{"src": "hda", "id": self.hda.id, "name": "first"}],
            copy_elements=True,
            history=target,
        )

    def test_collection_element_copies_from_another_user_are_recorded(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            hdca = self.build_collection_from(target)
            assert self.events == []
            self.hda_manager.record_collection_copies(hdca)

        (event,) = self.events
        (copy,) = hdca.dataset_instances
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"] == {
            "new_hda_id": copy.id,
            "target_history_id": target.id,
            "recipient_id": self.recipient.id,
        }

    def test_collection_copies_of_ones_own_datasets_record_nothing(self):
        target = self.history_manager.create(name="also mine", user=self.owner)
        with self.as_user(self.owner):
            self.trans.set_history(target)
            hdca = self.build_collection_from(target)
            self.hda_manager.record_collection_copies(hdca)
        assert self.events == []

    def test_an_uncommitted_copy_records_nothing(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            self.hda_manager.copy(self.hda, history=target, flush=False)
        assert self.events == []

    def test_importing_another_users_history(self):
        with self.as_user(self.recipient):
            new_history = self.source_history.copy(name="Copy", target_user=self.recipient, all_datasets=True)
            self.trans.sa_session.commit()
            self.history_manager.record_history_import(self.source_history, new_history, all_datasets=True)

        (event,) = self.events
        assert event["action"] == "history.import"
        assert event["object"]["type"] == "history"
        assert event["object"]["id"] == self.source_history.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"] == {
            "new_history_id": new_history.id,
            "recipient_id": self.recipient.id,
            "all_datasets": True,
        }

    def test_recording_an_import_reads_nothing_through_the_request_session(self):
        with self.as_user(self.recipient):
            new_history = self.source_history.copy(name="Copy", target_user=self.recipient)
            self.trans.sa_session.commit()
            new_history_id = new_history.id
            self.trans.sa_session.expire_all()
            with self.request_reads_fail():
                self.history_manager.record_history_import(self.source_history, new_history, all_datasets=False)

        (event,) = self.events
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"] == {"new_history_id": new_history_id, "recipient_id": self.recipient.id}

    def test_copying_ones_own_history_records_nothing(self):
        with self.as_user(self.owner):
            new_history = self.source_history.copy(name="Copy", target_user=self.owner)
            self.trans.sa_session.commit()
            self.history_manager.record_history_import(self.source_history, new_history, all_datasets=False)
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
