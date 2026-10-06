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
        # ~250 m per 30 s = 30 km/h, held over two steps.
        fixes = _fixes((0, 1.08, 2.05), (30, 1.09, 2.05),
                       (60, 1.10, 2.05))
        self.assertAlmostEqual(_u.speed_kmh(fixes), 30, delta=3)

    def test_one_hop_is_too_thin_to_believe(self):
        """Fifteen seconds of movement is not a speed.

        Taken alone it put the first live forecast of the morning out by ten
        minutes; the bus has usually just pulled away and is still crawling.
        """
        fixes = _fixes((0, 1.08, 2.05), (30, 1.09, 2.05))
        self.assertIsNone(_u.speed_kmh(fixes))

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
    NOW = T0 + dt.timedelta(seconds=60)

    def test_moving_towards_the_stop(self):
        # ~650 m still to go at 30 km/h -> a couple of minutes once the road
        # factor is applied.
        fixes = _fixes((0, 1.04, 2.08), (30, 1.05, 2.08),
                       (60, 1.06, 2.08))
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


class TestLeadingStandstill(unittest.TestCase):
    """The bus waits at the start of its line before it sets off.

    Averaging that wait together with the first metres of driving gave 3.5
    km/h - just above the standing threshold, so it was believed - and the
    forecast jumped to twenty minutes out before collapsing back over the next
    ninety seconds. Reproduced from the run of 6 October.
    """

    def _pulling_away(self):
        # Parked at the line start, then two 98 m hops as it pulls away.
        points = [(s, 1.18, 2.03) for s in range(0, 91, 15)]
        points += [(105, 1.20, 2.03), (120, 1.21, 2.03)]
        return _fixes(*points)

    def test_standstill_is_not_averaged_into_the_speed(self):
        kmh = _u.speed_kmh(self._pulling_away())
        self.assertIsNotNone(kmh)
        # 98 m in 15 s is roughly 23 km/h; the mixed window gave 3.5.
        self.assertGreater(kmh, 15, kmh)

    def test_forecast_does_not_balloon_when_the_bus_sets_off(self):
        fixes = self._pulling_away()
        now = fixes[-1][0]
        eta = _u.eta_from_position(fixes, STOP, now)
        self.assertIsNotNone(eta)
        minutes = (eta - now).total_seconds() / 60
        # The observed value was 19.5 minutes; the bus was there in about 4.
        self.assertLess(minutes, 8, minutes)

    def test_the_very_first_hop_still_promises_nothing(self):
        """Wait for a second hop rather than publish a wild first guess."""
        points = [(s, 1.18, 2.03) for s in range(0, 106, 15)]
        points.append((120, 1.20, 2.03))
        self.assertIsNone(_u.speed_kmh(_fixes(*points)))

    def test_a_bus_that_only_stands_still_still_promises_nothing(self):
        fixes = _fixes(*[(s, 1.18, 2.03) for s in range(0, 120, 15)])
        self.assertIsNone(_u.speed_kmh(fixes))


class TestStopServedThroughAGap(unittest.TestCase):
    """The feed went quiet for 3 min 20 s exactly across the stop.

    Last fix before the gap 495 m out, first after it 368 m, closest ever
    359 m - never inside the 60 m that counts as arrived. The forecast kept
    running while the bus drove off, so the remaining minutes grew again
    instead of reaching zero.
    """

    def test_close_then_drawing_away_counts_as_served(self):
        self.assertTrue(_u.stop_was_served(359, 700))

    def test_still_approaching_does_not(self):
        self.assertFalse(_u.stop_was_served(495, 495))

    def test_a_small_wobble_does_not(self):
        self.assertFalse(_u.stop_was_served(359, 480))

    def test_a_bus_that_never_came_near_does_not(self):
        """Passing a kilometre away is another line, not our stop."""
        self.assertFalse(_u.stop_was_served(1662, 2200))

    def test_unknown_distances_do_not(self):
        self.assertFalse(_u.stop_was_served(None, 700))
        self.assertFalse(_u.stop_was_served(359, None))


class TestMinutesUntil(unittest.TestCase):
    """The countdown an ESP32 display and a voice answer read.

    It must be derived from the timestamp at the moment it is read. Taken
    from the coordinator's snapshot it froze whenever polling paused: on one
    Friday it sat at 454 minutes from 08:45 until 14:45, while the timestamp
    beside it was right the whole time.
    """

    def test_counts_down_as_time_passes(self):
        eta = T0 + dt.timedelta(minutes=30)
        self.assertEqual(_u.minutes_until(eta, T0), 30)
        self.assertEqual(_u.minutes_until(eta, T0 + dt.timedelta(minutes=25)), 5)

    def test_never_goes_negative(self):
        """A bus that is already past reads zero, not minus four."""
        eta = T0 - dt.timedelta(minutes=4)
        self.assertEqual(_u.minutes_until(eta, T0), 0)

    def test_hours_ahead_is_still_a_number(self):
        """Asked at breakfast about the afternoon run, it must answer."""
        eta = T0 + dt.timedelta(hours=8)
        self.assertEqual(_u.minutes_until(eta, T0), 480)

    def test_no_forecast_is_none(self):
        self.assertIsNone(_u.minutes_until(None, T0))
        self.assertIsNone(_u.minutes_until(T0, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
