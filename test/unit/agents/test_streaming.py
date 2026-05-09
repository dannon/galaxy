import pytest

from galaxy.agents.streaming import (
    ChatStreamEvent,
    ChatStreamKind,
    StreamingEventEmitter,
)


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
