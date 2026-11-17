import logging
import re
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
)

from pydantic import Field

from galaxy.exceptions import RasPassportRequiredException
from galaxy.files.models import (
    BaseFileSourceConfiguration,
    BaseFileSourceTemplateConfiguration,
    FilesSourceRuntimeContext,
)
from galaxy.util.config_templates import TemplateExpansion
from . import (
    BaseFilesSource,
    PluginKind,
)
from .util import fetch_drs_to_file

if TYPE_CHECKING:
    from galaxy.files import OptionalUserContext

log = logging.getLogger(__name__)


class DRSFileSourceTemplateConfiguration(BaseFileSourceTemplateConfiguration):
    # `url_regex` is not templated because it needs to be set at initialization with no RuntimeContext available.
    url_regex: str = r"^drs://"
    force_http: bool | TemplateExpansion = False
    http_headers: dict[str, str] | TemplateExpansion = {}
    # Send the user's NIH RAS passport with DRS requests to this source.
    ras_passport: bool = False
    # Filled in when the source is serialized for a job, which has no user to look the passport up for.
    passports: list[str] = Field(default=[], repr=False)


class DRSFileSourceConfiguration(BaseFileSourceConfiguration):
    url_regex: str = r"^drs://"
    force_http: bool = False
    http_headers: dict[str, str] = {}
    ras_passport: bool = False
    passports: list[str] = Field(default=[], repr=False)


class DRSFilesSource(BaseFilesSource[DRSFileSourceTemplateConfiguration, DRSFileSourceConfiguration]):
    plugin_type = "drs"
    plugin_kind = PluginKind.drs

    template_config_class = DRSFileSourceTemplateConfiguration
    resolved_config_class = DRSFileSourceConfiguration

    def __init__(self, template_config: DRSFileSourceTemplateConfiguration):
        defaults = dict(
            id="_drs",
            label="DRS file",
            doc="DRS file handler",
            writable=False,
        )
        template_config = self._apply_defaults_to_template(defaults, template_config)
        super().__init__(template_config)
        assert self.template_config.url_regex, "DRSFilesSource requires a url_regex to be set in the configuration"
        self._url_regex = re.compile(self.template_config.url_regex)

    @property
    def _allowlist(self):
        return self._file_sources_config.fetch_url_allowlist

    def to_dict(self, for_serialization=False, user_context: "OptionalUserContext" = None) -> dict[str, Any]:
        rval = super().to_dict(for_serialization=for_serialization, user_context=user_context)
        if for_serialization and self.template_config.ras_passport and user_context is not None:
            if passport := user_context.ras_passport:
                rval["passports"] = [passport]
        return rval

    def _compute_auth_expires_at(self, user_context: "OptionalUserContext") -> datetime | None:
        expires_at = super()._compute_auth_expires_at(user_context)
        if not self.template_config.ras_passport or user_context is None:
            return expires_at
        passport_expires_at = user_context.ras_passport_expires_at
        if expires_at is None or passport_expires_at is None:
            return expires_at or passport_expires_at
        return min(expires_at, passport_expires_at)

    def _realize_to(
        self, source_path: str, native_path: str, context: FilesSourceRuntimeContext[DRSFileSourceConfiguration]
    ):
        user_context = context.user_data.context if context.user_data.context else None
        config = context.config
        headers = dict(config.http_headers)
        passports = list(config.passports)
        if not passports and config.ras_passport:
            if user_context is not None and user_context.ras_passport:
                passports = [user_context.ras_passport]
            else:
                raise RasPassportRequiredException("A NIH RAS passport is required to access this data.")
        fetch_drs_to_file(
            source_path,
            native_path,
            user_context=user_context,
            fetch_url_allowlist=self._allowlist,
            headers=headers or None,
            force_http=config.force_http,
            metadata_out=context.metadata_out,
            passports=passports or None,
        )

    def _write_from(
        self, target_path: str, native_path: str, context: FilesSourceRuntimeContext[DRSFileSourceConfiguration]
    ):
        raise NotImplementedError()

    def score_url_match(self, url: str):
        if match := self._url_regex.match(url):
            return match.span()[1]
        else:
            return 0


__all__ = ("DRSFilesSource",)
