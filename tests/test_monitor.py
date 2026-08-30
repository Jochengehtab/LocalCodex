import unittest

from local_codex.monitor import LeaseRegistry


class MonitorLeaseTests(unittest.TestCase):
    def test_parallel_leases_and_release(self):
        registry = LeaseRegistry(ttl_seconds=15)
        registry.register("one", 1, now=100)
        registry.register("two", 2, now=100)
        self.assertEqual(2, registry.snapshot(now=101)["active_count"])
        registry.release("one", now=102)
        self.assertEqual(1, registry.snapshot(now=102)["active_count"])
        registry.release("two", now=103)
        state = registry.snapshot(now=108)
        self.assertEqual(0, state["active_count"])
        self.assertEqual(5, state["empty_seconds"])

    def test_stale_lease_is_reaped(self):
        registry = LeaseRegistry(ttl_seconds=10)
        registry.register("stale", 1, now=10)
        state = registry.snapshot(now=21)
        self.assertEqual(0, state["active_count"])
        self.assertEqual(1, state["stale_removed"])


if __name__ == "__main__":
    unittest.main()
