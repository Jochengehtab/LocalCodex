import unittest

from local_codex.setup import CONTEXT_CANDIDATES


class SetupContextTests(unittest.TestCase):
    def test_long_context_profiles_are_available_for_benchmarking(self):
        self.assertEqual((8192, 16384, 32768, 65536, 131072), CONTEXT_CANDIDATES)


if __name__ == "__main__":
    unittest.main()
