"""
API operations around galaxy.short_term_storage infrastructure.
"""

from typing import cast
from uuid import UUID

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security.utils import get_authorization_scheme_param

from galaxy.managers.audit import (
    AuditAttempt,
    AuditService,
    NULL_ATTEMPT,
)
from galaxy.managers.audit_actions import AuditObject
from galaxy.managers.audit_actions.exports import (
    ArchiveDownloadDetails,
    storage_request_digest,
)
from galaxy.managers.session import GalaxySessionManager
from galaxy.managers.users import UserManager
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.short_term_storage import (
    ShortTermStorageMonitor,
    ShortTermStorageServeCancelledInformation,
    ShortTermStorageServeCompletedInformation,
)
from galaxy.webapps.base.api import GalaxyFileResponse
from galaxy.webapps.base.audit import audited_response
from . import (
    api_key_cookie,
    api_key_header,
    api_key_query,
    depends,
    get_api_user,
    get_session,
    get_user,
    Router,
)

router = Router(tags=["short_term_storage"])


@router.cbv
class FastAPIShortTermStorage:
    short_term_storage_monitor: ShortTermStorageMonitor = depends(ShortTermStorageMonitor)  # type: ignore[type-abstract]  # https://github.com/python/mypy/issues/4717
    audit: AuditService = depends(AuditService)
    user_manager: UserManager = depends(UserManager)
    session_manager: GalaxySessionManager = depends(GalaxySessionManager)
    security: IdEncodingHelper = depends(IdEncodingHelper)

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
    def serve(self, storage_request_id: UUID, request: Request):
        requested = AuditObject(type="short_term_storage")
        attempt: AuditAttempt = NULL_ATTEMPT
        if self.audit.wants("archive.download"):
            self._note_downloader(request)
            # The id itself is a download link, so the event carries only its digest, which
            # the export event that asked for this storage also carries.
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

    def _note_downloader(self, request: Request) -> None:
        """Resolve the request identity the way authenticated routes do, for the audit event.

        The credentials are read from the request here rather than declared as security
        dependencies, which would mark this public route as authenticated in the API
        schema and do the work with auditing off. Only the identity is affected: a
        missing, unknown or refused credential leaves the request anonymous, as it always
        is to this route, and never fails the download.
        """
        try:
            galaxy_session = get_session(
                self.session_manager, self.security, request.cookies.get(api_key_cookie.model.name, "")
            )
            api_user = None
            if galaxy_session is None:
                scheme, credentials = get_authorization_scheme_param(request.headers.get("Authorization"))
                bearer_token = (
                    HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)
                    if scheme.lower() == "bearer" and credentials
                    else None
                )
                # run_as isn't honoured: it can't change what this route serves.
                api_user = get_api_user(
                    self.user_manager,
                    request.query_params.get(api_key_query.model.name, ""),
                    request.headers.get(api_key_header.model.name, ""),
                    # None is what the security dependency passes when there is no token.
                    cast(HTTPAuthorizationCredentials, bearer_token),
                    run_as=None,
                )
            get_user(galaxy_session, api_user)
        except Exception:
            pass
