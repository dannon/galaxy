import asyncio

import pytest
from pydantic_ai.messages import (
    PartDeltaEvent,
    TextPartDelta,
)

from galaxy.agents.base import (
    AgentResponse,
    AgentType,
    BaseGalaxyAgent,
)
from galaxy.agents.router import QueryRouterAgent
from galaxy.agents.streaming import (
    ChatRunRegistry,
    ChatStreamEvent,
    ChatStreamKind,
    StreamingEventEmitter,
)
from galaxy.exceptions import TooManyConcurrentRequestsException
from galaxy.managers.agents import AgentService
from galaxy.managers.sse_dispatch import SSEEventDispatcher


class FakeDispatcher:
    def __init__(self):
        self.calls: list[tuple[int, dict]] = []

    def chat_event(self, user_id: int, payload: dict, event_id=None) -> None:
        self.calls.append((user_id, payload))


@pytest.mark.asyncio
async def test_emitter_assigns_monotonic_seq():
    dispatcher = FakeDispatcher()
    emitter = StreamingEventEmitter(
        dispatcher=dispatcher,
        user_id=42,
        run_id="run-abc",
        exchange_id="exch-xyz",
    )

    await emitter.delta("hello ")
    await emitter.delta("world")
    await emitter.done(final_content="hello world")

    seqs = [payload["seq"] for _, payload in dispatcher.calls]
    assert seqs == [0, 1, 2]
    kinds = [payload["kind"] for _, payload in dispatcher.calls]
    assert kinds == [ChatStreamKind.DELTA, ChatStreamKind.DELTA, ChatStreamKind.DONE]
    assert all(payload["run_id"] == "run-abc" for _, payload in dispatcher.calls)
    assert all(payload["exchange_id"] == "exch-xyz" for _, payload in dispatcher.calls)


@pytest.mark.asyncio
async def test_emitter_done_after_error_is_noop():
    dispatcher = FakeDispatcher()
    emitter = StreamingEventEmitter(dispatcher=dispatcher, user_id=1, run_id="r", exchange_id="e")
    await emitter.error("boom")
    await emitter.done(final_content="ignored")
    kinds = [payload["kind"] for _, payload in dispatcher.calls]
    assert kinds == [ChatStreamKind.ERROR]


def test_chat_stream_event_dataclass_shape():
    event = ChatStreamEvent(
        kind=ChatStreamKind.DELTA,
        run_id="r",
        exchange_id="e",
        seq=0,
        body={"text": "hi"},
    )
    assert event.kind == ChatStreamKind.DELTA
    assert event.body == {"text": "hi"}


@pytest.mark.asyncio
async def test_registry_tracks_runs_until_completion():
    registry = ChatRunRegistry(max_per_user=2)

    async def work():
        await asyncio.sleep(0.01)

    handle1 = registry.start(user_id=7, run_id="a", coro_factory=work)
    handle2 = registry.start(user_id=7, run_id="b", coro_factory=work)
    assert registry.active_count(user_id=7) == 2

    await handle1
    await handle2
    # Yield once so the tasks' done callbacks (which deregister) get to run.
    await asyncio.sleep(0)
    assert registry.active_count(user_id=7) == 0


@pytest.mark.asyncio
async def test_registry_enforces_per_user_cap():
    registry = ChatRunRegistry(max_per_user=1)

    async def work():
        await asyncio.sleep(0.05)

    registry.start(user_id=9, run_id="a", coro_factory=work)
    with pytest.raises(TooManyConcurrentRequestsException, match="Too many concurrent"):
        registry.start(user_id=9, run_id="b", coro_factory=work)


@pytest.mark.asyncio
async def test_default_process_streaming_emits_single_delta_then_done():
    class _StubAgent(BaseGalaxyAgent):
        agent_type = AgentType.HISTORY

        def __init__(self):
            pass  # bypass full constructor

        def _create_agent(self):
            return None

        async def process(self, query, context=None):
            return AgentResponse(content="the answer", agent_type=self.agent_type, confidence="high")

        def get_system_prompt(self):
            return ""

    dispatcher = FakeDispatcher()
    emitter = StreamingEventEmitter(dispatcher=dispatcher, user_id=1, run_id="r", exchange_id="e")
    await _StubAgent().process_streaming("q", emitter, context=None)

    kinds = [payload["kind"] for _, payload in dispatcher.calls]
    assert kinds == [ChatStreamKind.DELTA, ChatStreamKind.DONE]
    delta_payload = dispatcher.calls[0][1]
    done_payload = dispatcher.calls[1][1]
    assert delta_payload["text"] == "the answer"
    assert done_payload["final_content"] == "the answer"


