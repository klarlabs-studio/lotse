"""Paired statistics for the preregistered decision rule."""
import math
import unittest

from paired_stats import holm, required_n, sign_flip_p, wilson


class SignFlipTests(unittest.TestCase):
    def test_exact_p_counts_every_sign_assignment(self):
        # Four positive differences: only the all-positive assignment is as extreme.
        self.assertAlmostEqual(sign_flip_p([1, 2, 3, 4]), 1 / 16)
        self.assertEqual(sign_flip_p([0, 0, 0]), 1.0)

    def test_direction_matters(self):
        self.assertGreater(sign_flip_p([-1, -2, -3, -4]), 0.9)

    def test_monte_carlo_is_deterministic_and_close_to_exact(self):
        diffs = [0.3, -0.1, 0.5, 0.2, 0.4, -0.2, 0.6, 0.1, 0.3, 0.2,
                 0.5, -0.1, 0.4, 0.3, 0.2, 0.1, 0.6, 0.2, -0.3, 0.4]
        exact = sign_flip_p(diffs, exact_limit=20)
        sampled = sign_flip_p(diffs, exact_limit=0, resamples=200_000)
        self.assertEqual(sampled, sign_flip_p(diffs, exact_limit=0, resamples=200_000))
        self.assertAlmostEqual(sampled, exact, delta=0.003)

    def test_rejects_empty_and_non_finite_input(self):
        for bad in ([], [1.0, math.nan], [math.inf]):
            with self.assertRaises(ValueError):
                sign_flip_p(bad)


class HolmTests(unittest.TestCase):
    def test_step_down_stops_at_first_failure(self):
        self.assertEqual(holm([0.01, 0.04, 0.03], 0.05), [True, False, False])
        self.assertEqual(holm([0.001, 0.02, 0.04], 0.05), [True, True, True])
        self.assertEqual(holm([0.2, 0.001], 0.05), [False, True])


class WilsonTests(unittest.TestCase):
    def test_interval_brackets_proportion_and_handles_zero(self):
        low, high = wilson(0, 70)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.0521, places=3)
        low, high = wilson(9, 18)
        self.assertAlmostEqual(low, 0.2903, places=3)
        self.assertAlmostEqual(high, 0.7097, places=3)
        with self.assertRaises(ValueError):
            wilson(1, 0)


class SampleSizeTests(unittest.TestCase):
    def test_one_sided_non_inferiority_planning(self):
        # (1.6449 + 0.8416)^2 * sd^2 / margin^2 = 6.18 for sd = margin.
        self.assertEqual(required_n(sd=0.1, margin=0.1), 7)
        self.assertEqual(required_n(sd=0.1, margin=0.03), 69)
        self.assertEqual(required_n(sd=0.0, margin=0.05), 2)


if __name__ == "__main__":
    unittest.main()
