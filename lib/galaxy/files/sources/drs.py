import logging
import re
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import (
    Any,
    TYPE_CHECKING,
)

from pydantic import Field

from galaxy.exceptions import (
    AuthenticationRequired,
    FileSourceCredentialExpired,
    RasPassportRequiredException,
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
from .util import fetch_drs_to_file

if TYPE_CHECKING:
    from galaxy.files import (
        GA4GHPassportCredential,
        OptionalUserContext,
    )

log = logging.getLogger(__name__)

CONNECT_HINT = "Connect your GA4GH Passport in User Preferences (/user/ga4gh_passport), then try again."
RECONNECT_HINT = "Reconnect your GA4GH Passport in User Preferences (/user/ga4gh_passport), then try again."


class DRSFileSourceTemplateConfiguration(BaseFileSourceTemplateConfiguration):
    # `url_regex` is not templated because it needs to be set at initialization with no RuntimeContext available.
    url_regex: str = r"^drs://"
    force_http: bool | TemplateExpansion = False
    http_headers: dict[str, str] | TemplateExpansion = {}
    # POST the user's NIH RAS passport to this source's access endpoint, and to the object
    # endpoint only after a plain GET of the object is refused. The download never gets it.
    ras_passport: bool = False
    # Send the user's GA4GH Passport to the DRS server (DRS 1.4 PassportAuth), as
    # controlled-access repositories such as dbGaP require.
    ga4gh_passport: bool | TemplateExpansion = False
    # Filled in when the source is serialized for a job, which has no user to look the passport
    # up for; not for configuration.
    passports: list[str] = Field(default=[], repr=False)


class DRSFileSourceConfiguration(BaseFileSourceConfiguration):
    url_regex: str = r"^drs://"
    force_http: bool = False
    http_headers: dict[str, str] = {}
    ras_passport: bool = False
    ga4gh_passport: bool = False
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

    @property
    def _allowlist(self):
        return self._file_sources_config.fetch_url_allowlist

    def to_dict(self, for_serialization=False, user_context: "OptionalUserContext" = None) -> dict[str, Any]:
        rval = super().to_dict(for_serialization=for_serialization, user_context=user_context)
        if not for_serialization:
            return rval
        if self.template_config.ras_passport and user_context is not None:
            if passport := user_context.ras_passport:
                rval["passports"] = [passport]
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

    def _compute_auth_expires_at(self, user_context: "OptionalUserContext") -> datetime | None:
        expires_at = super()._compute_auth_expires_at(user_context)
        if not self.template_config.ras_passport or user_context is None:
            return expires_at
        if user_context.ras_passport_expired:
            # The store has already dropped the passport, so its own deadline is gone; any past
            # time makes the job fail as credential-expired rather than as passport-required.
            return datetime.now(timezone.utc) - timedelta(seconds=1)
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
            elif user_context is not None and user_context.ras_passport_expired:
                raise FileSourceCredentialExpired()
            else:
                raise RasPassportRequiredException()
        elif not passports and config.ga4gh_passport and (credential := self._passport_credential(user_context)):
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
