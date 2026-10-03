"""
API operations around galaxy.short_term_storage infrastructure.
"""

from uuid import UUID

from galaxy.managers.audit import AuditService
from galaxy.managers.audit_actions import AuditObject
from galaxy.managers.audit_actions.exports import (
    ArchiveDownloadDetails,
    storage_request_digest,
)
from galaxy.short_term_storage import (
    ShortTermStorageMonitor,
    ShortTermStorageServeCancelledInformation,
    ShortTermStorageServeCompletedInformation,
)
from galaxy.webapps.base.api import GalaxyFileResponse
from galaxy.webapps.base.audit import audited_response
from . import (
    depends,
    Router,
)

router = Router(tags=["short_term_storage"])


@router.cbv
class FastAPIShortTermStorage:
    short_term_storage_monitor: ShortTermStorageMonitor = depends(ShortTermStorageMonitor)  # type: ignore[type-abstract]  # https://github.com/python/mypy/issues/4717
    audit: AuditService = depends(AuditService)

    @router.get(
        "/api/short_term_storage/{storage_request_id}/ready",
        summary="Determine if specified storage request ID is ready for download.",
        response_description="Boolean indicating if the storage is ready.",
        public=True,
    )
    def is_ready(self, storage_request_id: UUID) -> bool:
        storage_target = self.short_term_storage_monitor.recover_target(storage_request_id)
        return self.short_term_storage_monitor.is_ready(storage_target)

    @router.get(
        "/api/short_term_storage/{storage_request_id}",
        public=True,
        summary="Serve the staged download specified by request ID.",
        response_description="Raw contents of the file.",
        response_class=GalaxyFileResponse,
        responses={
            200: {
                "description": "The archive file containing the History.",
            },
            204: {
                "description": "Request was cancelled without an exception condition recorded.",
            },
        },
    )
    def serve(self, storage_request_id: UUID):
        # The id itself is a download link, so the event carries only its digest, which
        # the export event that asked for this storage also carries.
        requested = AuditObject(type="short_term_storage")
        attempt = self.audit.attempt(
            "archive.download",
            requested,
            ArchiveDownloadDetails(
                source="short_term_storage", storage_request_digest=storage_request_digest(storage_request_id)
            ),
        )
        with attempt.guard():
            storage_target = self.short_term_storage_monitor.recover_target(storage_request_id)
            serve_info = self.short_term_storage_monitor.get_serve_info(storage_target)
            attempt.authorized(requested)
            if isinstance(serve_info, ShortTermStorageServeCompletedInformation):
                response = GalaxyFileResponse(
                    path=serve_info.target.path,
                    media_type=serve_info.mime_type,
                    filename=serve_info.filename,
                )
                return audited_response(response, attempt)

            assert isinstance(serve_info, ShortTermStorageServeCancelledInformation)
            # The task that was to build the archive failed or was cancelled; nothing is served.
            attempt.failed("archive_failed")
            raise serve_info.message_exception
