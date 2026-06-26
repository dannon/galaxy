from unittest import mock
from urllib.parse import (
    parse_qs,
    urlparse,
)

from galaxy.agents.help_forum import (
    build_ask_forum_url,
)


def _config():
    cfg = mock.Mock()
    cfg.help_forum_api_url = "https://help.galaxyproject.org/"
    return cfg


def test_build_ask_forum_url_encodes_question():
    url = build_ask_forum_url('Why does "upload" fail & hang?', _config())
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.path == "/new-topic"
    assert qs["title"][0].startswith('Why does "upload" fail')
    assert qs["body"][0] == 'Why does "upload" fail & hang?'


def test_build_ask_forum_url_truncates_long_title():
    long_q = "word " * 60
    url = build_ask_forum_url(long_q.strip(), _config())
    qs = parse_qs(urlparse(url).query)
    assert len(qs["title"][0]) <= 80
    assert qs["body"][0] == long_q.strip()
