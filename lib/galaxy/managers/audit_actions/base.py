"""Types shared by every family of audit actions."""

from collections.abc import Callable
from typing import (
    Any,
    ClassVar,
)

from pydantic import (
    BaseModel,
    ConfigDict,
)


class AuditDetails(BaseModel):
    """Action-specific facts. Subclasses list fields explicitly; nothing else is serialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Fields that can carry user-chosen names; dropped unless include_names is on.
    identifying_fields: ClassVar[frozenset[str]] = frozenset()


class AuditObject(BaseModel):
    """What was acted on, described well enough to stand alone.

    Log consumers cannot join back to Galaxy's database, so each event carries the
    identifiers someone investigating would search for.
    """

    type: str
    id: int | None = None
    encoded_id: str | None = None
    uuid: str | None = None
    dataset_id: int | None = None
    history_id: int | None = None
    owner_id: int | None = None
    # Only with include_names.
    name: str | None = None
    history_name: str | None = None


# A describer gets the AuditService (for id encoding and include_names) and a model
# object, and returns None when the object isn't one it knows.
ObjectDescriber = Callable[[Any, Any], AuditObject | None]
