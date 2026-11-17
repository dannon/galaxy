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


def match_protected_source(sources: Sequence[ProtectedSource], url: str) -> ProtectedMatch | None:
    for source in sources:
        match = re.match(source.url_regex, url)
        if match is None:
            continue
        if "phs_id" in match.re.groupindex:
            accession = match.group("phs_id") or None
            return ProtectedMatch(accession, study_not_captured=accession is None)
        return ProtectedMatch(source.phs_id)
    return None
