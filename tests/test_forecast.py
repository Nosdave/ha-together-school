#!/usr/bin/env python3
"""Tests for the arrival forecast helpers.

These decide when a lift gets called and when a voice announcement fires, so
the failure modes that matter are "promises an arrival it cannot know" and
"jitters across the trigger threshold".

    python3 tests/test_forecast.py
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import unittest

_UTIL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components" / "together_school" / "util.py"
)
_spec = importlib.util.spec_from_file_location("ts_util", _UTIL)
_u = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_u)

STOP = (1.12, 2.07)
T0 = dt.datetime(2026, 9, 25, 5, 33, tzinfo=dt.timezone.utc)


def _fixes(*points):
    """(seconds_offset, lat, lon) -> the shape the helpers expect."""
    return [(T0 + dt.timedelta(seconds=s), la, lo) for s, la, lo in points]


class TestDistance(unittest.TestCase):
    def test_known_separation(self):
        # The start point observed every morning, ~945 m from the stop.
        d = _u.distance_m((1.18, 2.03), STOP)
        self.assertAlmostEqual(d, 945, delta=25)

    def test_missing_input(self):
        self.assertIsNone(_u.distance_m(None, STOP))
        self.assertIsNone(_u.distance_m(STOP, None))


class TestMedian(unittest.TestCase):
    def test_odd_and_even(self):
        self.assertEqual(_u.median([3, 1, 2]), 2)
        self.assertEqual(_u.median([1, 2, 3, 4]), 2.5)

    def test_empty_is_none(self):
        self.assertIsNone(_u.median([]))
        self.assertIsNone(_u.median(None))

    def test_outlier_does_not_dominate(self):
        """One freak run must not move the everyday expectation."""
        self.assertEqual(_u.median([2, 3, 3, 4, 45]), 3)


class TestSpeed(unittest.TestCase):
    def test_typical_city_speed(self):
        # ~250 m in 30 s = 30 km/h
        fixes = _fixes((0, 1.08, 2.05), (30, 1.09, 2.05))
        self.assertAlmostEqual(_u.speed_kmh(fixes), 30, delta=3)

    def test_standing_bus_returns_none(self):
        """A stationary bus must not yield a speed to divide by."""
        fixes = _fixes((0, 1.12, 2.08), (30, 1.13, 2.09))
        self.assertIsNone(_u.speed_kmh(fixes))

    def test_absurd_jump_rejected(self):
        fixes = _fixes((0, 1.08, 2.05), (5, 1.23, 2.05))
        self.assertIsNone(_u.speed_kmh(fixes))

    def test_too_few_fixes(self):
        self.assertIsNone(_u.speed_kmh(_fixes((0, 1.07, 4.35))))
        self.assertIsNone(_u.speed_kmh([]))


class TestEtaFromPosition(unittest.TestCase):
    NOW = T0 + dt.timedelta(seconds=30)

    def test_moving_towards_the_stop(self):
        # ~650 m still to go at 30 km/h -> a couple of minutes once the road
        # factor is applied.
        fixes = _fixes((0, 1.05, 2.08), (30, 1.06, 2.08))
        eta = _u.eta_from_position(fixes, STOP, self.NOW)
        self.assertIsNotNone(eta)
        minutes = (eta - self.NOW).total_seconds() / 60
        self.assertTrue(1 < minutes < 8, minutes)

    def test_already_at_the_stop(self):
        fixes = _fixes((0, 1.14, 2.10), (30, 1.12, 2.08))
        self.assertEqual(_u.eta_from_position(fixes, STOP, self.NOW), self.NOW)

    def test_standing_far_away_promises_nothing(self):
        """Better no forecast than one invented from a stationary bus."""
        fixes = _fixes((0, 1.02, 2.01), (30, 1.03, 2.02))
        self.assertIsNone(_u.eta_from_position(fixes, STOP, self.NOW))

    def test_no_stop_known(self):
        fixes = _fixes((0, 1.08, 2.08), (30, 1.09, 2.08))
        self.assertIsNone(_u.eta_from_position(fixes, None, self.NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
