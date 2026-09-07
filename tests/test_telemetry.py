import unittest

from local_codex.telemetry import TelemetryHub, TelemetrySSEParser
from local_codex.usage import TokenUsage


class FakeClock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class TelemetryTests(unittest.TestCase):
    def test_stream_ignores_all_reasoning_and_estimates_visible_output(self):
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
        self.assertNotIn("thinking_summary", snapshot)
        self.assertEqual("generating", snapshot["phase"])
        self.assertGreater(snapshot["estimated_output_tokens"], 0)
        self.assertNotIn("estimated_reasoning_tokens", snapshot)
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
        before = hub.snapshot()["estimated_output_tokens"]
        parser = TelemetrySSEParser(None)
        parser.feed(b'data: {"type":"response.output_text.delta","delta":"hidden title"}\n\n')
        self.assertEqual(before, hub.snapshot()["estimated_output_tokens"])

    def test_snapshot_includes_session_aggregate_with_live_overlay(self):
        hub = TelemetryHub()
        hub.start(
            model="m", session_id="s", turn_id="t",
            session_statistics={"input_tokens": 20, "output_tokens": 10, "estimated_saved_usd": 0.1},
            input_tokens_estimate=8,
            comparison_model="gpt-5.6-luna",
        )
        hub.output_delta("12345678")
        snapshot = hub.snapshot()
        self.assertEqual(20, snapshot["session"]["input_tokens"])
        self.assertEqual(28, snapshot["session"]["live_input_tokens"])
        self.assertGreater(snapshot["session"]["live_output_tokens"], 10)
        self.assertGreater(snapshot["session"]["live_saved_usd"], 0.1)

    def test_schema_five_includes_revision_route_total_and_context(self):
        hub = TelemetryHub()
        initial_revision = hub.revision
        hub.update_all_statistics({"input_tokens": 100, "output_tokens": 40, "estimated_saved_usd": 1.5})
        hub.start(
            model="local-codex-plan:latest", session_id="s", turn_id="t",
            input_tokens_estimate=12,
            mode="plan", source_model="qwen3.8:27b", route_reason="plan or review",
        )
        hub.update_ollama_runtime({"models": [{"name": "local-codex-plan:latest", "context_length": 8192}]})
        hub.output_delta("12345678")
        snapshot = hub.snapshot()
        self.assertEqual(5, snapshot["schema_version"])
        self.assertGreater(snapshot["sequence"], initial_revision)
        self.assertEqual("plan", snapshot["turn"]["role"])
        self.assertEqual("plan", snapshot["turn"]["mode"])
        self.assertEqual("qwen3.8:27b", snapshot["turn"]["source_model"])
        self.assertEqual("plan or review", snapshot["turn"]["route_reason"])
        self.assertEqual(8192, snapshot["context"]["capacity_tokens"])
        self.assertEqual(112, snapshot["total"]["live_input_tokens"])
        self.assertGreater(snapshot["total"]["live_output_tokens"], 40)

    def test_small_stream_fragments_do_not_each_count_as_a_token(self):
        clock = FakeClock()
        hub = TelemetryHub(wall_clock=clock.now, monotonic_clock=clock.now)
        hub.start(model="m", session_id="s", turn_id="t")
        for fragment in "abcd":
            hub.output_delta(fragment)
        self.assertEqual(1, hub.snapshot()["tokens"]["estimated_output"])

    def test_live_rate_uses_recent_stream_window_and_reports_average(self):
        clock = FakeClock()
        hub = TelemetryHub(wall_clock=clock.now, monotonic_clock=clock.now)
        hub.start(model="m", session_id="s", turn_id="t")
        hub.output_delta("abcdefgh")
        clock.advance(1.0)
        hub.output_delta("ijklmnop")
        snapshot = hub.snapshot()
        self.assertEqual(2.0, snapshot["performance"]["tokens_per_second"])
        self.assertEqual(4.0, snapshot["performance"]["average_tokens_per_second"])
        self.assertTrue(snapshot["performance"]["estimated"])


if __name__ == "__main__":
    unittest.main()
