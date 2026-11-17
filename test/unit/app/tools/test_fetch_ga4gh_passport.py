"""Data fetched with a GA4GH Passport is restricted from the moment its dataset is created."""

from types import SimpleNamespace
from typing import Any
from urllib.parse import (
    parse_qs,
    urlsplit,
)

import pytest
import responses

from galaxy import exceptions
from galaxy.authnz.ras.config import ProtectedSource
from galaxy.files import (
    ConfiguredFileSources,
    ConfiguredFileSourcesConf,
)
from galaxy.files.plugins import FileSourcePluginsConfig
from galaxy.security import RBACAgent
from galaxy.tools.actions import (
    upload,
    upload_common,
)

ACTIONS = RBACAgent.permitted_actions
CONTROLLED = "drs://controlled.example.org/obj1"
CONTROLLED_2 = "drs://controlled.example.org/obj2"
OPEN = "drs://open.example.org/obj3"
INDEXED = "drs://indexed.example.org/obj1"
INDEXED_2 = "drs://indexed.example.org/obj2"
INDEX = "https://indexed.example.org/index/index"
FILE_SOURCES: list[dict[str, Any]] = [
    {"type": "drs", "id": "controlled", "url_regex": "^drs://controlled.example.org/", "ga4gh_passport": True},
    {
        "type": "drs",
        "id": "indexed",
        "url_regex": "^drs://indexed.example.org/",
        "ga4gh_passport": True,
        "gen3_index_url": INDEX,
    },
    {"type": "drs", "id": "open", "url_regex": "^drs://open.example.org/"},
]


GRANT_1, GRANT_2 = SimpleNamespace(name="ga4gh_grant:7:phs1.c1"), SimpleNamespace(name="ga4gh_grant:7:phs2.c1")
MANAGE = SimpleNamespace(name="ga4gh_controlled_data")


class FakePassportManager:
    def __init__(self, error: Exception | None = None, passport: bool = True, grant_roles=(GRANT_1, GRANT_2)):
        self.error = error
        self.passport = passport
        self.grant_roles = list(grant_roles)
        self.calls = 0

    def fetch_roles(self, user):
        self.calls += 1
        if self.error:
            raise self.error
        return (self.grant_roles, MANAGE) if self.passport and user is not None else None


class FakeApp:
    def __init__(self, manager: FakePassportManager, protected_sources=None):
        self.ras_passport_gate = None
        if protected_sources is not None:
            self.ras_passport_gate = SimpleNamespace(config=SimpleNamespace(protected_sources=protected_sources))
        self.file_sources = ConfiguredFileSources(
            FileSourcePluginsConfig(), ConfiguredFileSourcesConf(conf_dict=FILE_SOURCES)
        )
        self.security_agent = SimpleNamespace(permitted_actions=ACTIONS)
        self.manager = manager

    def __getitem__(self, cls):
        return self.manager


def trans(manager=None, user=True, protected_sources=None):
    app = FakeApp(manager or FakePassportManager(), protected_sources)
    return SimpleNamespace(user=SimpleNamespace(id=7) if user else None, app=app)


def restricted_to(*grant_roles):
    return {ACTIONS.DATASET_ACCESS: list(grant_roles), ACTIONS.DATASET_MANAGE_PERMISSIONS: [MANAGE]}


def index_knows(*objects: tuple[str, str]) -> None:
    """Serve a Gen3 index holding ``(object id, study)`` records at ``INDEX``."""
    records = [{"did": did, "authz": [f"/programs/{study}"], "acl": []} for did, study in objects]
    responses.add(responses.GET, INDEX, json={"records": records})


def index_asked() -> list[list[str]]:
    return [parse_qs(urlsplit(str(call.request.url)).query)["ids"][0].split(",") for call in responses.calls]


def hdas(*urls, **item_extra):
    return {"destination": {"type": "hdas"}, "elements": [{"src": "url", "url": u, **item_extra} for u in urls]}


def request(*targets):
    return {"targets": list(targets)}


