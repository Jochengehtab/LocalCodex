import unittest
from unittest.mock import patch

from local_codex.setup import CONTEXT_CANDIDATES, benchmark_contexts


class SetupContextTests(unittest.TestCase):
    def test_long_context_profiles_are_available_for_benchmarking(self):
        self.assertEqual((8192, 16384, 32768, 65536, 131072), CONTEXT_CANDIDATES)

    @patch("local_codex.setup.remove_alias")
    @patch("local_codex.setup.unload_model")
    @patch("local_codex.setup.create_alias")
    @patch("local_codex.setup.memory_snapshot", return_value=(16 * 1024**3, 0))
    @patch("local_codex.setup.benchmark_model")
    def test_benchmark_recommends_context_per_model(self, benchmark, _memory, _create, _unload, remove):
        def result(_alias, role, repetitions=2):
            context = int(_alias.rsplit("-", 1)[1])
            success = not (role == "vision" and context > 8192)
            return {
                "successes": repetitions if success else 0,
                "repetitions": repetitions,
                "median_seconds": 1.0,
                "median_tokens_per_second": 4.0,
                "minimum_available_bytes": 8 * 1024**3,
                "maximum_swap_bytes": 0,
            }
        benchmark.side_effect = result
        recommendations, report = benchmark_contexts(contexts=(8192, 16384), repetitions=1)
        self.assertEqual({"plan": 16384, "build": 16384, "vision": 8192}, recommendations)
        self.assertEqual(recommendations, report["recommendations"])
        self.assertEqual(6, remove.call_count)


if __name__ == "__main__":
    unittest.main()
