"""The optional ``ras_config_file``. No file means RAS passport support is off."""

import logging
import os
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
    ValidationInfo,
)

from galaxy.authnz.ras.verify import DEFAULT_LEEWAY

log = logging.getLogger(__name__)

DEFAULT_ISSUER = "https://stsstg.nih.gov"


class RasConfigError(Exception):
    pass


def _require_https(value: str | None, name: str) -> str | None:
    if value is not None and not value.startswith("https://"):
        raise ValueError(f"{name} must be an https URL")
    return value


class DirectRasSettings(BaseModel):
    """Client settings for talking to RAS directly. For development only."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    # Prefer client_secret_env so the secret stays out of the config file.
    client_secret: str | None = None
    client_secret_env: str = "RAS_CLIENT_SECRET"
    redirect_uri: str
    scopes: str = "openid profile email ga4gh_passport_v1"

    def resolve_client_secret(self) -> str:
        secret = self.client_secret or os.environ.get(self.client_secret_env)
        if not secret:
            raise RasConfigError(f"No RAS client secret: set client_secret or ${self.client_secret_env}")
        return secret


class RasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str = DEFAULT_ISSUER
    # Looked up through the issuer's discovery document when not given.
    jwks_uri: str | None = None
    # The bridge application users are sent to when Galaxy needs a passport.
    bridge_url: str | None = None
    # Query parameter carrying Galaxy's return address to the bridge.
    return_param: str = "return_to"
    # Where in Galaxy the bridge sends users back to when the caller doesn't say.
    return_path: str = "/"
    # Galaxy's browser-facing base URL; defaults to galaxy_infrastructure_url.
    galaxy_url: str | None = None
    leeway: int = DEFAULT_LEEWAY
    store: Literal["vault", "tapis_vault"] = "vault"
    direct_ras: DirectRasSettings | None = None

    @field_validator("issuer", "jwks_uri", "bridge_url")
    @classmethod
    def _https(cls, value: str | None, info: ValidationInfo) -> str | None:
        return _require_https(value, info.field_name or "url")

    @field_validator("issuer")
    @classmethod
    def _strip_issuer(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("leeway")
    @classmethod
    def _leeway_range(cls, value: int) -> int:
        if not 0 <= value <= 600:
            raise ValueError("leeway must be between 0 and 600 seconds")
        return value

    @model_validator(mode="after")
    def _return_path_is_local(self) -> "RasConfig":
        if not self.return_path.startswith("/") or self.return_path.startswith("//"):
            raise ValueError("return_path must be a path on this Galaxy, starting with a single /")
        return self


def load_ras_config(path: str | None) -> RasConfig | None:
    """Parse ``path``, or return ``None`` (feature off) if it isn't set or doesn't exist."""
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise RasConfigError(f"{path} must contain a mapping")
    try:
        return RasConfig(**raw)
    except ValueError as e:
        raise RasConfigError(f"Invalid RAS config in {path}: {e}") from e
