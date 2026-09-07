import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from local_codex.app import app, monitor_events
from local_codex.service import _is_codex_title_request, _prepare_body
from local_codex.settings import SETTINGS, MODEL_CONFIG
from local_codex.telemetry import TelemetryHub
from local_codex.monitor import LeaseRegistry


class AppHelperTests(unittest.TestCase):
    def test_codex_title_request_is_separate_from_user_turn(self):
        self.assertTrue(_is_codex_title_request([{
            "role": "user",
            "content": [{"type": "input_text", "text": "Generate a concise, single-line task title of at most 36 characters."}],
        }]))

    def test_normal_prompt_is_displayed(self):
        self.assertFalse(_is_codex_title_request([{
            "role": "user",
            "content": [{"type": "input_text", "text": "Bitte generiere komplizierten C++ Code."}],
        }]))

    def test_upstream_request_never_requests_reasoning_summary(self):
        decision = SimpleNamespace(model="local-codex-build:latest", session_id="s")
        runtime = SimpleNamespace(settings=SETTINGS, model_config=MODEL_CONFIG, store=Mock())
        runtime.store.expand.return_value = []
        prepared, _ = _prepare_body(
            runtime,
            {"input": "test", "reasoning": {"summary": "auto", "effort": "low"}},
            {}, decision,
        )
        self.assertEqual("xhigh", prepared["reasoning"]["effort"])
        self.assertNotIn("summary", prepared["reasoning"])

    def test_live_monitor_event_endpoint_is_registered(self):
        self.assertIn("/monitor/events", app.openapi()["paths"])

    def test_live_monitor_endpoint_returns_event_stream(self):
        request = SimpleNamespace(is_disconnected=lambda: None)
        runtime = SimpleNamespace(telemetry=TelemetryHub(), leases=LeaseRegistry(), managed=False)
        request.app = SimpleNamespace(state=SimpleNamespace(runtime=runtime))
        async def disconnected():
            return False
        request.is_disconnected = disconnected
        async def first_event():
            response = await monitor_events(request)
            chunk = await anext(response.body_iterator)
            await response.body_iterator.aclose()
            return response, chunk
        response, chunk = asyncio.run(first_event())
        self.assertEqual("text/event-stream", response.media_type)
        self.assertIn("event: snapshot", chunk)
        self.assertIn('"schema_version":5', chunk)


if __name__ == "__main__":
    unittest.main()
