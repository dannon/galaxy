"""Ask a Gen3 commons which dbGaP study and consent group cover a DRS object.

DRS has no field for this. A Gen3 commons serves DRS from its index service,
though, and an object's index record is public: it lists the resources a user
must be authorized for (``authz``, by default ``/programs/<phs>.<consent>``),
or on a record without those a legacy access list (``acl``).
"""

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from galaxy.util import requests
from .util import (
    drs_uri_is_plain,
    LOCAL_HOST_AND_PORT_DRS_REST,
)

log = logging.getLogger(__name__)

INDEX_REQUEST_TIMEOUT = 10
# The fetch waits on the answer: all of one source's lookups together get this long.
INDEX_LOOKUP_BUDGET = 20
# Object ids travel in the query string; this keeps it to a few kilobytes.
INDEX_BATCH_SIZE = 50
_STUDY_AND_CONSENT = re.compile(r"^phs\d{1,6}\.c\d+$", re.IGNORECASE)
# How Gen3 renders a visa's study and consent group as a resource by default, under any namespace.
# A commons can map a study to some other path, so nothing else is taken for one.
_STUDY_RESOURCE = re.compile(r"^(?:/[^/]+)*/programs/(phs\d{1,6}\.c\d+)/?$", re.IGNORECASE)
_OBJECT_ID = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")


@dataclass(frozen=True)
class Gen3Object:
    """The object a DRS URI names, as a Gen3 index service would be asked for it."""

    object_id: str
    # drs://<host>/<id> names the commons by its host, drs://<prefix>:<id> by its prefix.
    host: str | None = None
    prefix: str | None = None


def gen3_object(drs_uri: str) -> Gen3Object | None:
    """The object ``drs_uri`` names, or None if the URI doesn't name one plainly."""
    if not drs_uri.startswith("drs://") or not drs_uri_is_plain(drs_uri):
        return None
    rest = drs_uri[len("drs://") :]
    host, slash, path = rest.partition("/")
    if slash and (":" not in host or LOCAL_HOST_AND_PORT_DRS_REST.match(rest)):
        named = Gen3Object(path, host=host.lower())
    elif not slash and ":" in rest:
        prefix, object_id = rest.split(":", 1)
        named = Gen3Object(object_id, prefix=prefix.lower())
    else:
        # Reads as either form. The fetch settles it by resolving the prefix; guessing here
        # could look up a different object than the one fetched.
        return None
    if not _OBJECT_ID.match(named.object_id) or named.prefix == "":
        return None
    return named


def gen3_record_studies(record: Any) -> list[str] | None:
    """The dbGaP studies (``phs000001.c1``) an index record says its object needs, or None if it doesn't say.

    This follows what the commons enforces. A record with ``authz`` needs every
    resource in it, so it only says when each one is a study. A record without
    ``authz`` falls back to the legacy ``acl``, where any one entry is enough,
    so it only says when that names a single study.
    """
    if not isinstance(record, dict):
        return None
    authz = _strings(record.get("authz"))
    if authz:
        studies = [match.group(1).lower() for resource in authz if (match := _STUDY_RESOURCE.match(resource))]
        return list(dict.fromkeys(studies)) if len(studies) == len(authz) else None
    studies = list(
        dict.fromkeys(entry.lower() for entry in _strings(record.get("acl")) if _STUDY_AND_CONSENT.match(entry))
    )
    return studies if len(studies) == 1 else None


def gen3_covering_studies(index_url: str, drs_uris: Sequence[str]) -> dict[str, list[str]]:
    """The dbGaP studies covering the objects at ``drs_uris``, as the index service at ``index_url`` has them.

    Best effort: a URI whose record can't be read, or names no study, is left
    out, as is one that names a host other than the index's own.
    """
    index_host = urlsplit(index_url).netloc.lower()
    named = {
        uri: named_object
        for uri in drs_uris
        if (named_object := gen3_object(uri)) is not None and named_object.host in (None, index_host)
    }
    object_ids = list(dict.fromkeys(named_object.object_id for named_object in named.values()))
    records: list[dict[str, Any]] = []
    deadline = time.monotonic() + INDEX_LOOKUP_BUDGET
    for start in range(0, len(object_ids), INDEX_BATCH_SIZE):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("Stopped asking the Gen3 index at %s after %s seconds", index_url, INDEX_LOOKUP_BUDGET)
            break
        batch = _index_records(
            index_url, object_ids[start : start + INDEX_BATCH_SIZE], min(INDEX_REQUEST_TIMEOUT, remaining)
        )
        if batch is None:
            # Don't spend a timeout on every remaining batch.
            break
        records.extend(batch)
    covering: dict[str, list[str]] = {}
    for uri, named_object in named.items():
        matching = [record for record in records if _is_record_of(record, named_object)]
        if len(matching) == 1 and (studies := gen3_record_studies(matching[0])):
            covering[uri] = studies
    return covering


def _index_records(index_url: str, object_ids: Sequence[str], timeout: float) -> list[dict[str, Any]] | None:
    """The index records of ``object_ids``, or None if the service gave no usable answer."""
    try:
        response = requests.get(
            index_url.rstrip("/"),
            params={"ids": ",".join(object_ids)},
            headers={"Accept": "application/json"},
            timeout=timeout,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        records = response.json()["records"]
    except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
        log.warning(
            "The Gen3 index at %s did not say which studies cover %d object(s): %s", index_url, len(object_ids), e
        )
        return None
    return [record for record in records if isinstance(record, dict)] if isinstance(records, list) else None


def _is_record_of(record: dict[str, Any], named: Gen3Object) -> bool:
    """Whether ``record`` is the index's record of the object ``named``.

    The index knows an object with or without the commons' prefix (``dg.4503/``),
    so the record's id may carry a prefix the URI left out -- but never a
    different one from the URI's, and nothing else may differ.
    """
    did = record.get("did")
    if not isinstance(did, str):
        return False
    if did == named.object_id:
        return True
    prefix, slash, bare = did.partition("/")
    if not slash or bare != named.object_id or "/" in named.object_id:
        return False
    return named.prefix is None or prefix.lower() == named.prefix


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
