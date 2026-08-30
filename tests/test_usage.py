import tempfile
import unittest
from pathlib import Path

from local_codex.usage import TokenUsage, UsageStore, estimate_cost, usage_from_response


class UsageTests(unittest.TestCase):
    def test_usage_parses_openai_and_ollama_shapes(self):
        self.assertEqual(TokenUsage(12, 5), usage_from_response({"usage": {"input_tokens": 12, "output_tokens": 5}}))
        self.assertEqual(TokenUsage(12, 5), usage_from_response({"usage": {"prompt_eval_count": 12, "eval_count": 5}}))

    def test_cost_and_summary_are_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            usage = TokenUsage(1_000_000, 1_000_000)
            store.record(session_id="s", turn_id="t", model="local-codex-build:latest", usage=usage)
            summary = store.summary(None)
            store.close()
        self.assertEqual(2_000_000, summary["total_tokens"])
        self.assertEqual(1.4, summary["comparison_cost_usd"])
        self.assertEqual(estimate_cost(usage), summary["estimated_saved_usd"])


if __name__ == "__main__":
    unittest.main()
