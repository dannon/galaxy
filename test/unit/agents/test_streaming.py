import asyncio

import pytest

from galaxy.agents.base import (
    AgentResponse,
    AgentType,
    BaseGalaxyAgent,
)
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
