"""Which URLs need a passport, and for which study."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from galaxy.authnz.ras.config import ProtectedSource


@dataclass(frozen=True)
class ProtectedMatch:
    # None means any valid passport will do, unless study_not_captured is set.
    accession: str | None
    # The entry's pattern declares a phs_id group that took no part in this match. The admin
    # tied these URLs to a study, so no passport can be shown to cover it.
    study_not_captured: bool = False


_SCHEME_AND_AUTHORITY = re.compile(r"^([^:/?#]+://[^/?#]*)")


def _lowercase_scheme_and_authority(url: str) -> str:
    # Hostnames and schemes are case-insensitive, so DRS://Host/ must not slip past ^drs://host/.
    return _SCHEME_AND_AUTHORITY.sub(lambda m: m.group(1).lower(), url, count=1)


def match_protected_source(sources: Sequence[ProtectedSource], url: str) -> ProtectedMatch | None:
    normalized = _lowercase_scheme_and_authority(url)
    for source in sources:
        # Also try the URL as given, so a pattern that spells out capitals still matches it.
        match = re.match(source.url_regex, normalized) or re.match(source.url_regex, url)
        if match is None:
            continue
        if "phs_id" in match.re.groupindex:
            accession = match.group("phs_id") or None
            return ProtectedMatch(accession, study_not_captured=accession is None)
        return ProtectedMatch(source.phs_id)
    return None
