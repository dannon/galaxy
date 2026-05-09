"""Streaming primitives for ChatGXY.

The emitter is the single point where in-process agent events are translated
into SSE payloads dispatched via ``SSEEventDispatcher``. Every event carries a
``run_id`` so the browser can route it to the right in-progress message, and a
monotonic ``seq`` so out-of-order delivery (which Kombu does not guarantee in
the abstract) can be reordered client-side.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import (
    dataclass,
    field,
)
from enum import Enum
from typing import (
    Any,
    Optional,
    Protocol,
)
from uuid import uuid4

log = logging.getLogger(__name__)


class ChatStreamKind(str, Enum):
    DELTA = "delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    HANDOFF = "handoff"
    DONE = "done"
    ERROR = "error"


class _DispatcherProtocol(Protocol):
    def chat_event(self, user_id: int, payload: dict[str, Any], event_id: Optional[str] = None) -> None: ...


@dataclass
class StreamingEventEmitter:
    dispatcher: _DispatcherProtocol
    user_id: int
    run_id: str
    exchange_id: Optional[str]
    _seq: int = 0
    _terminal: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def delta(self, text: str) -> None:
        await self._emit(ChatStreamKind.DELTA, {"text": text})

    async def tool_call_start(self, tool_name: str, tool_call_id: str, args: Optional[dict] = None) -> None:
        await self._emit(
            ChatStreamKind.TOOL_CALL_START,
            {"tool_name": tool_name, "tool_call_id": tool_call_id, "args": args or {}},
        )

    async def tool_call_end(self, tool_call_id: str, ok: bool = True, error: Optional[str] = None) -> None:
        await self._emit(
            ChatStreamKind.TOOL_CALL_END,
            {"tool_call_id": tool_call_id, "ok": ok, "error": error},
        )

    async def handoff(self, target_agent: str) -> None:
        await self._emit(ChatStreamKind.HANDOFF, {"target_agent": target_agent})

    async def done(self, final_content: str, agent_response: Optional[dict] = None) -> None:
        await self._emit(
            ChatStreamKind.DONE,
            {"final_content": final_content, "agent_response": agent_response},
        )

    async def error(self, message: str) -> None:
        await self._emit(ChatStreamKind.ERROR, {"message": message})

    async def _emit(self, kind: ChatStreamKind, body: dict[str, Any]) -> None:
        async with self._lock:
            if self._terminal:
                return
            payload = {
                "kind": kind.value,
                "run_id": self.run_id,
                "exchange_id": self.exchange_id,
                "seq": self._seq,
                **body,
            }
            self._seq += 1
            if kind in (ChatStreamKind.DONE, ChatStreamKind.ERROR):
                self._terminal = True
            try:
                self.dispatcher.chat_event(self.user_id, payload)
            except Exception:
                log.exception("Failed to dispatch chat_event seq=%s run_id=%s", payload["seq"], self.run_id)


@dataclass
class ChatStreamEvent:
    """Convenience type for tests; mirrors the wire payload."""

    kind: ChatStreamKind
    run_id: str
    exchange_id: Optional[str]
    seq: int
    body: dict[str, Any]


def new_run_id() -> str:
    return f"chatrun-{uuid4().hex}"
