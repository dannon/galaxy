"""Asking a Gen3 index service which dbGaP study and consent group cover a DRS object."""

import json
from typing import Any
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

from galaxy.files import (
    ConfiguredFileSources,
    ConfiguredFileSourcesConf,
)
from galaxy.files.plugins import FileSourcePluginsConfig
from galaxy.files.sources import _gen3
from galaxy.files.sources._gen3 import (
    gen3_covering_studies,
    gen3_object,
    gen3_record_studies,
    Gen3Object,
)
from galaxy.files.sources.drs import DRSFilesSource

HOST = "commons.example.org"
INDEX = f"https://{HOST}/index/index"
UUID_1 = "00002039-279d-4265-9060-82bfc4f03652"
UUID_2 = "00000073-27e1-4dcd-bfdc-e458c31feec2"


@pytest.mark.parametrize(
    "drs_uri, named",
    [
        (f"drs://{HOST}/{UUID_1}", Gen3Object(UUID_1, host=HOST)),
        (f"drs://Commons.Example.org/dg.4503/{UUID_1}", Gen3Object(f"dg.4503/{UUID_1}", host=HOST)),
        (f"drs://DG.4503:{UUID_1}", Gen3Object(UUID_1, prefix="dg.4503")),
        ("drs://127.0.0.1:8080/obj1", Gen3Object("obj1", host="127.0.0.1:8080")),
        ("drs://localhost:8080/obj1", Gen3Object("obj1", host="localhost:8080")),
        # A host with a port and a compact identifier with a slash look alike; the fetch tells
        # them apart by resolving the prefix.
        (f"drs://{HOST}:8443/obj1", None),
        (f"drs://dg.4503:dg.4503/{UUID_1}", None),
        # The fetch would not ask for the path these spell out.
        (f"drs://{HOST}/other/../obj1", None),
        (f"drs://{HOST}/./obj1", None),
        (f"drs://{HOST}/dg.4503%2Fobj1", None),
        (f"drs://{HOST}/obj1?x=1", None),
        (f"drs://{HOST}/obj1,obj2", None),
        (f"drs://{HOST}/obj 1", None),
        (f"drs://{HOST}/", None),
        (f"drs://{HOST}", None),
        ("drs://dg.4503:", None),
        ("drs://:obj1", None),
        (f"https://{HOST}/{UUID_1}", None),
    ],
)
def test_object_named_by_a_drs_uri(drs_uri, named):
    assert gen3_object(drs_uri) == named


@pytest.mark.parametrize(
    "authz, acl, studies",
    [
        # The default resource path for a study, here with an acl that agrees.
        (["/programs/phs000178.c1"], ["phs000178.c1"], ["phs000178.c1"]),
        (["/programs/phs002409.c1"], [], ["phs002409.c1"]),
        (["/orgA/programs/PHS000178.C1/"], ["*"], ["phs000178.c1"]),
        # Every resource is needed.
        (["/programs/phs000178.c1", "/programs/phs000235.c3"], [], ["phs000178.c1", "phs000235.c3"]),
        (["/programs/phs000178.c1", "/programs/phs000178.c1"], [], ["phs000178.c1"]),
        (["/programs/phs000178.c1", "/programs/topmed/projects/X"], ["phs000178.c1"], None),
        # A commons that names its resources something else: the acl is not what it enforces,
        # so the acl's study is not taken, and neither is a study-shaped name somewhere in a path.
        (["/programs/topmed/projects/COPDGene_HMB"], ["admin", "phs000951.c1"], None),
        (["/programs/topmed/projects/phs000951.c1"], [], None),
        (["/studies/phs000178.c1"], [], None),
        (["/programs/phs000178"], ["phs000178.c1"], None),
        (["/open"], ["*"], None),
        # No authz: the commons falls back to the acl, where any one entry is enough.
        ([], ["admin", "phs001138.c1"], ["phs001138.c1"]),
        ([], ["PHS001138.C1", "phs001138.c1"], ["phs001138.c1"]),
        ([], ["phs000001.c1", "phs000002.c1"], None),
        ([], ["admin", "TOPMed_Common_Exchange_Area"], None),
        ([], ["phs000178"], None),
        ([], ["*"], None),
        ([], [], None),
        (None, None, None),
        ("/programs/phs000178.c1", "phs000178.c1", None),
        ([{"resource": "/programs/phs000178.c1"}], [7], None),
    ],
)
def test_record_studies(authz, acl, studies):
    assert gen3_record_studies({"did": UUID_1, "authz": authz, "acl": acl}) == studies


@pytest.mark.parametrize("record", [None, "phs000178.c1", [], {}])
def test_record_studies_of_something_that_is_not_a_record(record):
    assert gen3_record_studies(record) is None


def record(did: str, study: str) -> dict[str, Any]:
    return {"did": did, "authz": [f"/programs/{study}"], "acl": []}


