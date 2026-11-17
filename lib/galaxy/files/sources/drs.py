import logging
import re
from collections.abc import Sequence
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    TYPE_CHECKING,
)

from pydantic import (
    Field,
    field_validator,
)

from galaxy.exceptions import (
    AuthenticationRequired,
    FileSourceCredentialExpired,
)
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
from ._gen3 import gen3_covering_studies
from .util import fetch_drs_to_file

if TYPE_CHECKING:
    from galaxy.files import (
        GA4GHPassportCredential,
        OptionalUserContext,
    )

log = logging.getLogger(__name__)

# An index service on this machine may be plain http, as a test's is.
_LOCAL_HTTP_URL = re.compile(r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)")

CONNECT_HINT = "Connect your GA4GH Passport in User Preferences (/user/ga4gh_passport), then try again."
RECONNECT_HINT = "Reconnect your GA4GH Passport in User Preferences (/user/ga4gh_passport), then try again."


class DRSFileSourceTemplateConfiguration(BaseFileSourceTemplateConfiguration):
    # `url_regex` is not templated because it needs to be set at initialization with no RuntimeContext available.
    url_regex: str = r"^drs://"
    force_http: bool | TemplateExpansion = False
    http_headers: dict[str, str] | TemplateExpansion = {}
    # Send the user's GA4GH Passport to the DRS server (DRS 1.4 PassportAuth), as
    # controlled-access repositories such as dbGaP require: POSTed to the access endpoint,
    # and to the object endpoint only after a plain GET of the object is refused. The
    # download from the signed URL never gets it.
    ga4gh_passport: bool | TemplateExpansion = False
    # The index service of the Gen3 commons this source fetches from
    # (https://<commons>/index/index). Galaxy asks it which dbGaP study and consent group
    # cover an object fetched with a Passport, so that the dataset is tied to the user's
    # grant for that study rather than to all of their grants. Not templated: it is used
    # when the fetch is submitted, before the source is resolved for a user.
    gen3_index_url: str | None = None

    @field_validator("gen3_index_url")
    @classmethod
    def _gen3_index_over_https(cls, value: str | None) -> str | None:
        # Nothing signs the answer, and it decides which grant a dataset depends on.
        if value is not None and not value.startswith("https://") and not _LOCAL_HTTP_URL.match(value):
            raise ValueError("gen3_index_url must be an https:// URL")
        return value

    # Filled in when the source is serialized for a job, which has no user to look the passport
    # up for; not for configuration.
    passports: list[str] = Field(default=[], repr=False)


class DRSFileSourceConfiguration(BaseFileSourceConfiguration):
    url_regex: str = r"^drs://"
    force_http: bool = False
    http_headers: dict[str, str] = {}
    ga4gh_passport: bool = False
    gen3_index_url: str | None = None
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
    def sends_ga4gh_passport(self) -> bool:
        """Whether fetches from this source carry the user's GA4GH Passport.

        A templated value counts as yes: data from it is then treated as
        controlled, which errs on the safe side.
        """
        return bool(self.template_config.ga4gh_passport)

    def covering_studies(self, urls: Sequence[str]) -> dict[str, list[str]]:
        """The dbGaP studies the repository says cover the objects at ``urls``.

        A URL it doesn't say for is left out, as are all of them when this
        source has no way to ask (``gen3_index_url``).
        """
        index_url = self.template_config.gen3_index_url
        if not index_url:
            return {}
        return gen3_covering_studies(index_url, urls)

    @property
    def _allowlist(self):
        return self._file_sources_config.fetch_url_allowlist

    def to_dict(self, for_serialization=False, user_context: "OptionalUserContext" = None) -> dict[str, Any]:
        rval = super().to_dict(for_serialization=for_serialization, user_context=user_context)
        if not for_serialization:
            return rval
        if rval.get("ga4gh_passport"):
            credential = self._passport_credential(user_context)
            if credential is not None:
                # The job realizing this source has no user context: give it the Passport, and
                # the time after which it must not send it.
                rval["passports"] = [credential.passport]
                expires_at = credential.fresh_until
                if rval.get("auth_expires_at"):
                    expires_at = min(expires_at, datetime.fromisoformat(rval["auth_expires_at"]))
                rval["auth_expires_at"] = expires_at.isoformat()
                log.info(
                    "GA4GH Passport added to DRS file source %s for user %s txn=%s fresh_until=%s",
                    self.id,
                    user_context.username if user_context else None,
                    credential.txn,
                    credential.fresh_until.isoformat(),
                )
        return rval

    def _realize_to(
        self, source_path: str, native_path: str, context: FilesSourceRuntimeContext[DRSFileSourceConfiguration]
    ):
        user_context = context.user_data.context if context.user_data.context else None
        config = context.config
        headers = dict(config.http_headers)
        passports = list(config.passports)
        if not passports and config.ga4gh_passport and (credential := self._passport_credential(user_context)):
            # Realized in this process (not in a job): use the user's Passport directly.
            self._check_passport_fresh(credential.fresh_until)
            passports = [credential.passport]
        try:
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
        except AuthenticationRequired as e:
            if not config.ga4gh_passport:
                raise
            message = str(e).rstrip()
            if not message.endswith((".", "!", "?")):
                message += "."
            raise AuthenticationRequired(f"{message} {RECONNECT_HINT if passports else CONNECT_HINT}") from e

    def _check_credentials_fresh(self) -> None:
        if self._auth_expires_at and self.template_config.ga4gh_passport and self.template_config.passports:
            self._check_passport_fresh(self._auth_expires_at)
        super()._check_credentials_fresh()

    @staticmethod
    def _check_passport_fresh(fresh_until: datetime) -> None:
        if datetime.now(timezone.utc) > fresh_until:
            raise FileSourceCredentialExpired(
                f"Your GA4GH Passport is too old to send to a data repository. {RECONNECT_HINT}"
            )

    @staticmethod
    def _passport_credential(user_context: "OptionalUserContext") -> "GA4GHPassportCredential | None":
        if user_context is None or user_context.anonymous:
            return None
        return user_context.ga4gh_passport

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
