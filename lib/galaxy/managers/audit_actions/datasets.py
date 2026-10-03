"""Reading dataset content: display, download and related routes."""

from typing import (
    ClassVar,
    Literal,
)

from galaxy.managers.audit_actions.base import (
    AuditDetails,
    ObjectDescriber,
)


class DatasetContentDetails(AuditDetails):
    identifying_fields: ClassVar[frozenset[str]] = frozenset({"filename"})

    source: Literal["hda", "ldda"] = "hda"
    preview: bool = False
    raw: bool = False
    # Strings are clipped when the event is written, so a long request value never fails validation.
    to_ext: str | None = None
    # A file inside the dataset's extra files directory.
    filename: str | None = None
    offset: int | None = None
    ck_size: int | None = None
    # The HTTP Range header, for partial reads (IGV and friends make one request per range).
    http_range: str | None = None


DatasetAction = Literal["dataset.display", "dataset.download"]

ACTIONS: dict[str, type[AuditDetails]] = {
    "dataset.display": DatasetContentDetails,
    "dataset.download": DatasetContentDetails,
}

DESCRIBERS: list[ObjectDescriber] = []
