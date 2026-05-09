"""Integration test for the ChatGXY streaming round-trip.

Opens ``/api/events/stream`` against a real Galaxy server, fires
``POST /api/chat?stream=true``, and asserts that ``chat_event`` SSE frames
arrive in the expected ``delta`` -> ``done`` shape with a matching ``run_id``.

The static agent backend (configured by default in ``driver_util.py``) replaces
the LLM with deterministic canned responses, so this test runs in CI without
any external API keys.
"""

import json
from urllib.parse import urljoin
from uuid import uuid4

from galaxy_test.base.sse import SSELineListener
from galaxy_test.driver.integration_util import IntegrationTestCase


class TestChatStreamingIntegration(IntegrationTestCase):
    framework_tool_and_types = False

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        config["enable_chat_streaming"] = True
        # AI must look configured for the chat endpoint to be willing to dispatch
        # to the agent system; the static backend short-circuits before any real
        # call is made.
        config.setdefault("ai_api_key", "static-test-key")
        config.setdefault("ai_model", "static-test-model")

    def _stream_url(self) -> str:
        return urljoin(self.url, "api/events/stream")

    def test_streaming_post_returns_run_id_and_exchange_id(self):
        """The streaming POST should return immediately with a run_id and exchange_id."""
        email = f"chat_stream_shape_{uuid4()}@galaxy.test"
        self._setup_user(email)
        with self._different_user(email):
            response = self._post(
                "chat?stream=true&query=hello&agent_type=auto",
                data={},
                json=True,
            )
        self._assert_status_code_is_ok(response)
        body = response.json()
        assert body.get("streaming") is True, f"Expected streaming=True, got {body}"
        assert body.get("run_id"), f"Expected non-empty run_id, got {body}"
        assert body.get("exchange_id"), f"Expected non-empty exchange_id, got {body}"

    def test_streaming_run_emits_delta_then_done(self):
        """Open the SSE stream, fire a streaming chat run, observe delta + done frames."""
        email = f"chat_stream_round_{uuid4()}@galaxy.test"
        self._setup_user(email)
        _, api_key = self._setup_user_get_key(email)

        listener = SSELineListener(self._stream_url(), api_key)
        listener.start()
        try:
            with self._different_user(email):
                response = self._post(
                    "chat?stream=true&query=hello&agent_type=auto",
                    data={},
                    json=True,
                )
            self._assert_status_code_is_ok(response)
            body = response.json()
            run_id = body["run_id"]
            assert run_id, f"Expected run_id, got {body}"

            # Wait until at least one chat_event for this run_id has the terminal
            # ``done`` kind. Filtering by run_id shields the assertion from any
            # unrelated chat_event traffic that happens to be on the wire.
            def _is_done_for_run(event: dict) -> bool:
                payload = json.loads(event["data"])
                return payload.get("run_id") == run_id and payload.get("kind") == "done"

            chat_events = listener.wait_for_event_where("chat_event", _is_done_for_run)
        finally:
            listener.stop()

        run_payloads = [json.loads(e["data"]) for e in chat_events if json.loads(e["data"]).get("run_id") == run_id]
        kinds = [p["kind"] for p in run_payloads]
        assert "delta" in kinds, f"Expected at least one delta frame, got kinds={kinds}"
        assert kinds[-1] == "done", f"Expected last frame to be done, got kinds={kinds}"

        # Sequence numbers should be monotonic per the emitter contract.
        seqs = [p["seq"] for p in run_payloads]
        assert seqs == sorted(seqs), f"Expected monotonic seq numbers, got {seqs}"

        done_payload = run_payloads[-1]
        assert done_payload.get("final_content"), f"Expected non-empty final_content, got {done_payload}"