@pytest.mark.asyncio
async def test_router_streams_token_deltas():
    """QueryRouterAgent's process_streaming should emit one delta per text chunk."""
    chunks = ["Hel", "lo, ", "world"]

    class _FakeRequestStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def __aiter__(self):
            for c in chunks:
                yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=c))

    class _FakeNode:
        def stream(self, ctx):
            return _FakeRequestStream()

    class _FakeAgentRun:
        ctx = None
        result = type("R", (), {"output": "Hello, world"})()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def __aiter__(self):
            self._yielded = False
            return self

        async def __anext__(self):
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True
            return _FakeNode()

    class _FakeAgent:
        @staticmethod
        def is_model_request_node(node):
            return isinstance(node, _FakeNode)

        @staticmethod
        def is_call_tools_node(node):
            return False

        def iter(self, *a, **kw):
            return _FakeAgentRun()

    # Build a QueryRouterAgent shell whose self.agent is our fake. We give
    # `deps` a minimal stand-in so that helper methods like _validate_query
    # and _get_temperature can read inference config without crashing.
    class _StubConfig:
        inference_services: dict = {}

    class _StubDeps:
        config = _StubConfig()

    router = QueryRouterAgent.__new__(QueryRouterAgent)
    router.agent = _FakeAgent()
    router.agent_type = AgentType.ROUTER
    router.deps = _StubDeps()

    dispatcher = FakeDispatcher()
    emitter = StreamingEventEmitter(dispatcher=dispatcher, user_id=1, run_id="r", exchange_id="e")
    await router.process_streaming("hi", emitter, context=None)

    deltas = [p for _, p in dispatcher.calls if p["kind"] == ChatStreamKind.DELTA]
    assert [d["text"] for d in deltas] == chunks
    assert dispatcher.calls[-1][1]["kind"] == ChatStreamKind.DONE


class _StubUser:
    def __init__(self, user_id: int):
        self.id = user_id


class _StubModelRegistry:
    """Captures set/unset_request_id calls so tests can assert scope cleanup."""

    def __init__(self):
        self.scopes: list[str] = []
        self.unset: list[str] = []

    def set_request_id(self, request_id: str) -> None:
        self.scopes.append(request_id)

    def unset_request_id(self, request_id: str) -> None:
        self.unset.append(request_id)


class _StubApp:
    def __init__(self, dispatcher):
        self._dispatcher = dispatcher
        self.toolbox = None
        self.model = _StubModelRegistry()

    def resolve_or_none(self, cls):
        if cls is SSEEventDispatcher:
            return self._dispatcher
        return None


class _StubTrans:
    def __init__(self, dispatcher):
        self.app = _StubApp(dispatcher)


def _make_agent_service(agent_instance):
    """Build an AgentService whose registry returns the given agent."""

    class _StubRegistry:
        def get_agent(self, agent_type, deps):
            return agent_instance

    service = AgentService.__new__(AgentService)
    service.config = None
    service.job_manager = None
    service.registry = _StubRegistry()
    return service


