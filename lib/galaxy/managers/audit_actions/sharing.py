"""Data changing hands inside Galaxy: sharing, publishing, permissions and imports."""

from typing import (
    Any,
    ClassVar,
    Literal,
)

from galaxy import model
from galaxy.managers.audit_actions.base import (
    AuditDetails,
    AuditObject,
    ObjectDescriber,
)

SharingChange = Literal[
    "share_with_users",
    "enable_link_access",
    "disable_link_access",
    "publish",
    "unpublish",
    "set_slug",
    # Several attributes at once, through the item's update route.
    "update",
]


class SharingChangeDetails(AuditDetails):
    """Who can reach a history, workflow, page or visualization, before and after."""

    # Slugs are user-chosen and usually derived from the item's name.
    identifying_fields: ClassVar[frozenset[str]] = frozenset({"slug_before", "slug_after"})

    change: SharingChange
    importable_before: bool | None = None
    importable_after: bool | None = None
    published_before: bool | None = None
    published_after: bool | None = None
    users_added: list[int] = []
    users_removed: list[int] = []
    slug_before: str | None = None
    slug_after: str | None = None


DatasetPermissionsChange = Literal[
    # Explicit role lists through the permissions routes.
    "set_permissions",
    "remove_restrictions",
    "make_private",
    # Side effects of sharing or publishing the history that holds the dataset.
    "make_public",
    "share_privately",
]


PermissionsVia = Literal["permissions", "history_sharing"]


class DatasetPermissionsDetails(AuditDetails):
    change: DatasetPermissionsChange
    via: PermissionsVia = "permissions"
    # None (left out of the event) when not known, so an empty list always means "no roles":
    # for access, a public dataset.
    access_roles_before: list[int] | None = None
    access_roles_after: list[int] | None = None
    manage_roles_before: list[int] | None = None
    manage_roles_after: list[int] | None = None
    # Library datasets only.
    modify_roles_before: list[int] | None = None
    modify_roles_after: list[int] | None = None
    # A user needs every access role, so dropping one can let more users in. Only access
    # roles are considered; a new manage role grants the power to widen later instead.
    may_widen_access: bool | None = None


class HistoryImportDetails(AuditDetails):
    """A user copied a history they don't own. The event's object is the source history."""

    new_history_id: int | None = None
    recipient_id: int | None = None
    all_datasets: bool = False


class DatasetCopyDetails(AuditDetails):
    """A dataset was copied into a history owned by someone other than the source's owner."""

    new_hda_id: int | None = None
    target_history_id: int | None = None
    recipient_id: int | None = None


SharingAction = Literal[
    "history.share",
    "workflow.share",
    "page.share",
    "visualization.share",
    "history.import",
    "dataset.permissions",
    "dataset.copy",
]

ACTIONS: dict[str, type[AuditDetails]] = {
    "history.share": SharingChangeDetails,
    "workflow.share": SharingChangeDetails,
    "page.share": SharingChangeDetails,
    "visualization.share": SharingChangeDetails,
    "history.import": HistoryImportDetails,
    "dataset.permissions": DatasetPermissionsDetails,
    "dataset.copy": DatasetCopyDetails,
}

# The audit object type of each sharable model, which is also its action prefix.
SHARABLE_TYPES: dict[type, Literal["history", "workflow", "page", "visualization"]] = {
    model.History: "history",
    model.StoredWorkflow: "workflow",
    model.Page: "page",
    model.Visualization: "visualization",
}

SHARE_ACTIONS: dict[str, SharingAction] = {
    "history": "history.share",
    "workflow": "workflow.share",
    "page": "page.share",
    "visualization": "visualization.share",
}


def describe_sharable(audit_service: Any, obj: Any) -> AuditObject | None:
    object_type = next((name for cls, name in SHARABLE_TYPES.items() if isinstance(obj, cls)), None)
    if object_type is None:
        return None
    name = obj.title if object_type in ("page", "visualization") else obj.name
    # Object fields aren't clipped when the event is written; 256 is the audit module's MAX_STRING_LENGTH.
    name = name[:256] if audit_service.include_names and name else None
    is_history = object_type == "history"
    return AuditObject(
        type=object_type,
        id=obj.id,
        encoded_id=audit_service.security.encode_id(obj.id) if obj.id is not None else None,
        # A history's own id and name also fill the history fields, so one query on them finds
        # events about the history and about what's in it.
        history_id=obj.id if is_history else None,
        owner_id=obj.user_id,
        name=name,
        history_name=name if is_history else None,
    )


DESCRIBERS: list[ObjectDescriber] = [describe_sharable]
