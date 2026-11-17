"""Downloading or embedding a collection checks access to each dataset, not just to its history."""

from types import SimpleNamespace

import pytest

from galaxy import exceptions
from galaxy.managers import (
    hdcas,
    markdown_util,
)


class FakeHDA:
    states = SimpleNamespace(OK="ok")

    def __init__(self, name):
        self.name = name
        self.state = "ok"
        self.purged = False
        self.dataset = SimpleNamespace(purged=False)
        self.datatype = SimpleNamespace(to_archive=lambda dataset, name, auth: [(f"/data/{name}", name)])


class Archive:
    def __init__(self):
        self.written = []

    def write(self, path, relpath):
        self.written.append(relpath)


@pytest.fixture
def collection(monkeypatch):
    hdas = [FakeHDA("open"), FakeHDA("controlled")]
    monkeypatch.setattr(hdcas, "get_hda_and_element_identifiers", lambda instance: ([h.name for h in hdas], hdas))
    return SimpleNamespace(collection=SimpleNamespace(populated_optimized=True))


def test_collection_download_refuses_an_inaccessible_element(collection):
    with pytest.raises(exceptions.ItemAccessibilityException, match="controlled"):
        hdcas.write_dataset_collection(collection, Archive(), None, can_access=lambda hda: hda.name != "controlled")


def test_collection_download_of_accessible_elements(collection):
    archive = Archive()
    hdcas.write_dataset_collection(collection, archive, None, can_access=lambda hda: True)
    assert archive.written == ["open", "controlled"]


class FakeSecurityAgent:
    def __init__(self, controlled: bool, accessible: bool):
        self.controlled = controlled
        self.accessible = accessible

    def admin_override_allowed(self, is_admin, dataset):
        return is_admin and not self.controlled

    def can_access_dataset(self, roles, dataset):
        return self.accessible


def trans(agent, admin=False):
    return SimpleNamespace(
        app=SimpleNamespace(security_agent=agent), user_is_admin=admin, get_current_user_roles=lambda: []
    )


HDCA = SimpleNamespace(collection=SimpleNamespace(dataset_instances=[SimpleNamespace(dataset=object())]))


@pytest.mark.parametrize(
    "controlled, accessible, admin, allowed",
    [
        (False, True, False, True),
        (True, False, False, False),  # a viewer of a shared/published page without the grants
        (True, True, False, True),  # the data's owner
        (False, False, True, True),  # admins keep their powers over ordinary data...
        (True, False, True, False),  # ...but not over controlled data
    ],
)
def test_markdown_collection_embedding(controlled, accessible, admin, allowed):
    check = markdown_util._check_collection_datasets_accessible
    if allowed:
        check(trans(FakeSecurityAgent(controlled, accessible), admin), HDCA)
    else:
        with pytest.raises(exceptions.ItemAccessibilityException):
            check(trans(FakeSecurityAgent(controlled, accessible), admin), HDCA)
