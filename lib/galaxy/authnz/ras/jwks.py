"""Fetch and cache an issuer's signing keys (JWKS)."""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import jwt
import requests

log = logging.getLogger(__name__)

JsonFetcher = Callable[[str], dict[str, Any]]

DEFAULT_HTTP_TIMEOUT = 20


def requests_json_fetcher(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=DEFAULT_HTTP_TIMEOUT, headers={"Accept": "application/json"})
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data


class JwksError(Exception):
    pass


class JwksUnavailableError(JwksError):
    """The issuer's keys couldn't be fetched, which says nothing about the token."""


class JwksClient:
    """Signing keys for one issuer, looked up by ``kid``.

    Keys are cached for ``cache_ttl`` seconds. A ``kid`` that isn't in the cache
    triggers one refetch (the issuer may have rotated). No fetch, successful or
    not, is retried within ``min_refresh_interval``, so neither a stream of made-up
    ``kid`` values nor an issuer outage turns into a stream of requests. When a
    refresh fails, the keys from the last good fetch keep being used.
    """

    def __init__(
        self,
        jwks_uri: str | Callable[[], str],
        fetcher: JsonFetcher = requests_json_fetcher,
        cache_ttl: float = 3600,
        min_refresh_interval: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._jwks_uri = jwks_uri
        self._fetcher = fetcher
        self._cache_ttl = cache_ttl
        self._min_refresh_interval = min_refresh_interval
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._last_refresh_failed = False
        self._lock = threading.Lock()

    @property
    def jwks_uri(self) -> str:
        return self._jwks_uri() if callable(self._jwks_uri) else self._jwks_uri

    def _refresh(self) -> None:
        self._attempted_at = self._clock()
        self._last_refresh_failed = True
        try:
            jwks = self._fetcher(self.jwks_uri)
        except (requests.RequestException, ValueError) as e:
            raise JwksUnavailableError(f"Could not fetch JWKS: {e}") from e
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in jwks.get("keys", []) if isinstance(jwks, dict) else []:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            # Only RSA signing keys can verify RS256; skip anything else rather than
            # failing the whole set.
            if not kid or jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig":
                continue
            try:
                keys[kid] = jwt.PyJWK(jwk, algorithm="RS256")
            except jwt.PyJWTError as e:
                log.warning("Skipping unusable JWKS key %s: %s", kid, e)
        self._keys = keys
        self._fetched_at = self._clock()
        self._last_refresh_failed = False

    def get_signing_key(self, kid: str | None) -> jwt.PyJWK:
        if not kid:
            raise JwksError("Token header has no kid")
        with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= self._cache_ttl
            throttled = self._attempted_at is not None and now - self._attempted_at < self._min_refresh_interval
            if (stale or kid not in self._keys) and not throttled:
                try:
                    self._refresh()
                except JwksUnavailableError as e:
                    if not self._keys:
                        raise
                    log.warning("Could not refresh JWKS, using the cached keys: %s", e)
            key = self._keys.get(kid)
            if key is None and self._last_refresh_failed:
                raise JwksUnavailableError(f"No cached signing key with kid {kid!r} and the JWKS can't be fetched")
        if key is None:
            raise JwksError(f"No signing key with kid {kid!r}")
        return key
