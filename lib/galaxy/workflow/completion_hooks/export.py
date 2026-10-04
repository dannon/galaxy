"""
Export workflow completion hook.

This hook exports completed workflow invocations to a configured
file source (e.g., remote storage).
"""

import logging

__all__ = ("ExportToFileSourceHook",)

from typing import (
    Any,
    TYPE_CHECKING,
)

from galaxy.managers.audit import (
    audit_failures,
    audit_id,
    AuditOutcome,
    AuditReason,
    AuditService,
    classify_failure,
    NULL_PREPARED,
    PreparedEvent,
)
from galaxy.managers.audit_actions.exports import sanitize_target_uri
from galaxy.managers.export_audit import store_export_details
from galaxy.managers.export_tracker import StoreExportTracker
from galaxy.schema.schema import (
    ExportObjectType,
    ModelStoreFormat,
)
from galaxy.schema.tasks import (
    RequestUser,
    WriteInvocationTo,
)
from galaxy.web.framework.request_scope import (
    request_scope,
    RequestIdentity,
)
from galaxy.workflow.completion_hooks.base import WorkflowCompletionHook

if TYPE_CHECKING:
    from galaxy.model import (
        WorkflowInvocation,
        WorkflowInvocationCompletion,
    )

log = logging.getLogger(__name__)


class ExportToFileSourceHook(WorkflowCompletionHook):
    """
    Hook that exports completed workflow invocations to a configured file source.

    Configuration is provided in the on_complete action object. The configuration
    should include:

    - target_uri: (required) Galaxy Files URI to export to
    - format: (optional) Model store format, defaults to "rocrate.zip"
    - include_files: (optional) Include files in export, defaults to True
    - include_hidden: (optional) Include hidden datasets, defaults to False
    - include_deleted: (optional) Include deleted datasets, defaults to False

    Example on_complete action:
    {
        "export_to_file_source": {
            "target_uri": "gxfiles://my_storage/exports/",
            "format": "rocrate.zip",
            "include_files": true
        }
    }
    """

    plugin_type = "export_to_file_source"

    def execute(self, completion: "WorkflowInvocationCompletion") -> None:
        """
        Export the completed invocation to the configured file source.

        Args:
            completion: The completion record.
        """
        invocation = completion.workflow_invocation
        export_config = self._get_export_config(invocation)

        if not export_config:
            log.debug(
                "No export configuration found for invocation %d",
                invocation.id,
            )
            return

        # Build the export request
        target_uri = export_config.get("target_uri")
        if not target_uri:
            log.warning(
                "Export configuration missing target_uri for invocation %d",
                invocation.id,
            )
            return

        # Parse format, default to rocrate.zip
        format_str = export_config.get("format", "rocrate.zip")
        try:
            model_store_format = ModelStoreFormat(format_str)
        except ValueError:
            log.warning(
                "Invalid export format '%s' for invocation %d, using rocrate.zip",
                format_str,
                invocation.id,
            )
            model_store_format = ModelStoreFormat.ROCRATE_ZIP

        # Build user context - user is on the history, not the invocation
        history = invocation.history
        if not history:
            log.warning(
                "No history associated with invocation %d, cannot export",
                invocation.id,
            )
            return

        user = history.user
        if not user:
            log.warning(
                "No user associated with invocation %d, cannot export",
                invocation.id,
            )
            return

        # Before the export association's commit, while describing the invocation is a read
        # on this session that no commit has expired.
        audit_event = self._prepare_audit(invocation, user.id)

        # Get galaxy URL for RO-Crate metadata
        galaxy_url = self.app.config.galaxy_infrastructure_url or ""

        # Create export association for tracking in "Recent Exports"
        export_tracker = StoreExportTracker(self.app)
        export_association = export_tracker.create_export_association(
            object_id=invocation.id,
            object_type=ExportObjectType.INVOCATION,
        )

        request = WriteInvocationTo(
            invocation_id=invocation.id,
            target_uri=target_uri,
            model_store_format=model_store_format,
            include_files=export_config.get("include_files", True),
            include_hidden=export_config.get("include_hidden", False),
            include_deleted=export_config.get("include_deleted", False),
            user=RequestUser(user_id=user.id),
            galaxy_url=galaxy_url,
            export_association_id=export_association.id,
        )

        log.info(
            "Queuing export of invocation %d to %s (format: %s, export_association_id: %d)",
            invocation.id,
            # The URI as given can hold credentials for the file source.
            sanitize_target_uri(target_uri),
            model_store_format,
            export_association.id,
        )

        # Import here to avoid circular import (celery.tasks imports completion_hooks)
        from galaxy.celery.tasks import write_invocation_to

        # Queue the export via Celery task
        # The task handles dependency injection for ModelStoreManager
        try:
            result = write_invocation_to.delay(request=request, task_user_id=user.id)
        except Exception as exc:
            self._audit_export(audit_event, request, *classify_failure(exc))
            raise
        # Before the commit below: the export is under way whether or not that succeeds.
        self._audit_export(audit_event, request, "success", task_id=result.id)

        # Store the task UUID for tracking
        export_association.task_uuid = result.id
        self.app.model.context.commit()

    def _prepare_audit(self, invocation: "WorkflowInvocation", owner_id: int) -> PreparedEvent:
        """The export's event, as the invocation's owner, the only identity this hook has.

        No request asked for this export and no credential is presented: the owner asked
        for it when submitting the workflow, and it runs with their permissions. So the
        event is background work (auth method "task") for the owner as effective user,
        with no actor and no credential. Never raises.
        """
        try:
            audit = self.app[AuditService]
            if not audit.wants("invocation.export"):
                return NULL_PREPARED
            with request_scope() as scope:
                scope.identity = RequestIdentity("task", user_id=owner_id)
                return audit.prepare("invocation.export", invocation)
        except Exception:
            audit_failures.report(
                "prepare", "Failed to prepare the export event of invocation %s", audit_id(invocation)
            )
            return NULL_PREPARED

    def _audit_export(
        self,
        audit_event: PreparedEvent,
        request: WriteInvocationTo,
        outcome: AuditOutcome,
        reason: AuditReason | None = None,
        task_id: str | None = None,
    ) -> None:
        """Record the export; ``trigger`` says where it came from. Never raises."""
        try:
            audit_event.record(
                outcome,
                details=lambda: store_export_details(request, request.target_uri).model_copy(
                    update={"task_id": task_id, "trigger": "workflow_completion"}
                ),
                reason=reason,
                stage="prepare",
            )
        except Exception:
            audit_failures.report("prepare", "Failed to record the export of invocation %s", request.invocation_id)

    def _get_export_config(self, invocation) -> "dict[str, Any] | None":
        """
        Extract export configuration from invocation on_complete actions.

        Args:
            invocation: The workflow invocation.

        Returns:
            The export configuration dict if found, None otherwise.
        """
        on_complete = invocation.on_complete or []
        for action in on_complete:
            if isinstance(action, dict) and "export_to_file_source" in action:
                return action["export_to_file_source"]
        return None

    def is_applicable(self, completion: "WorkflowInvocationCompletion") -> bool:
        """
        Only export if requested in on_complete and configuration is present.

        Args:
            completion: The completion record.

        Returns:
            True if export was requested and configuration is present.
        """
        invocation = completion.workflow_invocation
        # Configuration is embedded in the on_complete action object
        return self._get_export_config(invocation) is not None
