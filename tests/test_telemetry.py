import unittest

from local_codex.telemetry import TelemetryHub, TelemetrySSEParser
from local_codex.usage import TokenUsage


class TelemetryTests(unittest.TestCase):
    def test_stream_only_keeps_reasoning_summary_and_estimates_output(self):
        hub = TelemetryHub()
        hub.start(model="local-codex-build:latest", session_id="s", turn_id="t")
        parser = TelemetrySSEParser(hub)
        parser.feed(
            b'event: response.reasoning_summary_text.delta\n'
            b'data: {"type":"response.reasoning_summary_text.delta","delta":"Pruefe Tests. "}\n\n'
            b'event: response.reasoning_text.delta\n'
            b'data: {"type":"response.reasoning_text.delta","delta":"private raw thought"}\n\n'
            b'event: response.output_text.delta\n'
            b'data: {"type":"response.output_text.delta","delta":"Hallo Welt"}\n\n'
        )
        snapshot = hub.snapshot()
        self.assertIn("Pruefe Tests", snapshot["thinking_summary"])
        self.assertNotIn("private raw thought", snapshot["thinking_summary"])
        self.assertEqual("generating", snapshot["phase"])
        self.assertGreater(snapshot["estimated_output_tokens"], 0)
        self.assertGreater(snapshot["estimated_reasoning_tokens"], 0)
        self.assertTrue(snapshot["tokens_per_second_estimated"])

    def test_final_usage_replaces_estimates(self):
        hub = TelemetryHub()
        hub.start(model="m", session_id="s", turn_id="t")
        hub.output_delta("a fairly long generated response")
        hub.complete(TokenUsage(31, 7), comparison_cost_usd=0.12345678, comparison_model="gpt-5.6-luna")
        snapshot = hub.snapshot()
        self.assertFalse(snapshot["active"])
        self.assertEqual("completed", snapshot["phase"])
        self.assertEqual(31, snapshot["input_tokens"])
        self.assertEqual(7, snapshot["output_tokens"])
        self.assertFalse(snapshot["tokens_per_second_estimated"])
        self.assertEqual(0.12345678, snapshot["comparison_cost_usd"])
        self.assertEqual(7, snapshot["tokens"]["output"])
        self.assertTrue(snapshot["tokens"]["exact"])
        self.assertEqual("ollama.responses.usage+stream_clock", snapshot["performance"]["source"])

    def test_ollama_runtime_fields_are_normalized(self):
        hub = TelemetryHub()
        hub.start(model="m", session_id="s", turn_id="t")
        hub.update_ollama_runtime({"models": [{
            "name": "m", "size": 100, "size_vram": 80, "context_length": 32768,
            "details": {"parameter_size": "27B", "quantization_level": "Q4_K_M"},
        }]}, "0.32.15")
        snapshot = hub.snapshot()
        self.assertEqual("0.32.15", snapshot["ollama_version"])
        self.assertEqual(32768, snapshot["ollama_runtime"]["context_length"])
        self.assertEqual("ollama.api.ps", snapshot["ollama_runtime"]["source"])

    def test_disabled_stream_observer_does_not_replace_previous_turn(self):
        hub = TelemetryHub()
        hub.start(model="m", session_id="s", turn_id="t")
        hub.output_delta("visible answer")
        parser = TelemetrySSEParser(None)
        parser.feed(b'data: {"type":"response.output_text.delta","delta":"hidden title"}\n\n')
        self.assertNotIn("hidden title", hub.snapshot()["thinking_summary"])


if __name__ == "__main__":
    unittest.main()
