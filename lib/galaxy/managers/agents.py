"""Agent service layer for AI agent management."""

import logging
from collections.abc import (
    Awaitable,
    Callable,
)
from typing import (
    Any,
    Optional,
)

from galaxy.agents import GalaxyAgentDependencies
from galaxy.agents.base import BaseGalaxyAgent
from galaxy.agents.registry import AgentRegistry
from galaxy.agents.router import QueryRouterAgent
from galaxy.agents.streaming import (
    get_run_registry,
    new_run_id,
    StreamingEventEmitter,
)
from galaxy.config import GalaxyAppConfiguration
from galaxy.managers.context import ProvidesUserContext
from galaxy.managers.jobs import JobManager
from galaxy.managers.sse_dispatch import SSEEventDispatcher
from galaxy.model import User
from galaxy.schema.agents import AgentResponse

log = logging.getLogger(__name__)


class AgentService:
    """Service layer for AI agent execution and routing."""

    def __init__(
        self,
        config: GalaxyAppConfiguration,
        job_manager: JobManager,
        registry: AgentRegistry,
    ):
        self.config = config
        self.job_manager = job_manager
        self.registry = registry

    def create_dependencies(self, trans: ProvidesUserContext, user: User) -> GalaxyAgentDependencies:
        """Create agent dependencies for dependency injection."""
        toolbox = trans.app.toolbox if hasattr(trans, "app") and hasattr(trans.app, "toolbox") else None
        return GalaxyAgentDependencies(
            trans=trans,
            user=user,
            config=self.config,
            job_manager=self.job_manager,
            toolbox=toolbox,
            get_agent=self.registry.get_agent,
        )

    def _resolve_agent(self, agent_type: str, deps: GalaxyAgentDependencies) -> BaseGalaxyAgent:
        """Resolve an agent_type to an agent instance via the registry.

        Centralized so both ``execute_agent`` and ``start_streaming_run`` agree
        on lookup semantics; raises ``ValueError`` for unknown types so callers
        can fall back to the router.
        """
        return self.registry.get_agent(agent_type, deps)

    async def execute_agent(
        self,
        agent_type: str,
        query: str,
        trans: ProvidesUserContext,
        user: User,
        context: Optional[dict[str, Any]] = None,
    ) -> AgentResponse:
        """Execute a specific agent and return response."""
        deps = self.create_dependencies(trans, user)

        if context is None:
            context = {}

        try:
            log.info(f"Executing {agent_type} agent for query: '{query[:100]}...'")
            agent = self._resolve_agent(agent_type, deps)
            response = await agent.process(query, context)

            return AgentResponse(
                content=response.content,
                agent_type=response.agent_type,
                confidence=response.confidence,
                suggestions=response.suggestions,
                metadata=response.metadata,
                reasoning=response.reasoning,
            )
        except ValueError as e:
            log.warning(f"Unknown agent type {agent_type}, falling back to router: {e}")
            # Fallback to router for unknown agents - it handles general queries
            router = QueryRouterAgent(deps)
            response = await router.process(query, context)
            metadata = response.metadata.copy()
            metadata["fallback"] = True
            metadata["original_agent_type"] = agent_type
            return AgentResponse(
                content=response.content,
                agent_type=response.agent_type,
                confidence=response.confidence,
                suggestions=response.suggestions,
                metadata=metadata,
                reasoning=response.reasoning,
            )
        except OSError as e:
            log.error(f"Network error executing agent {agent_type}: {e}")
            raise
        except RuntimeError as e:
            log.exception(f"Runtime error executing agent {agent_type}: {e}")
            raise

    async def route_and_execute(
        self,
        query: str,
        trans: ProvidesUserContext,
        user: User,
        context: Optional[dict[str, Any]] = None,
        agent_type: str = "auto",
    ) -> AgentResponse:
        """
        Execute query with automatic routing or specific agent.

        When agent_type is 'auto', the router agent handles the query directly,
        either answering it or using output functions to hand off to specialists.
        """
        if agent_type == "auto":
            # Router handles everything via output functions:
            # - Answers general questions directly
            # - Hands off to error_analysis for debugging
            # - Hands off to custom_tool for tool creation
            log.info(f"Processing query via router: '{query[:100]}...'")
            return await self.execute_agent("router", query, trans, user, context)
        else:
            # Explicit agent request - execute directly
            log.info(f"User explicitly requested agent: {agent_type}")
            return await self.execute_agent(agent_type, query, trans, user, context)

    def list_agents(self) -> list[str]:
        return self.registry.list_agents()

    def get_agent_info(self, agent_type: str) -> dict:
        return self.registry.get_agent_info(agent_type)

    async def start_streaming_run(
        self,
        trans: ProvidesUserContext,
        user: User,
        query: str,
        agent_type: str,
        context: Optional[dict[str, Any]],
        exchange_id: Optional[str],
        on_complete: Callable[[str, Optional[AgentResponse]], Awaitable[None]],
    ) -> str:
        """Schedule a streaming agent run.

        Returns the run_id immediately. The actual ``agent.iter()`` loop runs
        on the worker's event loop and emits SSE events via
        ``StreamingEventEmitter``. ``on_complete`` is awaited after the run
        finishes (success or error) so the caller can persist the final
        message via ``chat_manager``.
        """
        run_id = new_run_id()
        deps = self.create_dependencies(trans, user)
        try:
            agent = self._resolve_agent(agent_type, deps)
        except ValueError as e:
            log.warning(f"Unknown agent type {agent_type} for streaming run, falling back to router: {e}")
            agent = QueryRouterAgent(deps)
        dispatcher = trans.app.resolve_or_none(SSEEventDispatcher)
        if dispatcher is None:
            raise RuntimeError("SSEEventDispatcher is not registered; cannot start streaming run.")
        emitter = StreamingEventEmitter(
            dispatcher=dispatcher,
            user_id=user.id,
            run_id=run_id,
            exchange_id=exchange_id,
        )

        async def _run() -> None:
            try:
                response = await agent.process_streaming(query, emitter, context)
                await on_complete(run_id, response)
            except Exception as e:
                log.warning("Streaming run %s failed: %s", run_id, e)
                await on_complete(run_id, None)

        get_run_registry().start(user_id=user.id, run_id=run_id, coro_factory=_run)
        return run_id
