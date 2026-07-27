"""Help Forum Agent -- searches help.galaxyproject.org (Discourse) and answers."""

import json
import logging
from pathlib import Path
from typing import (
    Any,
    Optional,
)
from urllib.parse import urlencode

import anyio
from pydantic import (
    BaseModel,
    Field,
)
from pydantic_ai import (
    Agent,
    RunContext,
)

from galaxy.schema.agents import ConfidenceLevel
from galaxy.webapps.galaxy.services.help import HelpService
from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    AgentType,
    BaseGalaxyAgent,
    extract_result_content,
    extract_structured_output,
    extract_usage_info,
    GalaxyAgentDependencies,
)

log = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 80


class HelpThread(BaseModel):
    """A single cited help-forum thread."""

    title: str = Field(..., description="Thread title")
    topic_id: int = Field(..., gt=0, description="Forum topic id, as returned by search_help_forum")
    excerpt: str = Field("", description="Short snippet or synthesized excerpt")
    has_accepted_answer: bool = Field(False, description="Whether the thread has an accepted answer")
    tags: list[str] = Field(default_factory=list, description="Thread tags")
    reply_count: int = Field(0, description="Number of replies")


class HelpForumResponse(BaseModel):
    """Structured response from the help forum agent."""

    summary: str = Field(..., description="Synthesized answer in the model's own words")
    threads: list[HelpThread] = Field(default_factory=list, description="Cited forum threads")


def build_topic_url(topic_id: int, config: Any) -> str:
    """Build a canonical forum topic URL from trusted configuration.

    Links are constructed here rather than taken from the model's output. Forum posts
    are untrusted user-generated text, so a model-supplied URL -- hallucinated, or
    planted by an injected post -- would otherwise render as a link and an action
    button inside Galaxy's own assistant UI. Discourse redirects the slug-less form
    to the full topic URL.
    """
    base_url = (getattr(config, "help_forum_api_url", "") or "").rstrip("/")
    return f"{base_url}/t/{topic_id}"


def build_ask_forum_url(question: str, config: Any) -> str:
    """Build a Discourse new-topic URL with the user's question prefilled."""
    base_url = (getattr(config, "help_forum_api_url", "") or "").rstrip("/")
    title = question.strip()
    if len(title) > _MAX_TITLE_LENGTH:
        title = title[:_MAX_TITLE_LENGTH].rstrip()
    params = urlencode({"title": title, "body": question.strip()})
    return f"{base_url}/new-topic?{params}"


