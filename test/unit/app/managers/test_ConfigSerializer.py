from types import SimpleNamespace

import pytest

from galaxy.app_unittest_utils.galaxy_mock import MockApp
from galaxy.managers.configuration import ConfigSerializer


@pytest.mark.parametrize("authorize_url, expected", [("https://bridge.example.org/connect", True), (None, False)])
def test_ga4gh_passport_enabled(authorize_url, expected):
    config = SimpleNamespace(ga4gh_passport_broker_authorize_url=authorize_url)
    serializer = ConfigSerializer(MockApp())
    key = "ga4gh_passport_enabled"
    assert serializer.serializers[key](config, key) is expected
