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
    # The metadata element name the client asked for (e.g. bam_index); the datatype defines the valid ones.
    metadata_file: str


class ExtraFilesListDetails(AuditDetails):
    pass


class LibraryDownloadDetails(AuditDetails):
    # zip or uncompressed; one event per library dataset in the request.
    archive_format: str
    library_dataset_id: int | None = None
    # Set when the dataset was included by downloading a folder rather than named directly.
    folder_id: int | None = None


class ExternalLinkDetails(AuditDetails):
    """A link that lets a third-party site (a genome browser, say) fetch the dataset was handed out."""

    via: Literal["display_at", "display_application"]
    # Display application and link ids and the display_at site, as the request named them; on
    # success they match the admin-configured application that was used.
    app_name: str | None = None
    link_name: str | None = None
    site: str | None = None
    # Only the host the user is sent to; the rest of the URL can carry the dataset link itself.
    target_host: str | None = None
    # display_at grants the external host access unless the dataset is already public.
    public: bool | None = None


class ExternalFetchDetails(AuditDetails):
    """Content served for an external display, usually to the external site itself."""

    identifying_fields: ClassVar[frozenset[str]] = frozenset({"filename"})

    via: Literal["display_application", "display_as"]
    # For display_as, the display_app the content was formatted for.
    app_name: str | None = None
    link_name: str | None = None
    # display_as: rbac, or display_at for a host that display_at granted access to.
    authz_method: str | None = None
    app_action: str | None = None
    action_param: str | None = None
    # A file inside the dataset's extra files directory.
    filename: str | None = None
    # The user the link was made for. The request usually comes from the external site, so
    # the event's own user is often anonymous.
    link_user_id: int | None = None


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
    "dataset.external_link",
    "dataset.external_fetch",
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
    "dataset.external_link": ExternalLinkDetails,
    "dataset.external_fetch": ExternalFetchDetails,
    "library_dataset.download": LibraryDownloadDetails,
    "drs.object": DrsDetails,
    "drs.download": DrsDetails,
}

DESCRIBERS: list[ObjectDescriber] = []
