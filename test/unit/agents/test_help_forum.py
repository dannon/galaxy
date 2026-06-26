from unittest import mock
from urllib.parse import (
    parse_qs,
    urlparse,
)

import pytest

from galaxy.agents.base import (
    ActionType,
    GalaxyAgentDependencies,
)
from galaxy.agents.help_forum import (
    build_ask_forum_url,
    HelpForumAgent,
    HelpForumResponse,
    HelpThread,
)
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
                url="https://help.galaxyproject.org/t/upload-fails/42",
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
