from typing import Any

from starlette.background import BackgroundTask
from starlette.responses import Response
from starlette.types import (
    Message,
    Receive,
    Scope,
    Send,
)

from galaxy.managers.audit import AuditAttempt


class AuditedResponse(Response):
    """Settles an audit attempt when the wrapped response actually starts.

    Range validation, x-accel-redirect and file errors all happen inside the
    response, after the route has returned; recording success any earlier would
    claim access to content that was never sent. A success event therefore means
    the response started with a status below 400 -- handed to the server, not
    delivered.
    """

    def __init__(self, response: Response, attempt: AuditAttempt) -> None:
        # Deliberately no super().__init__(): this shares the wrapped response's
        # headers and forwards the attributes FastAPI reads and sets after the route.
        self.response = response
        self.attempt = attempt
        self.raw_headers = response.raw_headers

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @status_code.setter
    def status_code(self, value: int) -> None:
        self.response.status_code = value

    @property
    def background(self) -> BackgroundTask | None:
        return self.response.background

    @background.setter
    def background(self, value: BackgroundTask | None) -> None:
        self.response.background = value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def send_and_settle(message: Message) -> None:
            nonlocal started
            await send(message)
            # Settled only once the server has taken the start message.
            if message["type"] == "http.response.start" and not started:
                started = True
                self.attempt.response_started(message["status"])

        try:
            await self.response(scope, receive, send_and_settle)
        except BaseException as exc:
            if not started:
                self.attempt.failed_with(exc, "respond")
            raise


def audited_response(response: Any, attempt: AuditAttempt) -> Any:
    if not attempt.active:
        return response
    attempt.hand_off()
    return AuditedResponse(response, attempt)