def test_no_passport_uri_keeps_default_permissions():
    t = trans()
    assert upload._ga4gh_passport_permissions(t, request(hdas(OPEN, "https://example.org/x"))) is None
    assert t.app.manager.calls == 0


def test_passport_uri_of_unknown_study_restricted_to_all_grant_roles_managed_by_no_one():
    permissions = upload._ga4gh_passport_permissions(trans(), request(hdas(CONTROLLED, OPEN)))
    assert permissions == {CONTROLLED: restricted_to(GRANT_1, GRANT_2)}


def test_passport_uri_restricted_to_the_grant_role_of_the_study_its_protected_source_names():
    protected_sources = [
        ProtectedSource(url_regex=r"^drs://controlled\.example\.org/obj1$", phs_id="phs000002.c1"),
        ProtectedSource(url_regex=r"^drs://controlled\.example\.org/(?P<phs_id>phs\d+)/"),
        # Names no study: any passport will do, and Galaxy can't tell which grant covers the data.
        ProtectedSource(url_regex=r"^drs://controlled\.example\.org/"),
    ]
    in_url, not_held = "drs://controlled.example.org/phs000001/obj", "drs://controlled.example.org/phs000009/obj"
    t = trans(protected_sources=protected_sources)
    permissions = upload._ga4gh_passport_permissions(t, request(hdas(CONTROLLED, in_url, not_held, CONTROLLED_2)))
    assert permissions == {
        CONTROLLED: restricted_to(GRANT_2),
        in_url: restricted_to(GRANT_1),
        not_held: restricted_to(GRANT_1, GRANT_2),
        CONTROLLED_2: restricted_to(GRANT_1, GRANT_2),
    }
    assert t.app.manager.calls == 1


@responses.activate
def test_passport_uri_restricted_to_the_grant_roles_of_the_studies_its_repository_names():
    index_knows(("obj1", "phs000002.c1"))
    target = hdas(INDEXED, INDEXED_2, INDEXED, CONTROLLED, OPEN)
    permissions = upload._ga4gh_passport_permissions(trans(), request(target))
    assert permissions == {
        INDEXED: restricted_to(GRANT_2),
        INDEXED_2: restricted_to(GRANT_1, GRANT_2),
        CONTROLLED: restricted_to(GRANT_1, GRANT_2),
    }
    # One question, about the objects of the source that has an index to ask.
    assert index_asked() == [["obj1", "obj2"]]


@responses.activate
def test_protected_source_study_is_used_without_asking_the_repository():
    index_knows(("obj1", "phs000002.c1"))
    t = trans(protected_sources=[ProtectedSource(url_regex=r"^drs://indexed\.example\.org/", phs_id="phs1")])
    assert upload._ga4gh_passport_permissions(t, request(hdas(INDEXED))) == {INDEXED: restricted_to(GRANT_1)}
    assert index_asked() == []


@responses.activate
def test_repository_is_asked_when_the_protected_source_names_no_study():
    index_knows(("obj1", "phs000002.c1"))
    t = trans(protected_sources=[ProtectedSource(url_regex=r"^drs://indexed\.example\.org/")])
    assert upload._ga4gh_passport_permissions(t, request(hdas(INDEXED))) == {INDEXED: restricted_to(GRANT_2)}
    assert index_asked() == [["obj1"]]


@responses.activate
def test_nothing_is_asked_when_the_user_holds_a_single_grant():
    index_knows(("obj1", "phs000002.c1"))
    t = trans(FakePassportManager(grant_roles=[GRANT_1]))
    assert upload._ga4gh_passport_permissions(t, request(hdas(INDEXED))) == {INDEXED: restricted_to(GRANT_1)}
    assert index_asked() == []


