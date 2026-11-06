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
    triggers one refetch (the issuer may have rotated), but no more often than
    ``min_refresh_interval`` so a stream of tokens with made-up ``kid`` values can't
    turn into a stream of requests to the issuer.
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
        self._lock = threading.Lock()

    @property
    def jwks_uri(self) -> str:
        return self._jwks_uri() if callable(self._jwks_uri) else self._jwks_uri

    def _refresh(self) -> None:
        try:
            jwks = self._fetcher(self.jwks_uri)
        except (requests.RequestException, ValueError) as e:
            raise JwksUnavailableError(f"Could not fetch JWKS: {e}") from e
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in jwks.get("keys", []) if isinstance(jwks, dict) else []:
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

    def get_signing_key(self, kid: str | None) -> jwt.PyJWK:
        if not kid:
            raise JwksError("Token header has no kid")
        with self._lock:
            now = self._clock()
            if self._fetched_at is None or now - self._fetched_at >= self._cache_ttl:
                self._refresh()
            elif kid not in self._keys and now - self._fetched_at >= self._min_refresh_interval:
                log.debug("kid %s not in cached JWKS, refetching", kid)
                self._refresh()
            key = self._keys.get(kid)
        if key is None:
            raise JwksError(f"No signing key with kid {kid!r}")
        return key