def serve(*records: dict[str, Any]) -> None:
    """An index service that answers ``?ids=`` for an id with or without its prefix, as indexd does."""

    def callback(request):
        ids = parse_qs(urlsplit(request.url).query)["ids"][0].split(",")
        tails = {requested.rsplit("/", 1)[-1] for requested in ids}
        found = [r for r in records if r["did"].rsplit("/", 1)[-1] in tails]
        return 200, {}, json.dumps({"records": found, "ids": ids})

    responses.add_callback(responses.GET, INDEX, callback=callback)


def requested_urls() -> list[str]:
    return [str(call.request.url) for call in responses.calls]


def asked() -> list[list[str]]:
    return [parse_qs(urlsplit(url).query)["ids"][0].split(",") for url in requested_urls()]


@responses.activate
def test_covering_studies_by_uri():
    serve(record(f"dg.4503/{UUID_1}", "phs000951.c1"), record(UUID_2, "phs000178.c1"), record("open", "open"))
    uris = [
        f"drs://dg.4503:{UUID_1}",
        f"drs://{HOST}/{UUID_2}",
        f"drs://{HOST}/open",
        f"drs://{HOST}/missing",
        f"drs://{HOST}:8443/unreadable",
    ]
    assert gen3_covering_studies(f"{INDEX}/", uris) == {uris[0]: ["phs000951.c1"], uris[1]: ["phs000178.c1"]}
    assert asked() == [[UUID_1, UUID_2, "open", "missing"]]
    assert not requested_urls()[0].startswith(f"{INDEX}/")


@pytest.mark.parametrize(
    "drs_uri, dids, study",
    [
        (f"drs://{HOST}/{UUID_1}", [UUID_1], "phs000001.c1"),
        # The index knows the object under its prefix, which the URI may leave out.
        (f"drs://{HOST}/{UUID_1}", [f"dg.4503/{UUID_1}"], "phs000001.c1"),
        (f"drs://{HOST}/dg.4503/{UUID_1}", [f"dg.4503/{UUID_1}"], "phs000001.c1"),
        (f"drs://dg.4503:{UUID_1}", [UUID_1], "phs000001.c1"),
        (f"drs://dg.4503:{UUID_1}", [f"DG.4503/{UUID_1}"], "phs000001.c1"),
        # A record under another prefix is another commons' object.
        (f"drs://dg.4503:{UUID_1}", [f"dg.other/{UUID_1}"], None),
        (f"drs://dg.4503:{UUID_1}", [f"dg.4503/{UUID_1}", f"dg.other/{UUID_1}"], "phs000001.c1"),
        # Only a single prefix on the record may differ, and only when the URI has none.
        (f"drs://{HOST}/dg.4503/{UUID_1}", [UUID_1], None),
        (f"drs://{HOST}/dg.4503/{UUID_1}", [f"dg.other/{UUID_1}"], None),
        (f"drs://{HOST}/ns/{UUID_1}", [UUID_1, f"dg.4503/ns/{UUID_1}"], None),
        (f"drs://{HOST}/{UUID_1}", [f"a/b/{UUID_1}"], None),
        (f"drs://{HOST}/{UUID_1}", [f"x{UUID_1}"], None),
        # Two records for one object: the index can't have meant both.
        (f"drs://{HOST}/{UUID_1}", [f"dg.4503/{UUID_1}", f"dg.other/{UUID_1}"], None),
    ],
)
@responses.activate
def test_covering_studies_only_takes_the_record_of_the_object_asked_for(drs_uri, dids, study):
    # The index answers with whatever it likes; the study must come from the object's own record.
    responses.add(responses.GET, INDEX, json={"records": [record(did, "phs000001.c1") for did in dids]})
    assert gen3_covering_studies(INDEX, [drs_uri]) == ({drs_uri: [study]} if study else {})


@responses.activate
def test_covering_studies_only_asks_about_objects_on_the_index_host():
    serve(record("obj1", "phs000001.c1"))
    here, elsewhere, compact = f"drs://{HOST}/obj1", "drs://other.example.org/obj1", "drs://dg.4503:obj1"
    assert gen3_covering_studies(INDEX, [elsewhere]) == {}
    assert asked() == []
    # A compact identifier names no host; its prefix is checked against the record's instead.
    assert gen3_covering_studies(INDEX, [here, elsewhere, compact]) == {
        here: ["phs000001.c1"],
        compact: ["phs000001.c1"],
    }
    assert asked() == [["obj1"]]


@responses.activate
def test_covering_studies_asks_in_batches_and_once_per_object(monkeypatch):
    monkeypatch.setattr(_gen3, "INDEX_BATCH_SIZE", 2)
    serve(*(record(f"obj{n}", f"phs00000{n}.c1") for n in range(1, 4)))
    uris = [f"drs://{HOST}/obj{n}" for n in (1, 2, 1, 3)]
    assert gen3_covering_studies(INDEX, uris) == {
        f"drs://{HOST}/obj1": ["phs000001.c1"],
        f"drs://{HOST}/obj2": ["phs000002.c1"],
        f"drs://{HOST}/obj3": ["phs000003.c1"],
    }
    assert asked() == [["obj1", "obj2"], ["obj3"]]


