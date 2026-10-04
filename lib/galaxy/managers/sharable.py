"""
Superclass Manager and Serializers for Sharable objects.

A sharable Galaxy object:
    has an owner/creator User
    is sharable with other, specific Users
    is importable (copyable) by users that have access
    has a slug which can be used as a link to view the resource
    can be published effectively making it available to all other Users
    can be rated
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from typing import (
    Any,
    NamedTuple,
    TypeVar,
)

from slugify import slugify
from sqlalchemy import (
    event,
    exists,
    false,
    inspect as sa_inspect,
    select,
    true,
)
from sqlalchemy.orm import (
    class_mapper,
    object_session,
)
from sqlalchemy.orm.attributes import instance_state

from galaxy import (
    exceptions,
    model,
)
from galaxy.managers import (
    annotatable,
    base,
    ratable,
    secured,
    taggable,
    users,
)
from galaxy.managers.audit import (
    audit_failures,
    audit_id,
    audit_read_session,
    audit_service_for,
    AuditService,
)
from galaxy.managers.audit_actions import AuditObject
from galaxy.managers.audit_actions.sharing import (
    SHARABLE_TYPES,
    SHARE_ACTIONS,
    SharingAction,
    SharingChange,
    SharingChangeDetails,
)
from galaxy.managers.base import combine_lists
from galaxy.managers.context import ProvidesUserContext
from galaxy.model import (
    User,
    UserShareAssociation,
)
from galaxy.model.tags import GalaxyTagHandler
from galaxy.schema.schema import (
    ShareWithExtra,
    SharingOptions,
)
from galaxy.structured_app import MinimalManagerApp
from galaxy.util import ready_name_for_url
from galaxy.util.hash_util import md5_hash_str

log = logging.getLogger(__name__)
# Only model classes that have `users_shared_with` field
U = TypeVar("U", model.History, model.Page, model.StoredWorkflow, model.Visualization)


class SharingState(NamedTuple):
    importable: bool
    published: bool
    slug: str | None
    user_ids: frozenset[int]
    # The item, described while it was read.
    target: AuditObject

    def access(self) -> tuple[bool, bool, str | None, frozenset[int]]:
        return self.importable, self.published, self.slug, self.user_ids


# Item columns that change who can reach it.
SHARING_COLUMNS = ("importable", "published", "slug")


class _SharingWrites:
    """What one request's own flushes wrote to an item's sharing, kept once a commit carries it.

    Read from the attribute history and the session's new and deleted share rows at each
    flush, so nothing here queries, and values another request committed never appear.
    """

    def __init__(self, item, share_model: type[UserShareAssociation], item_relationship: str) -> None:
        self._item = item
        self._item_id = audit_id(item)
        self._share_model = share_model
        mapper = class_mapper(share_model)
        (item_column,) = mapper.relationships[item_relationship].local_columns
        self._item_key = mapper.get_property_by_column(item_column).key
        self._pending: dict[str, Any] = {}
        self._pending_users: dict[int, bool] = {}
        self.values: dict[str, Any] = {}
        self.users_added: frozenset[int] = frozenset()
        self.users_removed: frozenset[int] = frozenset()

    def flushed(self, session, _flush_context) -> None:
        try:
            state = sa_inspect(self._item)
            for key in SHARING_COLUMNS:
                added = state.attrs[key].history.added
                if added:
                    self._pending[key] = added[-1]
            for objects, shared in ((session.new, True), (session.deleted, False)):
                for obj in objects:
                    user_id = self._share_user_id(obj)
                    if user_id is not None:
                        self._pending_users[user_id] = shared
        except Exception:
            # Never fail the flush over its audit event; the event still says what it saw.
            audit_failures.report("prepare", "Could not follow a sharing change of item %s", self._item_id)

    def committed(self, _session) -> None:
        self.values.update(self._pending)
        added, removed = set(self.users_added), set(self.users_removed)
        for user_id, shared in self._pending_users.items():
            (added if shared else removed).add(user_id)
            (removed if shared else added).discard(user_id)
        self.users_added, self.users_removed = frozenset(added), frozenset(removed)
        self.rolled_back(_session)

    def rolled_back(self, _session) -> None:
        self._pending = {}
        self._pending_users = {}

    def _share_user_id(self, obj) -> int | None:
        if not isinstance(obj, self._share_model):
            return None
        # Column values only: following a relationship here would be a query mid-flush.
        values = instance_state(obj).dict
        if values.get(self._item_key) != self._item_id:
            return None
        return values.get("user_id")


class SharableModelManager(
    base.ModelManager[U],
    secured.OwnableManagerMixin[U],
    secured.AccessibleManagerMixin[U],
    annotatable.AnnotatableManagerMixin,
    ratable.RatableManagerMixin,
):
    # e.g. histories, pages, stored workflows, visualizations
    # base.DeleteableModelMixin? (all four are deletable)

    #: the model used for UserShareAssociations with this model
    user_share_model: type[UserShareAssociation]

    #: the single character abbreviation used in username_and_slug: e.g. 'h' for histories: u/user/h/slug
    SINGLE_CHAR_ABBR: str | None = None

    def __init__(self, app: MinimalManagerApp):
        super().__init__(app)
        # user manager is needed to check access/ownership/admin
        self.user_manager = users.UserManager(app)
        self.tag_handler = app[GalaxyTagHandler]

    # .... has a user
    def by_user(self, user: User, **kwargs: Any) -> list[Any]:
        """
        Return list for all items (of model_class type) associated with the given
        `user`.
        """
        user_filter = self.model_class.table.c.user_id == user.id
        filters = combine_lists(user_filter, kwargs.get("filters", None))
        return self.list(filters=filters, **kwargs)

    # .... owned/accessible interfaces
    def is_owner(self, item: model.Base, user: User | None, **kwargs: Any) -> bool:
        """
        Return true if this sharable belongs to `user` (or `user` is an admin).
        """
        # ... effectively a good fit to have this here, but not semantically
        if self.user_manager.is_admin(user, trans=kwargs.get("trans", None)):
            return True
        return item.user == user  # type: ignore[attr-defined]

    def is_accessible(self, item, user: User | None, **kwargs: Any) -> bool:
        """
        If the item is importable, is owned by `user`, or (the valid) `user`
        is in 'users shared with' list for the item: return True.
        """
        if item.importable:
            return True
        # note: owners always have access - checking for accessible implicitly checks for ownership
        if self.is_owner(item, user, **kwargs):
            return True
        if self.user_manager.is_anonymous(user):
            return False
        if user in item.users_shared_with_dot_users:
            return True
        return False

    # .... importable
    def make_importable(self, item, flush=True):
        """
        Makes item accessible--viewable and importable--and sets item's slug.
        Does not flush/commit changes, however. Item must have name, user,
        importable, and slug attributes.
        """
        self.create_unique_slug(item, flush=False)
        return self._session_setattr(item, "importable", True, flush=flush)

    def make_non_importable(self, item, flush=True):
        """
        Makes item accessible--viewable and importable--and sets item's slug.
        Does not flush/commit changes, however. Item must have name, user,
        importable, and slug attributes.
        """
        # item must be unpublished if non-importable
        if item.published:
            self.unpublish(item, flush=False)
        return self._session_setattr(item, "importable", False, flush=flush)

    # .... published
    def publish(self, item, flush=True):
        """
        Set both the importable and published flags on `item` to True.
        """
        # item must be importable to be published
        if not item.importable:
            self.make_importable(item, flush=False)
        return self._session_setattr(item, "published", True, flush=flush)

    def unpublish(self, item, flush=True):
        """
        Set the published flag on `item` to False.
        """
        return self._session_setattr(item, "published", False, flush=flush)

    def list_published(self, filters=None, **kwargs):
        """
        Return a list of all published items.
        """
        published_filter = self.model_class.table.c.published == true()
        filters = combine_lists(published_filter, filters)
        return self.list(filters=filters, **kwargs)

    # .... user sharing
    # sharing is often done via a 3rd table btwn a User and an item -> a <Item>UserShareAssociation
    def get_share_assocs(self, item, user=None):
        """
        Get the UserShareAssociations for the `item`.

        Optionally send in `user` to test for a single match.
        """
        query = self.query_associated(self.user_share_model, item)
        if user is not None:
            query = query.filter_by(user=user)
        return query.all()

    def share_with(self, item, user: User, flush: bool = True):
        """
        Get or create a share for the given user.
        """
        # precondition: user has been validated
        # get or create
        existing = self.get_share_assocs(item, user=user)
        if existing:
            return existing.pop(0)
        return self._create_user_share_assoc(item, user, flush=flush)

    def _create_user_share_assoc(self, item, user, flush=True):
        """
        Create a share for the given user.
        """
        user_share_assoc = self.user_share_model()
        self.session().add(user_share_assoc)
        self.associate(user_share_assoc, item)
        user_share_assoc.user = user

        # ensure an item slug so shared users can access
        if not item.slug:
            self.create_unique_slug(item)

        if flush:
            session = self.session()
            session.commit()
        return user_share_assoc

    def unshare_with(self, item, user: User, flush: bool = True):
        """
        Delete a user share from the database.
        """
        # Look for and delete sharing relation for user.
        user_share_assoc = self.get_share_assocs(item, user=user)[0]
        self.session().delete(user_share_assoc)
        if flush:
            session = self.session()
            session.commit()
        return user_share_assoc

    def _query_shared_with(self, user, eagerloads=True, **kwargs):
        """
        Return a query for this model already filtered to models shared
        with a particular user.
        """
        query = self.session().query(self.model_class).join(self.model_class.users_shared_with)
        if eagerloads is False:
            query = query.enable_eagerloads(False)
        # TODO: as filter in FilterParser also
        query = query.filter(self.user_share_model.user == user)
        return self._filter_and_order_query(query, **kwargs)

    def list_shared_with(self, user, filters=None, order_by=None, limit=None, offset=None, **kwargs):
        """
        Return a list of those models shared with a particular user.
        """
        # TODO: refactor out dupl-code btwn base.list
        orm_filters, fn_filters = self._split_filters(filters)
        if not fn_filters:
            # if no fn_filtering required, we can use the 'all orm' version with limit offset
            query = self._query_shared_with(
                user, filters=orm_filters, order_by=order_by, limit=limit, offset=offset, **kwargs
            )
            return self._orm_list(query=query, **kwargs)

        # fn filters will change the number of items returnable by limit/offset - remove them here from the orm query
        query = self._query_shared_with(user, filters=orm_filters, order_by=order_by, limit=None, offset=None, **kwargs)
        # apply limit and offset afterwards
        items = self._apply_fn_filters_gen(query.all(), fn_filters)
        return list(self._apply_fn_limit_offset_gen(items, limit, offset))

    def get_sharing_extra_information(
        self, trans: ProvidesUserContext, item, users: set[User], errors: set[str], option: SharingOptions | None = None
    ) -> ShareWithExtra | None:
        """Returns optional extra information about the shareability of the given item.

        This function should be overridden in the particular manager class that wants
        to provide the extra information, otherwise, it will be None by default."""
        return None

    def make_members_public(self, trans: ProvidesUserContext, item):
        """Make potential elements of this item public.

        This method must be overridden in managers that need to change permissions of internal elements
        contained associated with the given item.
        """

    def update_current_sharing_with_users(self, item, new_users_shared_with: set[User], flush=True):
        """Updates the currently list of users this item is shared with by adding new
        users and removing missing ones."""
        current_shares = self.get_share_assocs(item)
        currently_shared_with = {share.user for share in current_shares}

        needs_adding = new_users_shared_with - currently_shared_with
        for user in needs_adding:
            current_shares.append(self.share_with(item, user, flush=False))

        needs_removing = currently_shared_with - new_users_shared_with
        for user in needs_removing:
            current_shares.remove(self.unshare_with(item, user, flush=False))

        if flush:
            session = self.session()
            session.commit()
        return current_shares, needs_adding, needs_removing

    # .... auditing
    @cached_property
    def audit(self) -> AuditService:
        return audit_service_for(self.app)

    @property
    def share_action(self) -> SharingAction:
        return SHARE_ACTIONS[SHARABLE_TYPES[self.model_class]]

    @contextmanager
    def recording_sharing_change(self, item, change: SharingChange) -> Iterator[None]:
        """Record what the block's own commits changed about who can reach ``item``.

        Only values this request wrote and committed count. Comparing the item's committed
        state before and after would also take in another request's change made meanwhile,
        and credit it to this one, refused or not.
        """
        if not self.audit.wants(self.share_action):
            yield
            return
        before = self._read_sharing_state(audit_id(item))
        session = object_session(item)
        if before is None or session is None:
            yield
            return
        writes = _SharingWrites(item, self.user_share_model, self.foreign_key_name)
        listeners = (
            ("after_flush", writes.flushed),
            ("after_commit", writes.committed),
            ("after_rollback", writes.rolled_back),
        )
        for name, listener in listeners:
            event.listen(session, name, listener)
        try:
            yield
        finally:
            for name, listener in listeners:
                event.remove(session, name, listener)
            self._record_sharing_writes(change, before, writes)

    def _read_sharing_state(self, item_id: int | None) -> SharingState | None:
        """The committed sharing state of the item and its description, from a session of their own."""
        try:
            with audit_read_session(self.app.model.engine) as session:
                item = session.get(self.model_class, item_id) if item_id is not None else None
                if item is None:
                    raise exceptions.ObjectNotFound(f"No {self.model_class.__name__} {item_id}")
                target = self.audit.describe(item)
                assert target is not None
                user_ids = frozenset(share.user_id for share in item.users_shared_with)
                return SharingState(bool(item.importable), bool(item.published), item.slug, user_ids, target)
        except Exception:
            # The change itself must go ahead; a missing audit event is reported, not raised.
            audit_failures.report("prepare", "Could not read the sharing state of %s %s", self.share_action, item_id)
            return None

    def _record_sharing_writes(self, change: SharingChange, before: SharingState, writes: "_SharingWrites") -> None:
        try:
            values = writes.values
            after = SharingState(
                bool(values.get("importable", before.importable)),
                bool(values.get("published", before.published)),
                values.get("slug", before.slug),
                (before.user_ids | writes.users_added) - writes.users_removed,
                before.target,
            )
        except Exception:
            audit_failures.report("prepare", "Lost the audit event for a %s on %s", self.share_action, before.target.id)
            return
        if after.access() == before.access():
            return
        details = SharingChangeDetails(
            change=change,
            importable_before=before.importable,
            importable_after=after.importable,
            published_before=before.published,
            published_after=after.published,
            users_added=sorted(after.user_ids - before.user_ids),
            users_removed=sorted(before.user_ids - after.user_ids),
            slug_before=before.slug,
            slug_after=after.slug,
        )
        self.audit.record(self.share_action, before.target, "success", details=details)

    def record_sharing_denied(self, item_id: int, change: SharingChange) -> None:
        if not self.audit.wants(self.share_action):
            return
        try:
            encoded_id = self.app.security.encode_id(item_id)
        except Exception:
            encoded_id = None
        requested = AuditObject(type=SHARABLE_TYPES[self.model_class], id=item_id, encoded_id=encoded_id)
        self.audit.record(
            self.share_action,
            requested,
            "denied",
            details=SharingChangeDetails(change=change),
            reason="not_accessible",
        )

    # .... slugs
    # slugs are human readable strings often used to link to sharable resources (replacing ids)
    # TODO: as validator, deserializer, etc. (maybe another object entirely?)
    def set_slug(self, item, new_slug, user, flush=True):
        """
        Validate and set the new slug for `item`.
        """
        # precondition: has been validated
        if not SlugBuilder.is_valid_slug(new_slug):
            raise exceptions.RequestParameterInvalidException("Invalid slug", slug=new_slug)

        if item.slug == new_slug:
            return item

        session = self.session()

        # error if slug is already in use
        if slug_exists(session, item.__class__, user, new_slug):
            raise exceptions.Conflict("Slug already exists", slug=new_slug)

        item.slug = new_slug
        if flush:
            session.commit()
        return item

    def _default_slug_base(self, item):
        # override in subclasses
        if hasattr(item, "title"):
            return item.title.lower()
        return item.name.lower()

    def get_unique_slug(self, item):
        """
        Returns a slug that is unique among user's importable items
        for item's class.
        """
        cur_slug = item.slug

        # Setup slug base.
        if cur_slug is None or cur_slug == "":
            slug_base = slugify(self._default_slug_base(item), allow_unicode=True)
        else:
            slug_base = cur_slug

        # Using slug base, find a slug that is not taken. If slug is taken,
        # add integer to end.
        new_slug = slug_base
        count = 1
        while importable_item_slug_exists(self.session(), item.__class__, item.user, new_slug):
            # Slug taken; choose a new slug based on count. This approach can
            # handle numerous items with the same name gracefully.
            new_slug = f"{slug_base}-{count}"
            count += 1

        return new_slug

    def create_unique_slug(self, item, flush=True):
        """
        Set a new, unique slug on the item.
        """
        item.slug = self.get_unique_slug(item)
        self.session().add(item)
        if flush:
            session = self.session()
            session.commit()
        return item

    # TODO: def by_slug( self, user, **kwargs ):


class SharableModelSerializer(
    base.ModelSerializer,
    taggable.TaggableSerializerMixin,
    annotatable.AnnotatableSerializerMixin,
    ratable.RatableSerializerMixin,
):
    # TODO: stub
    SINGLE_CHAR_ABBR: str | None = None

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self.add_view(
            "sharing",
            [
                "id",
                "title",
                "email_hash",
                "importable",
                "published",
                "username",
                "username_and_slug",
                "users_shared_with",
            ],
        )

    def add_serializers(self):
        super().add_serializers()
        taggable.TaggableSerializerMixin.add_serializers(self)
        annotatable.AnnotatableSerializerMixin.add_serializers(self)
        ratable.RatableSerializerMixin.add_serializers(self)
        self.serializers.update(
            {
                "id": self.serialize_id,
                "title": self.serialize_title,
                "username": self.serialize_username,
                "username_and_slug": self.serialize_username_and_slug,
                "users_shared_with": self.serialize_users_shared_with,
                "email_hash": self.serialize_email_hash,
            }
        )
        # these use the default serializer but must still be white-listed
        self.serializable_keyset.update(["importable", "published", "slug"])

    def serialize_email_hash(self, item, key, **context):
        if not (item.user and item.user.email):
            return None
        return md5_hash_str(item.user.email)

    def serialize_title(self, item, key, **context):
        if hasattr(item, "title"):
            return item.title
        elif hasattr(item, "name"):
            return item.name

    def serialize_username(self, item, key, **context):
        return item.user and item.user.username

    def serialize_username_and_slug(self, item, key, **context):
        if not (item.user and item.user.username and item.slug and self.SINGLE_CHAR_ABBR):
            return None
        return ("/").join(("u", item.user.username, self.SINGLE_CHAR_ABBR, item.slug))

    # the only ones that needs any fns:
    #   user/user_id
    #   username_and_slug?

    def serialize_users_shared_with(self, item, key, user=None, **context):
        """
        Returns a list of encoded ids for users the item has been shared.

        Skipped if the requesting user is not the owner.
        """
        # TODO: still an open question as to whether key removal based on user
        # should be handled here or at a higher level (even if we didn't have to pass user (via thread context, etc.))
        if not self.manager.is_owner(item, user):
            self.skip()

        share_assocs = self.manager.get_share_assocs(item)
        return [self.serialize_id(share, "user_id") for share in share_assocs]


# Update keys that change who can reach an item.
SHARING_KEYS = frozenset({"published", "importable", "users_shared_with"})


class SharableModelDeserializer(
    base.ModelDeserializer,
    taggable.TaggableDeserializerMixin,
    annotatable.AnnotatableDeserializerMixin,
    ratable.RatableDeserializerMixin,
):
    def __init__(self, app: MinimalManagerApp, **kwargs):
        super().__init__(app, **kwargs)
        self.tag_handler = app.tag_handler

    def add_deserializers(self):
        super().add_deserializers()
        taggable.TaggableDeserializerMixin.add_deserializers(self)
        annotatable.AnnotatableDeserializerMixin.add_deserializers(self)
        ratable.RatableDeserializerMixin.add_deserializers(self)

        self.deserializers.update(
            {
                "published": self.deserialize_published,
                "importable": self.deserialize_importable,
                "users_shared_with": self.deserialize_users_shared_with,
            }
        )

    def deserialize(self, item, data, flush=True, **context):
        if not (flush and SHARING_KEYS.intersection(data)):
            return super().deserialize(item, data, flush=flush, **context)
        # users_shared_with commits on its own (taking any keys set before it along), so a
        # later key failing doesn't undo everything; the event says what actually committed.
        with self.manager.recording_sharing_change(item, "update"):
            return super().deserialize(item, data, flush=flush, **context)

    def deserialize_published(self, item, key, val, **context):
        """ """
        val = self.validate.bool(key, val)
        if item.published == val:
            return val

        if val:
            self.manager.publish(item, flush=False)
        else:
            self.manager.unpublish(item, flush=False)
        return item.published

    def deserialize_importable(self, item, key, val, **context):
        """ """
        val = self.validate.bool(key, val)
        if item.importable == val:
            return val

        if val:
            self.manager.make_importable(item, flush=False)
        else:
            self.manager.make_non_importable(item, flush=False)
        return item.importable

    # TODO: def deserialize_slug( self, item, val, **context ):

    def deserialize_users_shared_with(self, item, key, val, **context):
        """
        Accept a list of encoded user_ids, validate them as users, and then
        add or remove user shares in order to update the users_shared_with to
        match the given list finally returning the new list of shares.
        """
        unencoded_ids = [self.app.security.decode_id(id_) for id_ in val]
        new_users_shared_with = set(self.manager.user_manager.by_ids(unencoded_ids))
        current_shares, _, _ = self.manager.update_current_sharing_with_users(item, new_users_shared_with)
        # TODO: or should this return the list of ids?
        return current_shares


class SharableModelFilters(
    base.ModelFilterParser, taggable.TaggableFilterMixin, annotatable.AnnotatableFilterMixin, ratable.RatableFilterMixin
):
    def _add_parsers(self):
        super()._add_parsers()
        taggable.TaggableFilterMixin._add_parsers(self)
        annotatable.AnnotatableFilterMixin._add_parsers(self)
        ratable.RatableFilterMixin._add_parsers(self)

        self.orm_filter_parsers.update(
            {
                "importable": {"op": ("eq"), "val": base.parse_bool},
                "published": {"op": ("eq"), "val": base.parse_bool},
                "slug": {"op": ("eq", "contains", "like")},
                # chose by user should prob. only be available for admin? (most often we'll only need trans.user)
                # 'user'          : { 'op': ( 'eq' ), 'val': self.parse_id_list },
            }
        )


class SlugBuilder:
    """Builder for creating slugs out of items."""

    def create_item_slug(self, sa_session, item) -> bool:
        """Create/set item slug.

        Slug is unique among user's importable items for item's class.

        :param sa_session: Database session context.
        :param item: The item to create/update its slug.
        :type item: [type]
        :return: Returns true if item's slug was set/changed; false otherwise.
        :rtype: bool
        """
        cur_slug = item.slug

        # Setup slug base.
        if cur_slug is None or cur_slug == "":
            # Item can have either a name or a title.
            item_name = ""
            if hasattr(item, "name"):
                item_name = item.name
            elif hasattr(item, "title"):
                item_name = item.title
            slug_base = ready_name_for_url(item_name.lower())
        else:
            slug_base = cur_slug

        # Using slug base, find a slug that is not taken. If slug is taken,
        # add integer to end.
        new_slug = slug_base
        count = 1
        # Ensure unique across model class and user and don't include this item
        # in the check in case it has previously been assigned a valid slug.
        while another_slug_exists(sa_session, item.__class__, item.user, new_slug, item.id):
            # Slug taken; choose a new slug based on count. This approach can
            # handle numerous items with the same name gracefully.
            new_slug = f"{slug_base}-{count}"
            count += 1

        # Set slug and return.
        item.slug = new_slug
        return item.slug == cur_slug

    @classmethod
    def is_valid_slug(self, slug):
        """Returns true if slug is valid."""
        return slugify(slug, allow_unicode=True) == slug


def slug_exists(session, model_class, user, slug, ignore_deleted=False):
    stmt = select(exists().where(model_class.user == user).where(model_class.slug == slug))
    if ignore_deleted:  # Only check items that are NOT marked as deleted
        stmt = stmt.where(model_class.deleted == false())
    return session.scalar(stmt)


def importable_item_slug_exists(session, model_class, user, slug):
    stmt = select(
        exists().where(model_class.user == user).where(model_class.slug == slug).where(model_class.importable == true())
    )
    return session.scalar(stmt)


def another_slug_exists(session, model_class, user, slug, id):
    stmt = select(exists().where(model_class.user == user).where(model_class.slug == slug).where(model_class.id != id))
    return session.scalar(stmt)


__all__ = (
    "SharableModelDeserializer",
    "SharableModelFilters",
    "SharableModelManager",
    "SharableModelSerializer",
    "SharingOptions",
    "ShareWithExtra",
    "SlugBuilder",
)