@pytest.mark.asyncio
async def test_start_streaming_run_returns_run_id_and_invokes_on_complete(monkeypatch):
    dispatcher = FakeDispatcher()

    response = AgentResponse(content="ok", agent_type=AgentType.ROUTER, confidence="high")

    class _StubAgent:
        async def process_streaming(self, query, emitter, context):
            await emitter.delta("ok")
            await emitter.done(final_content="ok")
            return response

    service = _make_agent_service(_StubAgent())
    trans = _StubTrans(dispatcher)
    user = _StubUser(user_id=123)

    # Use a per-test registry so this is isolated from concurrent tests.
    fresh_registry = ChatRunRegistry(max_per_user=2)
    monkeypatch.setattr("galaxy.managers.agents.get_run_registry", lambda: fresh_registry)

    completed: list[tuple[str, AgentResponse]] = []

    async def on_complete(run_id, agent_response):
        completed.append((run_id, agent_response))

    run_id = await service.start_streaming_run(
        trans=trans,
        user=user,
        query="hello",
        agent_type="router",
        context=None,
        exchange_id="exch-1",
        on_complete=on_complete,
    )

    assert isinstance(run_id, str) and run_id.startswith("chatrun-")
    # Run was registered synchronously.
    task = fresh_registry.get(run_id)
    assert task is not None
    await task
    # Yield once so the registry done-callback runs.
    await asyncio.sleep(0)
    assert completed == [(run_id, response)]


@pytest.mark.asyncio
async def test_start_streaming_run_calls_on_complete_with_none_on_failure(monkeypatch):
    dispatcher = FakeDispatcher()

    class _BoomAgent:
        async def process_streaming(self, query, emitter, context):
            raise RuntimeError("boom")

    service = _make_agent_service(_BoomAgent())
    trans = _StubTrans(dispatcher)
    user = _StubUser(user_id=7)

    fresh_registry = ChatRunRegistry(max_per_user=2)
    monkeypatch.setattr("galaxy.managers.agents.get_run_registry", lambda: fresh_registry)

    completed: list[tuple[str, object]] = []

    async def on_complete(run_id, agent_response):
        completed.append((run_id, agent_response))

    run_id = await service.start_streaming_run(
        trans=trans,
        user=user,
        query="hi",
        agent_type="router",
        context=None,
        exchange_id=None,
        on_complete=on_complete,
    )

    task = fresh_registry.get(run_id)
    assert task is not None
    await task
    await asyncio.sleep(0)
    assert completed == [(run_id, None)]
    # Even when the run errors, the per-task DB scope is cleaned up.
    assert trans.app.model.scopes == trans.app.model.unset


@pytest.mark.asyncio
async def test_start_streaming_run_scopes_a_fresh_request_id(monkeypatch):
    """Background runs must own a fresh DB session scope and clean it up.

    The originating HTTP request's scope is closed by the FastAPI cleanup
    middleware once the POST returns; the background task would otherwise
    leak a session under the stale request_id. Mirrors the Celery /
    job-runner pattern.
    """
    dispatcher = FakeDispatcher()
    response = AgentResponse(content="ok", agent_type=AgentType.ROUTER, confidence="high")

    class _StubAgent:
        async def process_streaming(self, query, emitter, context):
            return response

    service = _make_agent_service(_StubAgent())
    trans = _StubTrans(dispatcher)
    user = _StubUser(user_id=1)

    fresh_registry = ChatRunRegistry(max_per_user=2)
    monkeypatch.setattr("galaxy.managers.agents.get_run_registry", lambda: fresh_registry)

    async def on_complete(run_id, agent_response):
        pass

    run_id = await service.start_streaming_run(
        trans=trans,
        user=user,
        query="hi",
        agent_type="router",
        context=None,
        exchange_id=None,
        on_complete=on_complete,
    )
    await fresh_registry.get(run_id)
    await asyncio.sleep(0)

    # Exactly one set/unset, with matching ids -- no leak.
    assert len(trans.app.model.scopes) == 1
    assert trans.app.model.scopes == trans.app.model.unset


@pytest.mark.asyncio
async def test_start_streaming_run_raises_when_dispatcher_missing(monkeypatch):
    class _StubAgent:
        async def process_streaming(self, query, emitter, context):
            return None

    service = _make_agent_service(_StubAgent())
    trans = _StubTrans(dispatcher=None)
    user = _StubUser(user_id=1)

    fresh_registry = ChatRunRegistry(max_per_user=2)
    monkeypatch.setattr("galaxy.managers.agents.get_run_registry", lambda: fresh_registry)

    async def on_complete(run_id, agent_response):
        pass

    with pytest.raises(RuntimeError, match="SSEEventDispatcher"):
        await service.start_streaming_run(
            trans=trans,
            user=user,
            query="q",
            agent_type="router",
            context=None,
            exchange_id=None,
            on_complete=on_complete,
        )
