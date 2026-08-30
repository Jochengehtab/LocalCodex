import tempfile
import unittest
import json
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

    def test_session_statistics_titles_pagination_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            store.record(session_id="s1", turn_id="t1", model="build", usage=TokenUsage(10, 4))
            store.record(session_id="s1", turn_id="t2", model="plan", usage=TokenUsage(7, 3))
            store.record(session_id="s2", turn_id="t3", model="build", usage=TokenUsage(5, 2))
            store.set_session_title("s1", "  Mein   Test  ")
            session = store.statistics(period="all", session_id="s1")
            all_sessions = store.statistics(period="all", page_size=1)
            exported_json = json.loads(store.export(period="all", session_id="s1", format="json"))
            exported_csv = store.export(period="all", session_id="s1", format="csv")
            store.close()
        self.assertEqual(17, session["input_tokens"])
        self.assertEqual(2, session["turns"])
        self.assertEqual(2, all_sessions["session_count"])
        self.assertEqual(2, all_sessions["pagination"]["total_pages"])
        self.assertEqual(2, len(exported_json))
        self.assertIn("session_id,turn_id", exported_csv)


if __name__ == "__main__":
    unittest.main()
