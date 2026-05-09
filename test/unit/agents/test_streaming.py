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
