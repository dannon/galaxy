"""Data fetched with a GA4GH Passport is restricted from the moment its dataset is created."""

from types import SimpleNamespace
from typing import Any

import pytest

from galaxy import exceptions
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
FILE_SOURCES: list[dict[str, Any]] = [
    {"type": "drs", "id": "controlled", "url_regex": "^drs://controlled.example.org/", "ga4gh_passport": True},
    {"type": "drs", "id": "open", "url_regex": "^drs://open.example.org/"},
]


GRANT_1, GRANT_2 = SimpleNamespace(name="ga4gh_grant:7:phs1.c1"), SimpleNamespace(name="ga4gh_grant:7:phs2.c1")
MANAGE = SimpleNamespace(name="ga4gh_controlled_data")


class FakePassportManager:
    def __init__(self, error: Exception | None = None, passport: bool = True):
        self.error = error
        self.passport = passport
        self.calls = 0

    def fetch_roles(self, user):
        self.calls += 1
        if self.error:
            raise self.error
        return ([GRANT_1, GRANT_2], MANAGE) if self.passport and user is not None else None


class FakeApp:
    def __init__(self, manager: FakePassportManager):
        self.file_sources = ConfiguredFileSources(
            FileSourcePluginsConfig(), ConfiguredFileSourcesConf(conf_dict=FILE_SOURCES)
        )
        self.security_agent = SimpleNamespace(permitted_actions=ACTIONS)
        self.manager = manager

    def __getitem__(self, cls):
        return self.manager


def trans(manager=None, user=True):
    return SimpleNamespace(user=SimpleNamespace(id=7) if user else None, app=FakeApp(manager or FakePassportManager()))


def hdas(*urls, **item_extra):
    return {"destination": {"type": "hdas"}, "elements": [{"src": "url", "url": u, **item_extra} for u in urls]}


def request(*targets):
    return {"targets": list(targets)}


def test_no_passport_uri_keeps_default_permissions():
    t = trans()
    assert upload._ga4gh_passport_permissions(t, request(hdas(OPEN, "https://example.org/x"))) is None
    assert t.app.manager.calls == 0


def test_passport_uri_restricted_to_all_grant_roles_managed_by_no_one():
    permissions = upload._ga4gh_passport_permissions(trans(), request(hdas(CONTROLLED, OPEN)))
    assert permissions == {
        ACTIONS.DATASET_ACCESS: [GRANT_1, GRANT_2],
        ACTIONS.DATASET_MANAGE_PERMISSIONS: [MANAGE],
    }


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
    t = trans()
    target = hdas(CONTROLLED, OPEN, CONTROLLED_2)
    permissions = upload._ga4gh_passport_permissions(t, request(target))
    upload._precreate_fetched_hdas(t, None, target, [], permissions)
    assert created == [("obj1", permissions), ("obj3", None), ("obj2", permissions)]
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
