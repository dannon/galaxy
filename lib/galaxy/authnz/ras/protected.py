"""Which URLs need a passport, and for which study."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from galaxy.authnz.ras.config import ProtectedSource


@dataclass(frozen=True)
class ProtectedMatch:
    # None means any valid passport will do.
    accession: str | None


def match_protected_source(sources: Sequence[ProtectedSource], url: str) -> ProtectedMatch | None:
    for source in sources:
        match = re.match(source.url_regex, url)
        if match is None:
            continue
        if "phs_id" in match.re.groupindex:
            return ProtectedMatch(match.group("phs_id"))
        return ProtectedMatch(source.phs_id)
    return None
