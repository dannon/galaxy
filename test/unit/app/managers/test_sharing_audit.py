"""Audit events for sharing changes, dataset permission changes and cross-user copies."""

import contextlib
import json
import logging
import os
import tempfile
import uuid
from types import SimpleNamespace
from typing import (
    Any,
    cast,
)
from unittest import mock

import pytest
from sqlalchemy import (
    event,
    exc,
    select as sa_select,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import SingletonThreadPool

from galaxy import (
    exceptions,
    model,
)
from galaxy.app_unittest_utils import galaxy_mock
from galaxy.celery import tasks as celery_tasks
from galaxy.managers import (
    audit as audit_module,
    hdas,
)
from galaxy.managers.audit import (
    audit_failures,
    AUDIT_LOGGER_NAME,
    audit_read_session,
    AuditService,
)
from galaxy.managers.audit_actions.exports import ExportDetails
from galaxy.managers.audit_actions.sharing import (
    describe_sharable,
    SharingChangeDetails,
)
from galaxy.managers.collections import DatasetCollectionManager
from galaxy.managers.histories import (
    HistoryDeserializer,
    HistoryManager,
    HistorySerializer,
)
from galaxy.schema import SerializationParams
from galaxy.schema.fields import Security
from galaxy.schema.schema import (
    CreateHistoryPayload,
    DatasetSourceType,
    HistoryContentSource,
    SetSlugPayload,
    ShareWithPayload,
    UpdateDatasetPermissionsPayload,
)
from galaxy.web.framework.request_scope import (
    request_scope,
    RequestIdentity,
    set_request_identity,
)
from galaxy.webapps.galaxy.services.datasets import DatasetsService
from galaxy.webapps.galaxy.services.histories import HistoriesService
from galaxy.webapps.galaxy.services.history_contents import HistoriesContentsService
from galaxy.webapps.galaxy.services.sharable import ShareableService
from galaxy.work.context import SessionRequestContext
from galaxy.workflow.completion_hooks.export import ExportToFileSourceHook
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

    def commit_elsewhere(self, change):
        """Commit ``change(history)`` through a session of its own, as a concurrent request would."""
        with Session(self.app.model.engine) as session:
            change(session.get(model.History, self.history.id), session)
            session.commit()

    def test_a_refused_update_never_claims_a_concurrent_change(self):
        deserializer = self.app[HistoryDeserializer]
        deserializer.manager.audit = self.history_manager.audit
        validate_importable = deserializer.deserializers["importable"]

        def published_meanwhile(item, key, val, **context):
            self.commit_elsewhere(lambda history, _session: setattr(history, "published", True))
            return validate_importable(item, key, val, **context)

        with mock.patch.dict(deserializer.deserializers, {"importable": published_meanwhile}):
            with self.as_user(self.owner):
                with pytest.raises(exceptions.RequestParameterInvalidException):
                    deserializer.deserialize(self.history, {"importable": None}, user=self.owner, trans=self.trans)
        self.trans.sa_session.rollback()

        assert self.history.published
        assert self.events == []

    def test_a_change_never_claims_a_concurrent_share(self):
        def make_members_public(trans, item):
            self.commit_elsewhere(
                lambda history, session: session.add(
                    model.HistoryUserShareAssociation(history=history, user=session.get(model.User, self.other.id))
                )
            )

        with mock.patch.object(self.history_manager, "make_members_public", make_members_public):
            with self.as_user(self.owner):
                self.service.publish(self.trans, self.history.id)

        (event,) = self.events
        assert event["details"]["published_after"] is True
        assert "users_added" not in event["details"]
        assert self.history_manager.get_share_assocs(self.history)

    def test_a_rolled_back_savepoint_keeps_what_was_flushed_before_it(self):
        session = self.trans.sa_session
        with self.as_user(self.owner):
            with self.history_manager.recording_sharing_change(self.history, "update"):
                self.history.importable = True
                session.flush()
                with pytest.raises(RuntimeError):
                    with session.begin_nested():
                        self.history.published = True
                        session.flush()
                        raise RuntimeError("savepoint fails")
                session.commit()

        (event,) = self.events
        assert event["details"]["importable_after"] is True
        # Undone with the savepoint, so never committed.
        assert event["details"]["published_after"] is False

    def test_a_released_savepoint_is_not_a_commit(self):
        session = self.trans.sa_session
        with self.as_user(self.owner):
            with self.history_manager.recording_sharing_change(self.history, "update"):
                self.history.importable = True
                session.flush()
                with session.begin_nested():
                    self.history.published = True
                # The savepoint was released, but the transaction holding it never commits.
                session.rollback()

        session.expire_all()
        assert (self.history.importable, self.history.published) == (False, False)
        assert self.events == []

    def test_refusing_queries_can_nest(self):
        session = self.trans.sa_session()
        with audit_module._queries_refused(session):
            with audit_module._queries_refused(session):
                pass
            session.expire(self.history)
            with pytest.raises(audit_module._QueryRefused):
                assert self.history.name
        session.rollback()

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
        with self.as_user(self.owner):
            with self.request_reads_fail(after_commit=True):
                with self.history_manager.recording_sharing_change(self.history, "publish"):
                    self.history_manager.publish(self.history)

        (event,) = self.events
        assert event["object"]["id"] == self.history.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"]["published_after"] is True

    def test_a_refused_history_update_with_sharing_keys_is_denied(self):
        service = mock.Mock(manager=self.history_manager)
        with self.as_user(self.other):
            with pytest.raises(exceptions.ItemOwnershipException):
                HistoriesService.update(
                    service, self.trans, self.history.id, {"published": True}, SerializationParams()
                )
            with pytest.raises(exceptions.ItemOwnershipException):
                HistoriesService.update(service, self.trans, self.history.id, {"name": "x"}, SerializationParams())

        (event,) = self.events
        assert (event["action"], event["outcome"], event["reason"]) == ("history.share", "denied", "not_accessible")
        assert event["object"]["id"] == self.history.id
        assert event["details"] == {"change": "update"}
        service.deserializer.deserialize.assert_not_called()

    def test_user_names_are_read_without_the_request_session(self):
        self.history_manager.audit = self.make_audit(include_names=True)
        with self.as_user(self.owner, actor=self.admin_user):
            # The commit expires the users' names, so reading them on this session is a query.
            with self.request_reads_fail(after_commit=True):
                with self.history_manager.recording_sharing_change(self.history, "publish"):
                    self.history_manager.publish(self.history)
            self.trans.sa_session.commit()

        (event,) = self.events
        assert event["actor"]["username"] == self.admin_user.username
        assert event["effective_user"]["email"] == "owner@example.org"
        assert event["truncated"] == []

    def test_loaded_user_names_take_no_connection_of_their_own(self):
        audit = self.make_audit(include_names=True)
        # Loaded, as a request's own user and the item it acts on are.
        assert self.owner.username and self.admin_user.email and self.history.name
        with mock.patch.object(audit_module, "audit_read_session", side_effect=AssertionError("second connection")):
            with self.as_user(self.owner, actor=self.admin_user):
                audit.record("history.share", self.history, details=SharingChangeDetails(change="publish"))

        (event,) = self.events
        assert (event["actor"]["email"], event["effective_user"]["username"]) == (self.admin_user.email, "owner")
        assert event["truncated"] == []

    def test_disabled_auditing_skips_the_share_query(self):
        self.history_manager.audit = self.make_audit(enabled=False)
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

    def test_a_permission_change_on_an_inaccessible_dataset_is_denied(self):
        service = mock.Mock(dataset_manager_by_type={DatasetSourceType.hda: self.hda_manager})
        payload = UpdateDatasetPermissionsPayload(action="remove_restrictions")
        with self.as_user(self.other):
            with pytest.raises(exceptions.ItemAccessibilityException):
                DatasetsService.update_permissions(service, self.trans, self.hda.id, payload)

        (event,) = self.permission_events()
        assert (event["outcome"], event["reason"]) == ("denied", "not_accessible")
        assert (event["object"]["type"], event["object"]["id"]) == ("hda", self.hda.id)
        assert event["effective_user"]["id"] == self.other.id
        assert event["details"] == {"change": "remove_restrictions"}

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
            self.hda_manager.record_permissions_denied(self.hda.id, "")

        named, unnamed, empty = self.events
        assert (named["outcome"], named["reason"]) == ("denied", "not_accessible")
        assert named["object"]["type"] == "hda"
        assert named["object"]["id"] == self.hda.id
        assert named["object"]["owner_id"] is None
        assert named["details"] == {"change": "remove_restrictions"}
        assert unnamed["details"] == {}
        # Only a missing action means set_permissions; an empty one is invalid.
        assert empty["details"] == {}

    def test_audit_reads_are_refused_on_a_database_whose_sessions_share_a_connection(self):
        engine = mock.Mock(pool=mock.Mock(spec=SingletonThreadPool))
        engine.engine = engine
        with pytest.raises(RuntimeError):
            with audit_read_session(engine):
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

    def build_collection(self, target: model.History, element_identifiers: list[dict], collection_type="list"):
        """Build a collection the way the API does, returning it and the copies it made."""
        collections = self.app[DatasetCollectionManager]
        session = self.trans.sa_session
        hdca = collections.create(
            self.trans,
            parent=target,
            name="copied",
            collection_type=collection_type,
            element_identifiers=element_identifiers,
            copy_elements=True,
            history=target,
            flush=False,
        )
        copies = [
            obj
            for obj in session.new
            if isinstance(obj, model.HistoryDatasetAssociation)
            and obj.copied_from_history_dataset_association_id is not None
        ]
        session.commit()
        return hdca, copies

    def test_collection_element_copies_from_another_user_are_recorded(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            _, copies = self.build_collection(target, [{"src": "hda", "id": self.hda.id, "name": "first"}])
            assert self.events == []
            self.hda_manager.record_copies(copies)

        (event,) = self.events
        (copy,) = copies
        assert event["object"]["id"] == self.hda.id
        assert event["object"]["owner_id"] == self.owner.id
        assert event["details"] == {
            "new_hda_id": copy.id,
            "target_history_id": target.id,
            "recipient_id": self.recipient.id,
        }

    def test_a_collection_that_only_references_existing_copies_records_nothing(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            first, _ = self.build_collection(target, [{"src": "hda", "id": self.hda.id, "name": "first"}])
            # Nested collections are referenced, not copied, even with copy_elements.
            _, copies = self.build_collection(
                target, [{"src": "hdca", "id": first.id, "name": "outer"}], collection_type="list:list"
            )
            self.hda_manager.record_copies(copies)
        assert copies == []
        assert self.events == []

    def test_collection_copies_of_ones_own_datasets_record_nothing(self):
        target = self.history_manager.create(name="also mine", user=self.owner)
        with self.as_user(self.owner):
            self.trans.set_history(target)
            _, copies = self.build_collection(target, [{"src": "hda", "id": self.hda.id, "name": "first"}])
            self.hda_manager.record_copies(copies)
        assert len(copies) == 1
        assert self.events == []

    def test_a_refused_dataset_copy_through_the_service_is_denied(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        service = mock.Mock(hda_manager=self.hda_manager, history_manager=self.history_manager)
        with self.as_user(self.recipient):
            # The dataset itself is public; the history holding it isn't shared.
            with pytest.raises(exceptions.ItemAccessibilityException):
                HistoriesContentsService._HistoriesContentsService__create_hda_from_copy(  # type: ignore[attr-defined]
                    service, self.trans, target, self.hda.id
                )

        (event,) = self.events
        assert (event["action"], event["outcome"]) == ("dataset.copy", "denied")
        assert event["object"]["id"] == self.hda.id
        assert event["details"] == {"target_history_id": target.id}

    def test_copies_in_a_released_savepoint_that_never_commits_record_nothing(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        collections = self.collections()
        session = self.trans.sa_session
        with self.as_user(self.recipient):
            with collections._recording_copies(self.trans, True) as tracker:
                assert tracker is not None
                with session.begin_nested():
                    tracker.made.append(self.hda_manager.copy(self.hda, history=target, flush=False))
                session.rollback()
                assert tracker.committed == []
        assert self.events == []

    def collections(self) -> DatasetCollectionManager:
        collections = self.app[DatasetCollectionManager]
        # The container gives the collection manager datasets managers of its own.
        collections.hda_manager.dataset_manager.audit = self.audit
        return collections

    def test_collections_record_the_copies_they_make(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        collections = self.collections()
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            first = collections.create(
                self.trans,
                parent=target,
                name="copied",
                collection_type="list",
                element_identifiers=[{"src": "hda", "id": self.hda.id, "name": "first"}],
                copy_elements=True,
                history=target,
            )
            (created,) = self.events
            # Nested collections are referenced, not copied, even with copy_elements.
            collections.create(
                self.trans,
                parent=target,
                name="outer",
                collection_type="list:list",
                element_identifiers=[{"src": "hdca", "id": first.id, "name": "outer"}],
                copy_elements=True,
                history=target,
            )
            assert self.events == [created]
            mine = self.history_manager.create(name="also mine", user=self.recipient)
            collections.copy(self.trans, mine, HistoryContentSource.hdca, first.id, copy_elements=True)

        assert created["object"]["id"] == self.hda.id
        assert created["details"]["recipient_id"] == self.recipient.id
        # Copying the recipient's own copy moves nothing between users.
        assert len(self.events) == 1

    def test_collection_copies_from_another_user_are_recorded(self):
        collections = self.collections()
        with self.as_user(self.owner):
            self.trans.set_history(self.source_history)
            source = collections.create(
                self.trans,
                parent=self.source_history,
                name="theirs",
                collection_type="list",
                element_identifiers=[{"src": "hda", "id": self.hda.id, "name": "first"}],
            )
        self.source_history.importable = True
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            new_hdca = collections.copy(self.trans, target, HistoryContentSource.hdca, source.id, copy_elements=True)

        (event,) = self.events
        (copy,) = new_hdca.dataset_instances
        assert event["object"]["id"] == self.hda.id
        assert event["details"] == {
            "new_hda_id": copy.id,
            "target_history_id": target.id,
            "recipient_id": self.recipient.id,
        }

    def add_library_dataset(self) -> model.LibraryDatasetDatasetAssociation:
        session = self.trans.sa_session
        folder = model.LibraryFolder(name="root")
        library = model.Library(name="shared", root_folder=folder)
        library_dataset = model.LibraryDataset(folder=folder, name="reads")
        ldda = model.LibraryDatasetDatasetAssociation(
            name="reads",
            extension="txt",
            library_dataset=library_dataset,
            dataset=self.hda_manager.dataset_manager.create(),
            user=self.owner,
            create_dataset=False,
            sa_session=session,
        )
        library_dataset.library_dataset_dataset_association = ldda
        session.add_all([library, folder, library_dataset, ldda])
        session.commit()
        return ldda

    def test_copies_are_recorded_when_a_later_element_commits_the_session(self):
        ldda = self.add_library_dataset()
        other_hda = self.hda_manager.create(
            history=self.source_history, dataset=self.hda_manager.dataset_manager.create()
        )
        self.trans.sa_session.commit()
        target = self.history_manager.create(name="mine", user=self.recipient)
        # MockTrans has no role lookup; library access checks need the current user's.
        self.mock_trans.get_current_user_roles = lambda: self.trans.user.all_roles()  # type: ignore[attr-defined]
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            self.collections().create(
                self.trans,
                parent=target,
                name="mixed",
                collection_type="list",
                element_identifiers=[
                    {"src": "hda", "id": self.hda.id, "name": "first"},
                    # Bringing a library dataset into the history commits the session.
                    {"src": "ldda", "id": ldda.id, "name": "second"},
                    {"src": "hda", "id": other_hda.id, "name": "third"},
                ],
                copy_elements=True,
                history=target,
            )

        assert sorted(event["object"]["id"] for event in self.events) == sorted([self.hda.id, other_hda.id])
        assert {event["action"] for event in self.events} == {"dataset.copy"}

    def test_copies_committed_before_a_later_element_fails_are_recorded(self):
        ldda = self.add_library_dataset()
        private_hda = self.hda_manager.create(
            history=self.source_history, dataset=self.hda_manager.dataset_manager.create()
        )
        self.trans.sa_session.commit()
        security_agent = self.app.security_agent
        private_role = security_agent.get_private_user_role(self.owner)
        actions = security_agent.permitted_actions
        security_agent.set_all_dataset_permissions(
            private_hda.dataset,
            {actions.DATASET_MANAGE_PERMISSIONS: [private_role], actions.DATASET_ACCESS: [private_role]},
        )
        target = self.history_manager.create(name="mine", user=self.recipient)
        self.mock_trans.get_current_user_roles = lambda: self.trans.user.all_roles()  # type: ignore[attr-defined]
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            with pytest.raises(exceptions.ItemAccessibilityException):
                self.collections().create(
                    self.trans,
                    parent=target,
                    name="mixed",
                    collection_type="list",
                    element_identifiers=[
                        {"src": "hda", "id": self.hda.id, "name": "first"},
                        # Commits the copy of "first" before "third" is refused.
                        {"src": "ldda", "id": ldda.id, "name": "second"},
                        {"src": "hda", "id": private_hda.id, "name": "third"},
                    ],
                    copy_elements=True,
                    history=target,
                )
        self.trans.sa_session.rollback()

        (event,) = self.events
        assert (event["action"], event["outcome"]) == ("dataset.copy", "success")
        assert event["object"]["id"] == self.hda.id
        committed_copy = self.trans.sa_session.get(model.HistoryDatasetAssociation, event["details"]["new_hda_id"])
        assert committed_copy is not None and committed_copy.history_id == target.id

    def test_a_failed_build_that_committed_nothing_records_nothing(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            self.trans.set_history(target)
            with pytest.raises(exceptions.RequestParameterInvalidException):
                self.collections().create(
                    self.trans,
                    parent=target,
                    name="broken",
                    collection_type="list",
                    element_identifiers=[
                        {"src": "hda", "id": self.hda.id, "name": "first"},
                        {"src": "nonsense", "id": self.hda.id, "name": "second"},
                    ],
                    copy_elements=True,
                    history=target,
                )
        self.trans.sa_session.rollback()
        assert self.events == []

    def test_collections_track_copies_only_when_audited(self):
        collections = self.collections()
        collections.hda_manager.dataset_manager.audit = self.make_audit(enabled=False)
        target = self.history_manager.create(name="mine", user=self.recipient)
        with mock.patch.object(collections.hda_manager, "record_copies") as record_copies:
            with self.as_user(self.recipient):
                self.trans.set_history(target)
                collections.create(
                    self.trans,
                    parent=target,
                    name="copied",
                    collection_type="list",
                    element_identifiers=[{"src": "hda", "id": self.hda.id, "name": "first"}],
                    copy_elements=True,
                    history=target,
                )
        record_copies.assert_not_called()

    def history_service(self) -> mock.Mock:
        service = mock.Mock(manager=self.history_manager, user_manager=self.user_manager)
        service._serialize_history.side_effect = lambda trans, history, params: history
        return service

    def test_importing_a_history_through_the_service_is_recorded(self):
        self.source_history.importable = True
        self.trans.sa_session.commit()
        payload = CreateHistoryPayload(all_datasets=False)
        payload.history_id = self.source_history.id
        with self.as_user(self.recipient):
            new_history = HistoriesService.create(self.history_service(), self.trans, payload, SerializationParams())

        (event,) = self.events
        assert (event["action"], event["outcome"]) == ("history.import", "success")
        assert event["object"]["id"] == self.source_history.id
        assert event["details"] == {"new_history_id": new_history.id, "recipient_id": self.recipient.id}

    def test_a_refused_history_import_is_denied(self):
        payload = CreateHistoryPayload()
        payload.history_id = self.source_history.id
        with self.as_user(self.recipient):
            with pytest.raises(exceptions.ItemAccessibilityException):
                HistoriesService.create(self.history_service(), self.trans, payload, SerializationParams())

        (event,) = self.events
        assert (event["action"], event["outcome"], event["reason"]) == ("history.import", "denied", "not_accessible")
        assert (event["object"]["type"], event["object"]["id"]) == ("history", self.source_history.id)

    def test_a_copy_that_cant_be_read_loses_only_its_own_event(self):
        target = self.history_manager.create(name="mine", user=self.recipient)
        with self.as_user(self.recipient):
            copy = self.hda_manager.copy(self.hda, history=target, flush=False)
            self.trans.sa_session.commit()
            never_saved = model.HistoryDatasetAssociation(create_dataset=False, sa_session=None)
            self.hda_manager.record_copies([never_saved, copy])

        (event,) = self.events
        assert event["details"]["new_hda_id"] == copy.id

    def test_refusal_recorders_never_raise_on_bad_input(self):
        failures = audit_failures.count
        with self.as_user(self.recipient):
            self.hda_manager.record_copy_denied(None, None)
            self.hda_manager.record_permissions_denied("f2db41e1fa331b3e", "make_private")
        assert self.events == []
        assert audit_failures.count == failures + 2

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
    assert described.history_name == (described.name if expected_type == "history" else None)
    without_names = describe_sharable(SimpleNamespace(include_names=False, security=service.security), item)
    assert without_names is not None and without_names.name is None


def test_unknown_objects_are_left_to_other_describers():
    assert describe_sharable(SimpleNamespace(), model.User()) is None


class TestDescribingAfterACommit(AuditTestCase):
    def set_up_managers(self):
        super().set_up_managers()
        # The hook takes the app's own service.
        self.audit = self.app[AuditService]
        self.audit.enabled = True
        self.app.config.galaxy_infrastructure_url = None
        self.owner = self.create_user("owner")
        session = self.trans.sa_session
        history = model.History(name="results", user=self.owner)
        self.invocation = model.WorkflowInvocation()
        self.invocation.history = history
        self.invocation.workflow = model.Workflow()
        self.invocation.on_complete = [{"export_to_file_source": {"target_uri": "gxftp://exports/run.zip"}}]
        session.add_all([history, self.invocation])
        session.commit()

    @contextlib.contextmanager
    def history_loads_break_the_session(self):
        """After the next commit, a history query reaching the database through the request's
        session fails as a dropped connection does; other sessions' queries go through."""
        session = self.trans.sa_session()
        engine = self.app.model.engine
        armed = [False]
        request_connections: list = []
        failed: list[str] = []

        def arm(_session):
            armed[0] = True

        def note_connection(orm_execute_state):
            if armed[0]:
                request_connections.append(orm_execute_state.session.connection())

        def fail(conn, cursor, statement, parameters, context, executemany):
            if any(conn is known for known in request_connections) and "FROM history " in statement:
                failed.append(statement)
                conn.invalidate()
                raise exc.OperationalError(statement, parameters, Exception("connection lost"))

        event.listen(session, "after_commit", arm)
        event.listen(session, "do_orm_execute", note_connection)
        event.listen(engine, "before_cursor_execute", fail)
        try:
            yield failed
        finally:
            event.remove(engine, "before_cursor_execute", fail)
            event.remove(session, "do_orm_execute", note_connection)
            event.remove(session, "after_commit", arm)

    def test_the_completion_export_event_leaves_the_final_commit_working(self):
        task = mock.Mock()
        task_id = str(uuid.uuid4())
        task.delay.return_value = SimpleNamespace(id=task_id)
        completion = SimpleNamespace(workflow_invocation=self.invocation)
        hook = ExportToFileSourceHook(cast(Any, self.app))
        with mock.patch.object(celery_tasks, "write_invocation_to", task):
            with self.history_loads_break_the_session() as failed:
                hook.execute(cast(Any, completion))

        assert failed == []
        association = self.trans.sa_session.scalars(sa_select(model.StoreExportAssociation)).one()
        assert str(association.task_uuid) == task_id
        (event_,) = self.events
        assert (event_["action"], event_["outcome"]) == ("invocation.export", "success")
        assert event_["object"]["id"] == self.invocation.id
        assert event_["object"]["owner_id"] == self.owner.id
        assert event_["truncated"] == []

    def test_objects_the_request_loaded_are_described_without_a_connection_of_their_own(self):
        # Loaded, as the hook has them before its first commit.
        assert self.invocation.history.user_id and self.invocation.workflow_id
        with mock.patch.object(audit_module, "audit_read_session", side_effect=AssertionError("second connection")):
            with self.as_user(self.owner):
                self.audit.record("invocation.export", self.invocation, details=ExportDetails(destination="download"))

        (event_,) = self.events
        assert event_["object"]["owner_id"] == self.owner.id
        assert event_["truncated"] == []
