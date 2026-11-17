from galaxy.authnz.ras.config import ProtectedSource
from galaxy.authnz.ras.protected import (
    match_protected_source,
    ProtectedMatch,
)


def test_first_match_wins():
    sources = [
        ProtectedSource(url_regex=r"^drs://host/", phs_id="phs000001"),
        ProtectedSource(url_regex=r"^drs://host/obj", phs_id="phs000002"),
    ]
    assert match_protected_source(sources, "drs://host/obj") == ProtectedMatch("phs000001")


def test_named_group_supplies_the_accession():
    sources = [ProtectedSource(url_regex=r"^drs://host/(?P<phs_id>phs\d+)/")]
    assert match_protected_source(sources, "drs://host/phs000123/obj") == ProtectedMatch("phs000123")


def test_literal_accession():
    sources = [ProtectedSource(url_regex=r"^drs://host/", phs_id="phs000123")]
    assert match_protected_source(sources, "drs://host/obj") == ProtectedMatch("phs000123")


def test_entry_without_accession():
    sources = [ProtectedSource(url_regex=r"^drs://host/")]
    assert match_protected_source(sources, "drs://host/obj") == ProtectedMatch(None)


def test_no_match_is_none():
    sources = [ProtectedSource(url_regex=r"^drs://host/", phs_id="phs000123")]
    assert match_protected_source(sources, "drs://other/obj") is None
    assert match_protected_source([], "drs://host/obj") is None


def test_match_is_anchored_at_the_start():
    sources = [ProtectedSource(url_regex=r"drs://host/")]
    assert match_protected_source(sources, "https://x/?u=drs://host/") is None


def test_declared_group_that_captured_nothing_is_flagged():
    sources = [ProtectedSource(url_regex=r"^drs://host/(?:(?P<phs_id>phs\d+)/)?")]
    assert match_protected_source(sources, "drs://host/obj") == ProtectedMatch(None, study_not_captured=True)
    assert match_protected_source(sources, "drs://host/phs000123/obj") == ProtectedMatch("phs000123")


def test_scheme_and_host_case_is_ignored():
    sources = [ProtectedSource(url_regex=r"^drs://protected\.example\.org/", phs_id="phs000123")]
    assert match_protected_source(sources, "drs://Protected.Example.ORG/obj") == ProtectedMatch("phs000123")
    assert match_protected_source(sources, "DRS://protected.example.org/obj") == ProtectedMatch("phs000123")


def test_path_case_is_kept():
    sources = [ProtectedSource(url_regex=r"^drs://host/(?P<phs_id>phs\d+)/Obj$")]
    assert match_protected_source(sources, "drs://HOST/phs000123/Obj") == ProtectedMatch("phs000123")
    assert match_protected_source(sources, "drs://HOST/phs000123/obj") is None


def test_pattern_written_with_capitals_still_matches_as_written():
    # Lowercasing the URL must not stop a pattern that spells out a capitalised prefix.
    sources = [ProtectedSource(url_regex=r"^drs://dg\.ANV0:", phs_id="phs000123")]
    assert match_protected_source(sources, "drs://dg.ANV0:abc") == ProtectedMatch("phs000123")
