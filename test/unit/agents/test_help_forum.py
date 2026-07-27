from unittest import mock
from urllib.parse import (
    parse_qs,
    urlparse,
)

import pytest
from pydantic import ValidationError

from galaxy.agents.base import (
    ActionType,
    GalaxyAgentDependencies,
)
from galaxy.agents.help_forum import (
    build_ask_forum_url,
    build_topic_url,
    HelpForumAgent,
    HelpForumResponse,
    HelpThread,
)
from galaxy.agents.router import QueryRouterAgent
from galaxy.schema.agents import ConfidenceLevel


def _config():
    cfg = mock.Mock()
    cfg.help_forum_api_url = "https://help.galaxyproject.org/"
    return cfg


def _deps():
    config = _config()
    config.inference_services = {"help_forum": {"model": "test-model", "api_key": "k"}}
    config.ai_model = "test-model"
    config.ai_api_key = "k"
    config.ai_api_base_url = "http://localhost"
    config.agent_model_capabilities_file = None
    return GalaxyAgentDependencies(
        trans=mock.Mock(),
        user=mock.Mock(),
        config=config,
        get_agent=mock.Mock(),
    )


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


@pytest.mark.asyncio
async def test_process_always_includes_ask_button():
    agent = HelpForumAgent(_deps())
    structured = HelpForumResponse(
        summary="Set passive mode in your FTP client.",
        threads=[
            HelpThread(
                title="Upload fails with FTP",
                topic_id=42,
                excerpt="Use passive mode.",
                has_accepted_answer=True,
                tags=["upload"],
                reply_count=3,
            )
        ],
    )
    result = mock.Mock()
    result.output = structured
    result.usage = mock.Mock(return_value=None)
    with mock.patch.object(agent, "_run_with_retry", new=mock.AsyncMock(return_value=result)):
        response = await agent.process("Why does my FTP upload fail?")

    ask_buttons = [
        s
        for s in response.suggestions
        if s.action_type == ActionType.VIEW_EXTERNAL and "Ask on Galaxy Help" in s.description
    ]
    assert len(ask_buttons) == 1
    assert "/new-topic?" in ask_buttons[0].parameters["url"]
    assert "Set passive mode" in response.content
    assert response.confidence == ConfidenceLevel.HIGH


@pytest.mark.asyncio
async def test_cited_links_are_built_from_config_not_model_output():
    """A cited thread's link must come from help_forum_api_url + topic_id.

    Forum posts are untrusted, so the model never supplies a URL -- otherwise an
    injected or hallucinated link would render as an action button in Galaxy's UI.
    """
    agent = HelpForumAgent(_deps())
    structured = HelpForumResponse(
        summary="Use passive mode.",
        threads=[HelpThread(title="Upload fails", topic_id=42, excerpt="passive mode")],
    )
    result = mock.Mock()
    result.output = structured
    result.usage = mock.Mock(return_value=None)
    with mock.patch.object(agent, "_run_with_retry", new=mock.AsyncMock(return_value=result)):
        response = await agent.process("Why does my upload fail?")

    expected = "https://help.galaxyproject.org/t/42"
    thread_buttons = [s for s in response.suggestions if s.description.startswith("Open thread:")]
    assert len(thread_buttons) == 1
    assert thread_buttons[0].parameters["url"] == expected
    assert expected in response.content
    # Every rendered link points at the configured forum, nothing else.
    for suggestion in response.suggestions:
        assert suggestion.parameters["url"].startswith("https://help.galaxyproject.org/")


def test_help_thread_requires_a_positive_integer_topic_id():
    """topic_id is an int so a model cannot smuggle an arbitrary host into a link."""
    for bad in (0, -1, "https://evil.example/t/1"):
        with pytest.raises(ValidationError):
            HelpThread(title="t", topic_id=bad)


def test_build_topic_url_uses_configured_host():
    assert build_topic_url(13803, _config()) == "https://help.galaxyproject.org/t/13803"


def test_router_exposes_help_forum_handoff():
    agent = QueryRouterAgent(_deps())
    handoff = agent._create_help_forum_handoff()
    assert handoff.__name__ == "hand_off_to_help_forum"
