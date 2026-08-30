import unittest
import asyncio
from types import SimpleNamespace

from local_codex.app import app, monitor_events, _is_codex_title_request, _prepare_body


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
        decision = SimpleNamespace(model="local-codex-build:latest")
        prepared, _ = _prepare_body(
            {"input": "test", "reasoning": {"summary": "auto", "effort": "low"}},
            {}, decision,
        )
        self.assertEqual("xhigh", prepared["reasoning"]["effort"])
        self.assertNotIn("summary", prepared["reasoning"])

    def test_live_monitor_event_endpoint_is_registered(self):
        self.assertIn("/monitor/events", {route.path for route in app.routes})

    def test_live_monitor_endpoint_returns_event_stream(self):
        request = SimpleNamespace(is_disconnected=lambda: None)
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
        self.assertIn('"schema_version":4', chunk)


if __name__ == "__main__":
    unittest.main()