@pytest.mark.parametrize(
    "url",
    [
        "drs://controlled.example.org/phs000001/../obj1",
        "drs://controlled.example.org/phs000001/./obj1",
        "drs://controlled.example.org/phs000001/obj%31",
        "drs://controlled.example.org/phs000001/obj1?x=1",
    ],
)
def test_study_is_not_read_off_a_url_the_fetch_would_rewrite(url):
    # The fetch may ask for another object than the one under the study this URL spells out.
    source = ProtectedSource(url_regex=r"^drs://controlled\.example\.org/(?P<phs_id>phs\d+)/")
    t = trans(protected_sources=[source])
    assert upload._ga4gh_passport_permissions(t, request(hdas(url))) == {url: restricted_to(GRANT_1, GRANT_2)}


@pytest.mark.parametrize(
    "target",
    [
        {"destination": {"type": "hdca"}, "elements": [{"src": "url", "url": CONTROLLED}], "collection_type": "list"},
        {
            "destination": {"type": "library_folder", "library_folder_id": "f"},
            "elements": [{"src": "url", "url": CONTROLLED}],
        },
        {"destination": {"type": "hdas"}, "elements_from": "archive", "src": "url", "url": CONTROLLED},
        hdas(CONTROLLED, deferred=True),
        {
            "destination": {"type": "hdas"},
            "elements": [{"src": "url", "url": OPEN, "elements": [{"src": "url", "url": CONTROLLED}]}],
        },
    ],
    ids=["collection", "library", "elements_from", "deferred", "nested"],
)
def test_passport_uri_refused_where_dataset_is_not_created_now(target):
    t = trans()
    with pytest.raises(
        exceptions.RequestParameterInvalidException, match="can only be fetched as an individual dataset"
    ):
        upload._ga4gh_passport_permissions(t, request(hdas(OPEN), target))


@pytest.mark.parametrize("user", [True, False], ids=["no passport", "anonymous"])
def test_without_passport_nothing_is_restricted_or_refused(user):
    # No Passport is sent, so the repository can only return public data.
    t = trans(FakePassportManager(passport=False), user=user)
    collection = {"destination": {"type": "hdca"}, "elements": [{"src": "url", "url": CONTROLLED}]}
    assert upload._ga4gh_passport_permissions(t, request(hdas(CONTROLLED), collection)) is None
    assert t.app.manager.calls == 1


def test_passport_problems_refuse_the_request():
    error = exceptions.AuthenticationRequired("Connect your GA4GH Passport")
    with pytest.raises(exceptions.AuthenticationRequired, match="Connect"):
        upload._ga4gh_passport_permissions(trans(FakePassportManager(error)), request(hdas(CONTROLLED)))


def test_only_passport_datasets_get_the_permissions(monkeypatch):
    created = []

    def new_upload(
        trans, cntrller, uploaded_dataset, library_bunch=None, history=None, tag_list=None, permissions=None
    ):
        created.append((uploaded_dataset.name, permissions))
        return SimpleNamespace(id=len(created), dataset_id=len(created))

    monkeypatch.setattr(upload_common, "new_upload", new_upload)
    obj2_source = ProtectedSource(url_regex=r"^drs://controlled\.example\.org/obj2$", phs_id="phs000002.c1")
    t = trans(protected_sources=[obj2_source])
    target = hdas(CONTROLLED, OPEN, CONTROLLED_2)
    permissions = upload._ga4gh_passport_permissions(t, request(target))
    upload._precreate_fetched_hdas(t, None, target, [], permissions)
    assert created == [
        ("obj1", restricted_to(GRANT_1, GRANT_2)),
        ("obj3", None),
        ("obj2", restricted_to(GRANT_2)),
    ]
    assert [item["object_id"] for item in target["elements"]] == [1, 2, 3]


def test_without_passport_datasets_get_default_permissions(monkeypatch):
    created = []

    def new_upload(
        trans, cntrller, uploaded_dataset, library_bunch=None, history=None, tag_list=None, permissions=None
    ):
        created.append(permissions)
        return SimpleNamespace(id=len(created), dataset_id=len(created))

    monkeypatch.setattr(upload_common, "new_upload", new_upload)
    t = trans(FakePassportManager(passport=False))
    target = hdas(CONTROLLED)
    upload._precreate_fetched_hdas(t, None, target, [], upload._ga4gh_passport_permissions(t, request(target)))
    assert created == [None]
