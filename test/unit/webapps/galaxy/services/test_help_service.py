from unittest import mock

import pytest

from galaxy.exceptions import (
    ServerNotConfiguredForRequest,
    UpstreamProxyError,
)
from galaxy.schema.help import HelpForumTopicContent
from galaxy.webapps.galaxy.services.help import (
    _compose_search_query,
    HelpService,
)


def _config(**overrides):
    cfg = mock.Mock()
    cfg.help_forum_api_url = "https://help.galaxyproject.org/"
    cfg.help_forum_api_key = None
    cfg.help_forum_api_username = None
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _service(**overrides):
    return HelpService(security=mock.Mock(), config=_config(**overrides))


def test_compose_search_query_plain():
    assert _compose_search_query("rna seq error") == "rna seq error"


def test_compose_search_query_with_filters():
    composed = _compose_search_query(
        "upload fails",
        solved_only=True,
        category="usage",
        tags=["upload", "ftp"],
        order="likes",
    )
    assert composed == "upload fails status:solved #usage tags:upload,ftp order:likes"


def test_search_forum_requires_url():
    service = _service(help_forum_api_url=None)
    with pytest.raises(ServerNotConfiguredForRequest):
        service.search_forum("anything")


def test_search_forum_sends_filters_and_auth():
    service = _service(help_forum_api_key="k", help_forum_api_username="bot")
    response = mock.Mock(ok=True)
    response.json.return_value = {"topics": [], "posts": []}
    with mock.patch("galaxy.webapps.galaxy.services.help.requests.get", return_value=response) as get:
        service.search_forum("upload fails", solved_only=True)
    _, kwargs = get.call_args
    assert kwargs["params"]["q"] == "upload fails status:solved"
    assert kwargs["headers"]["Api-Key"] == "k"
    assert kwargs["headers"]["Api-Username"] == "bot"


def test_get_topic_prefers_accepted_answer():
    service = _service()
    payload = {
        "title": "Upload fails with FTP",
        "slug": "upload-fails-with-ftp",
        "accepted_answer": {"post_number": 3},
        "post_stream": {
            "posts": [
                {"post_number": 1, "cooked": "<p>My &amp; upload <b>fails</b></p>", "like_count": 0},
                {"post_number": 2, "cooked": "<p>Did you try X?</p>", "like_count": 1},
                {"post_number": 3, "cooked": "<p>Use the FTP client.</p>", "like_count": 5},
            ]
        },
    }
    response = mock.Mock(ok=True)
    response.json.return_value = payload
    with mock.patch("galaxy.webapps.galaxy.services.help.requests.get", return_value=response):
        topic = service.get_topic(42)
    assert isinstance(topic, HelpForumTopicContent)
    assert topic.question == "My & upload fails"
    assert topic.answer == "Use the FTP client."
    assert topic.answer_is_accepted is True
    assert topic.url == "https://help.galaxyproject.org/t/upload-fails-with-ftp/42"


def test_get_topic_accepted_answer_via_per_post_flag():
    """Primary path: per-post accepted_answer=True, no topic-level accepted_answer key.

    Mirrors the real help.galaxyproject.org payload shape where the accepted post
    carries accepted_answer=True and topic_accepted_answer=True (which must be
    ignored -- it's set on every post to signal the topic is solved).
    """
    service = _service()
    payload = {
        "title": "How to run FastQC",
        "slug": "how-to-run-fastqc",
        "post_stream": {
            "posts": [
                {"post_number": 1, "cooked": "<p>How do I run FastQC?</p>", "like_count": None},
                {
                    "post_number": 2,
                    "cooked": "<p>Use the FastQC tool in Galaxy.</p>",
                    "like_count": None,
                    "accepted_answer": True,
                    "topic_accepted_answer": True,
                },
                {
                    "post_number": 3,
                    "cooked": "<p>You can also try MultiQC.</p>",
                    "like_count": None,
                    "topic_accepted_answer": True,
                },
            ]
        },
    }
    response = mock.Mock(ok=True)
    response.json.return_value = payload
    with mock.patch("galaxy.webapps.galaxy.services.help.requests.get", return_value=response):
        topic = service.get_topic(77)
    assert isinstance(topic, HelpForumTopicContent)
    assert topic.answer_is_accepted is True
    assert topic.answer == "Use the FastQC tool in Galaxy."


def test_search_forum_rate_limited_raises_upstream_proxy_error():
    """HTTP 429 must raise UpstreamProxyError (transient), not InternalServerError."""
    service = _service()
    response = mock.Mock(ok=False, status_code=429)
    with mock.patch("galaxy.webapps.galaxy.services.help.requests.get", return_value=response):
        with pytest.raises(UpstreamProxyError, match="rate-limiting"):
            service.search_forum("some query")


def test_get_topic_fallback_picks_most_liked_reply_with_null_like_count():
    """Fallback to most-liked reply when there is no accepted answer.

    One reply has like_count=None (JSON null) -- the null-guard must coerce it
    to 0 so max() doesn't raise TypeError, and the genuinely highest-liked
    reply must be selected.
    """
    service = _service()
    payload = {
        "title": "Job fails with exit code 1",
        "slug": "job-fails-with-exit-code-1",
        "accepted_answer": {},  # no accepted answer
        "post_stream": {
            "posts": [
                {"post_number": 1, "cooked": "<p>My job fails.</p>", "like_count": 0},
                {"post_number": 2, "cooked": "<p>Try increasing memory.</p>", "like_count": None},
                {"post_number": 3, "cooked": "<p>Check the stderr log.</p>", "like_count": 7},
                {"post_number": 4, "cooked": "<p>Reinstall the tool.</p>", "like_count": 2},
            ]
        },
    }
    response = mock.Mock(ok=True)
    response.json.return_value = payload
    with mock.patch("galaxy.webapps.galaxy.services.help.requests.get", return_value=response):
        topic = service.get_topic(99)
    assert isinstance(topic, HelpForumTopicContent)
    assert topic.answer_is_accepted is False
    assert topic.answer == "Check the stderr log."