@responses.activate
def test_covering_studies_stops_asking_after_a_batch_fails(monkeypatch):
    monkeypatch.setattr(_gen3, "INDEX_BATCH_SIZE", 1)
    responses.add(responses.GET, INDEX, json={"records": [record("obj1", "phs000001.c1")]})
    responses.add(responses.GET, INDEX, status=503)
    uris = [f"drs://{HOST}/obj{n}" for n in (1, 2, 3)]
    assert gen3_covering_studies(INDEX, uris) == {uris[0]: ["phs000001.c1"]}
    assert asked() == [["obj1"], ["obj2"]]


@responses.activate
def test_covering_studies_stops_asking_when_its_time_is_up(monkeypatch):
    monkeypatch.setattr(_gen3, "INDEX_BATCH_SIZE", 1)
    # The clock is read once for the deadline and once before each batch.
    clock = iter([0.0, 0.0, _gen3.INDEX_LOOKUP_BUDGET - 1.0, _gen3.INDEX_LOOKUP_BUDGET])
    monkeypatch.setattr("galaxy.files.sources._gen3.time.monotonic", lambda: next(clock))
    serve(*(record(f"obj{n}", f"phs00000{n}.c1") for n in range(1, 4)))
    uris = [f"drs://{HOST}/obj{n}" for n in (1, 2, 3)]
    assert gen3_covering_studies(INDEX, uris) == {uris[0]: ["phs000001.c1"], uris[1]: ["phs000002.c1"]}
    assert asked() == [["obj1"], ["obj2"]]


@responses.activate
def test_covering_studies_asks_nothing_without_a_usable_id():
    assert gen3_covering_studies(INDEX, [f"drs://{HOST}:8443/obj1", f"drs://{HOST}/other/../obj1"]) == {}
    assert asked() == []


@pytest.mark.parametrize(
    "response",
    [
        {"status": 500, "json": {"error": "boom"}},
        {"status": 403, "json": {"records": [record("obj1", "phs000001.c1")]}},
        # Not followed: the answer has to come from the service the admin named.
        {"status": 302, "headers": {"Location": "https://elsewhere.example.org/index"}},
        {"status": 200, "body": "<html>not json</html>"},
        {"status": 200, "json": []},
        {"status": 200, "json": {"records": "none"}},
        {"status": 200, "json": {"records": [None, "obj1", {"did": 7}, {"authz": ["/programs/phs000001.c1"]}]}},
        {"body": RequestsConnectionError("unreachable")},
    ],
    ids=["500", "refused", "redirect", "not json", "no records key", "records not a list", "odd records", "down"],
)
@responses.activate
def test_covering_studies_is_empty_when_the_index_gives_no_usable_answer(response):
    responses.add(responses.GET, INDEX, **response)
    assert gen3_covering_studies(INDEX, [f"drs://{HOST}/obj1"]) == {}
    assert len(responses.calls) == 1


def drs_file_sources(*sources: dict[str, Any]) -> ConfiguredFileSources:
    conf = [{"type": "drs", "ga4gh_passport": True, **source} for source in sources]
    return ConfiguredFileSources(FileSourcePluginsConfig(), ConfiguredFileSourcesConf(conf_dict=conf))


@responses.activate
def test_drs_file_source_asks_its_gen3_index():
    serve(record("obj1", "phs000001.c1"))
    file_sources = drs_file_sources(
        {"id": "commons", "url_regex": r"^drs://commons\.example\.org/", "gen3_index_url": INDEX},
        {"id": "other", "url_regex": r"^drs://other\.example\.org/"},
    )
    uri, other = f"drs://{HOST}/obj1", "drs://other.example.org/obj1"
    commons_source, other_source = file_sources.find_best_match(uri), file_sources.find_best_match(other)
    assert isinstance(commons_source, DRSFilesSource) and isinstance(other_source, DRSFilesSource)
    assert commons_source.covering_studies([uri]) == {uri: ["phs000001.c1"]}
    # No index to ask: nothing is known, and nothing is requested.
    assert other_source.covering_studies([other]) == {}
    assert asked() == [["obj1"]]


@pytest.mark.parametrize(
    "index_url, accepted",
    [
        (INDEX, True),
        ("http://127.0.0.1:8080/index/index", True),
        ("http://localhost/index/index", True),
        # The answer is not signed, so it must not travel in the clear.
        ("http://commons.example.org/index/index", False),
        ("http://127.0.0.1.example.org/index/index", False),
        ("commons.example.org/index/index", False),
    ],
)
def test_gen3_index_url_must_be_https_unless_local(index_url, accepted):
    source = {"id": "commons", "url_regex": r"^drs://commons\.example\.org/", "gen3_index_url": index_url}
    if accepted:
        assert drs_file_sources(source).find_best_match(f"drs://{HOST}/obj1") is not None
    else:
        with pytest.raises(ValueError, match="gen3_index_url must be an https:// URL"):
            drs_file_sources(source)
