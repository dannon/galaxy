"""Help Forum Agent -- searches help.galaxyproject.org (Discourse) and answers."""

import logging
from typing import (
    Any,
)
from urllib.parse import urlencode

from pydantic import (
    BaseModel,
    Field,
)

log = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 80


class HelpThread(BaseModel):
    """A single cited help-forum thread."""

    title: str = Field(..., description="Thread title")
    url: str = Field(..., description="Canonical thread URL")
    excerpt: str = Field("", description="Short snippet or synthesized excerpt")
    has_accepted_answer: bool = Field(False, description="Whether the thread has an accepted answer")
    tags: list[str] = Field(default_factory=list, description="Thread tags")
    reply_count: int = Field(0, description="Number of replies")


class HelpForumResponse(BaseModel):
    """Structured response from the help forum agent."""

    summary: str = Field(..., description="Synthesized answer in the model's own words")
    threads: list[HelpThread] = Field(default_factory=list, description="Cited forum threads")


def build_ask_forum_url(question: str, config: Any) -> str:
    """Build a Discourse new-topic URL with the user's question prefilled."""
    base_url = (getattr(config, "help_forum_api_url", "") or "").rstrip("/")
    title = question.strip()
    if len(title) > _MAX_TITLE_LENGTH:
        title = title[:_MAX_TITLE_LENGTH].rstrip()
    params = urlencode({"title": title, "body": question.strip()})
    return f"{base_url}/new-topic?{params}"