class HelpForumAgent(BaseGalaxyAgent):
    """Searches the Galaxy Help forum (Discourse) and synthesizes cited answers."""

    agent_type = AgentType.HELP_FORUM

    MAX_TOOL_CALLS = 4
    _TOOL_BUDGET_MESSAGE = (
        "SEARCH BUDGET REACHED. You have enough material to answer. Do NOT call "
        "search_help_forum or get_forum_topic again. Produce your final structured "
        "answer now from the threads already returned. If none is a strong match, say "
        "so and let the user ask on the forum."
    )

    def __init__(self, deps: GalaxyAgentDependencies):
        super().__init__(deps)
        self._tool_calls = 0
        self._help_service: Optional[HelpService] = None
        try:
            self._help_service = HelpService(deps.trans.app.security, deps.config)
        except (AttributeError, RuntimeError) as e:
            log.warning(f"Help forum service not available: {e}")

    def _charge_tool_budget(self) -> Optional[str]:
        self._tool_calls += 1
        if self._tool_calls > self.MAX_TOOL_CALLS:
            return self._TOOL_BUDGET_MESSAGE
        return None

    def _create_agent(self) -> Agent[GalaxyAgentDependencies, Any]:
        if not self._supports_structured_output():
            return Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                system_prompt=self.get_system_prompt(),
                retries=self._get_retries(),
            )

        agent = Agent(
            self._get_model(),
            deps_type=GalaxyAgentDependencies,
            output_type=HelpForumResponse,
            system_prompt=self.get_system_prompt(),
            retries=self._get_retries(),
        )

        @agent.tool
        async def search_help_forum(
            ctx: RunContext[GalaxyAgentDependencies],
            query: str,
            solved_only: bool = False,
            category: Optional[str] = None,
            tags: Optional[list[str]] = None,
            limit: int = 5,
        ) -> str:
            """Search the Galaxy Help forum. Returns ranked topics with title, url, tags, accepted-answer flag."""
            over_budget = self._charge_tool_budget()
            if over_budget:
                return over_budget
            if not self._help_service:
                return json.dumps({"error": "Help forum not available"})
            try:
                search = await anyio.to_thread.run_sync(
                    lambda: self._help_service.search_forum(
                        query, solved_only=solved_only, category=category, tags=tags
                    )
                )
            except Exception as e:
                log.warning(f"Help forum search failed: {e}")
                return json.dumps({"error": str(e)})
            topics = []
            for topic in (search.topics or [])[:limit]:
                # Deliberately no url here -- cite by topic_id and let Galaxy build the
                # link, so the model never handles (or invents) a URL.
                topics.append(
                    {
                        "topic_id": topic.id,
                        "title": topic.title,
                        "tags": [getattr(t, "name", "") for t in (topic.tags or [])],
                        "has_accepted_answer": topic.has_accepted_answer,
                        "reply_count": topic.reply_count,
                    }
                )
            return json.dumps({"results": topics, "count": len(topics)})

        @agent.tool
        async def get_forum_topic(ctx: RunContext[GalaxyAgentDependencies], topic_id: int) -> str:
            """Read one forum topic: the question plus its accepted/top answer (truncated)."""
            over_budget = self._charge_tool_budget()
            if over_budget:
                return over_budget
            if not self._help_service:
                return json.dumps({"error": "Help forum not available"})
            try:
                topic = await anyio.to_thread.run_sync(lambda: self._help_service.get_topic(topic_id))
            except Exception as e:
                log.warning(f"Help forum topic fetch failed: {e}")
                return json.dumps({"error": str(e)})
            return topic.model_dump_json()

        return agent

    def get_system_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompts" / "help_forum.md"
        return prompt_path.read_text()

    async def process(self, query: str, context: Optional[dict[str, Any]] = None) -> AgentResponse:
        validation_error = self._validate_query(query)
        if validation_error:
            return self._validation_error_response(validation_error)

        self._tool_calls = 0
        try:
            message_history = self._extract_message_history(context)
            result = await self._run_with_retry(query, message_history=message_history)

            usage = extract_usage_info(result)
            if usage:
                log.info(
                    "Help forum agent token usage: input=%s output=%s total=%s",
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("total_tokens", 0),
                )

            if self._supports_structured_output():
                response_data = extract_structured_output(result, HelpForumResponse, log)
                if response_data is None:
                    return self._build_response(
                        content=extract_result_content(result),
                        confidence=ConfidenceLevel.LOW,
                        method="text_fallback",
                        result=result,
                        query=query,
                        suggestions=[self._ask_button(query)],
                        error="invalid_structured_output",
                    )
                return self._build_response(
                    content=self._format_response(response_data),
                    confidence=ConfidenceLevel.HIGH if response_data.threads else ConfidenceLevel.MEDIUM,
                    method="structured",
                    result=result,
                    query=query,
                    suggestions=self._create_suggestions(response_data, query),
                    agent_data={"thread_count": len(response_data.threads)},
                )

            response_text = extract_result_content(result)
            return self._build_response(
                content=response_text,
                confidence=ConfidenceLevel.MEDIUM,
                method="simple_text",
                result=result,
                query=query,
                suggestions=[self._ask_button(query)],
            )
        except (OSError, ValueError) as e:
            log.error(f"Help forum agent error: {e}")
            return self._build_response(
                content=(
                    "I could not reach the Galaxy Help forum right now. "
                    "You can ask the community directly using the button below."
                ),
                confidence=ConfidenceLevel.LOW,
                method="error_fallback",
                query=query,
                suggestions=[self._ask_button(query)],
                error=str(e),
            )

    def _ask_button(self, query: str) -> ActionSuggestion:
        return ActionSuggestion(
            action_type=ActionType.VIEW_EXTERNAL,
            description="Ask on Galaxy Help",
            parameters={"url": build_ask_forum_url(query, self.deps.config)},
            confidence=ConfidenceLevel.MEDIUM,
            priority=1,
        )

    def _format_response(self, response_data: HelpForumResponse) -> str:
        parts: list[str] = []
        if response_data.summary:
            parts.append(response_data.summary)
        if response_data.threads:
            parts.append("\n**Relevant forum threads:**")
            for i, thread in enumerate(response_data.threads, 1):
                marker = " (accepted answer)" if thread.has_accepted_answer else ""
                parts.append(f"\n{i}. **{thread.title}**{marker}")
                if thread.excerpt:
                    parts.append(f"   {thread.excerpt}")
                parts.append(f"   - Link: {build_topic_url(thread.topic_id, self.deps.config)}")
            parts.append("\n_These are community answers, not official Galaxy documentation._")
        else:
            parts.append("\nI could not find a clear answer on the forum. You can ask the community directly below.")
        return "\n".join(parts)

    def _create_suggestions(self, response_data: HelpForumResponse, query: str) -> list[ActionSuggestion]:
        suggestions: list[ActionSuggestion] = []
        for thread in response_data.threads[:3]:
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.VIEW_EXTERNAL,
                    description=f"Open thread: {thread.title}",
                    parameters={"url": build_topic_url(thread.topic_id, self.deps.config)},
                    confidence=ConfidenceLevel.HIGH,
                    priority=1,
                )
            )
        suggestions.append(self._ask_button(query))
        return suggestions
