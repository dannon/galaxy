"""Reading dataset content: display, download and every other route that hands out its bytes."""

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


class DownloadUrlDetails(AuditDetails):
    """A link to the backing store was handed out; the bytes leave without passing through Galaxy.

    The URL itself is a bearer credential, so only facts about it are kept: where it
    points and how long it works for.
    """

    source: Literal["hda", "ldda"] = "hda"
    to_ext: str | None = None
    object_store_id: str | None = None
    url_host: str | None = None
    # Read from the signed URL's own expiry parameter when the store uses a known scheme.
    expires_in: int | None = None


class DatasetDataDetails(AuditDetails):
    """Content read through a datatype for a visualization or structured view."""

    source: Literal["hda", "ldda"] = "hda"
    # /api/datasets/{id}?data_type=... or /api/datasets/{id}/content/{content_type}.
    data_type: str | None = None
    content_type: str | None = None


class MetadataFileDetails(AuditDetails):
    # The metadata element name (e.g. bam_index), set by the datatype rather than the user.
    metadata_file: str


class ExtraFilesListDetails(AuditDetails):
    pass


class LibraryDownloadDetails(AuditDetails):
    # zip or uncompressed; one event per library dataset in the request.
    archive_format: str
    library_dataset_id: int | None = None
    # Set when the dataset was included by downloading a folder rather than named directly.
    folder_id: int | None = None


class DrsDetails(AuditDetails):
    # The DRS object id as the client sent it (hda-<id> or ldda-<id>); no secrets in it.
    object_id: str


DatasetAction = Literal[
    "dataset.display",
    "dataset.download",
    "dataset.download_url",
    "dataset.download_metadata_file",
    "dataset.list_extra_files",
    "dataset.read_text",
    "dataset.read_data",
    "library_dataset.download",
    "drs.object",
    "drs.download",
]

ACTIONS: dict[str, type[AuditDetails]] = {
    "dataset.display": DatasetContentDetails,
    "dataset.download": DatasetContentDetails,
    "dataset.download_url": DownloadUrlDetails,
    "dataset.download_metadata_file": MetadataFileDetails,
    "dataset.list_extra_files": ExtraFilesListDetails,
    "dataset.read_text": DatasetContentDetails,
    "dataset.read_data": DatasetDataDetails,
    "library_dataset.download": LibraryDownloadDetails,
    "drs.object": DrsDetails,
    "drs.download": DrsDetails,
}

DESCRIBERS: list[ObjectDescriber] = []
