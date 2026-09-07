"""Acceptance tests for the deliberately incomplete benchmark fixture, not repository CI."""
import unittest
import bench_project as project


class BuildChecks(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual([0, 3, 5], [project.clamp(x, 0, 5) for x in (-1, 3, 9)])
        with self.assertRaises(ValueError):
            project.clamp(1, 5, 0)

    def test_parse_ids(self):
        self.assertEqual([1, 2, 0], project.parse_ids(" 1,2, 0 "))
        for value in ("", "1,,2", "-1", "x"):
            with self.assertRaises(ValueError):
                project.parse_ids(value)

    def test_ordered_unique(self):
        values = [3, 1, 3, 2, 1]
        self.assertEqual([3, 1, 2], project.ordered_unique(values))
        self.assertEqual([3, 1, 3, 2, 1], values)


class RecoveryChecks(unittest.TestCase):
    def test_median(self):
        values = [4, 1, 3, 2]
        self.assertEqual(2.5, project.median(values))
        self.assertEqual([4, 1, 3, 2], values)
        with self.assertRaises(ValueError):
            project.median([])

    def test_percentage(self):
        self.assertEqual(25, project.percentage(1, 4))
        with self.assertRaises(ValueError):
            project.percentage(1, 0)

    def test_chunks(self):
        self.assertEqual([[1, 2], [3]], project.chunks([1, 2, 3], 2))
        self.assertEqual([], project.chunks([], 2))
        with self.assertRaises(ValueError):
            project.chunks([1], 0)
