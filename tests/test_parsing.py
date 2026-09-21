#!/usr/bin/env python3
"""Unit tests for the pure parsing helpers.

Runs with plain stdlib python (no Home Assistant, no aiohttp):

    python3 tests/test_parsing.py

The fixtures mirror real responses recorded from a live deployment
(anonymised): the {status,data} envelope, /auth/users/me, the plain-array
/parents/{id}/pupils, and the all-null deliveryWithBus/location returned
outside service hours.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import unittest

# Load util.py straight from its path: importing it as part of the package
# would pull in __init__.py, which needs Home Assistant.
_UTIL_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "together_school"
    / "util.py"
)
_spec = importlib.util.spec_from_file_location("ts_util", _UTIL_PATH)
_util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_util)

extract_latlon = _util.extract_latlon
extract_pupils = _util.extract_pupils
bus_routes = _util.bus_routes
is_on_bus = _util.is_on_bus
parse_dt = _util.parse_dt
route_arrival = _util.route_arrival
route_departure = _util.route_departure
route_state = _util.route_state
bus_fix = _util.bus_fix
route_checkin = _util.route_checkin
route_checkout = _util.route_checkout
select_route = _util.select_route
pick_id = _util.pick_id
pupil_display_name = _util.pupil_display_name
route_state = _util.route_state
bus_fix = _util.bus_fix
route_checkin = _util.route_checkin
route_checkout = _util.route_checkout
parse_tenant_id = _util.parse_tenant_id
unwrap_envelope = _util.unwrap_envelope

ME = {
    "userId": "3f7c1b90-2a44-4e18-9c05-7d61e8af0b23",
    "fullName": "JANE DOE",
    "email": "jane@example.com",
    "phoneNumber": "+32470000000",
    "roles": ["parent"],
    "pathToAvatar": None,
    "birthday": None,
}

PUPILS = [
    {
        "id": "c48d05e7-91b6-4a2f-8e73-16b0d2c9f451",
        "forename": "ALEX",
        "surname": "DOE",
        "fullName": "ALEX DOE",
        "active": False,
        "address": ["Some Street, CITY, 1000, BE"],
        "accompanied": True,
        "locationOn": False,
    }
]

DELIVERY_IDLE = {
    "studentLocation": None,
    "busLocation": None,
    "schoolLocation": None,
    "stationLocation": None,
}

AGENDA_EMPTY = {"BUS_ROUTE": []}

# Real shape of a scheduled morning run (stations anonymised).
ROUTE_SCHEDULED = {
    "busId": None,
    "activeRouteId": None,
    "studentState": None,
    "routeState": None,
    "direction": "WAY_TO",
    "schedule": "05:40:00+0000",
    "arrivalTime": "06:05:00+0000",
    "checkInTime": None,
    "checkOutTime": None,
    "missedTime": None,
    "inOtherStop": False,
    "busNumber": "99XX1",
    "boardingStation": "EXAMPLE STREET (public bus stop)",
    "arrivalStation": "EXAMPLE SCHOOL / MAIN ROAD 1",
    "startTime": "2026-09-21T05:40:00+0000",
    "online": False,
    "next": "AUTO",
    "name": "99XX1",
}


class TestEnvelope(unittest.TestCase):
    def test_unwraps_success(self):
        self.assertEqual(
            unwrap_envelope({"status": "SUCCESS", "data": {"accessToken": "t"}}),
            {"accessToken": "t"},
        )

    def test_leaves_error_envelope_intact(self):
        err = {"status": "ERROR", "message": "Unauthorized exception.",
               "errors": None}
        self.assertEqual(unwrap_envelope(err), err)

    def test_passes_through_bare_payloads(self):
        self.assertEqual(unwrap_envelope([1, 2]), [1, 2])
        self.assertIsNone(unwrap_envelope(None))

    def test_does_not_unwrap_without_status(self):
        body = {"data": {"x": 1}}
        self.assertEqual(unwrap_envelope(body), body)


class TestRobustness(unittest.TestCase):
    """Shapes the author never saw must not crash or lie."""

    def test_latlon_rejects_garbage_instead_of_raising(self):
        for bad in ("nope", 42, {"lat": "abc", "lng": "x"},
                    {"lat": True, "lng": False}, {"lat": 999, "lng": 4}, {}):
            self.assertIsNone(extract_latlon(bad))

    def test_latlon_rejects_null_island(self):
        """0,0 is the backend's 'no fix', not a position off Africa."""
        self.assertIsNone(extract_latlon({"lat": 0, "lng": 0}))

    def test_latlon_accepts_strings_and_list_wrapping(self):
        self.assertEqual(
            extract_latlon({"location": {"latitude": "50.8", "longitude": "4.3"}}),
            (50.8, 4.3),
        )
        self.assertEqual(extract_latlon([{"lat": 50.8, "lng": 4.3}]), (50.8, 4.3))

    def test_timestamps_are_always_aware(self):
        """HA rejects naive datetimes on TIMESTAMP sensors."""
        for value in ("2026-09-21T05:40:00", "2026-09-21T05:40:00+0000"):
            self.assertIsNotNone(parse_dt(value).tzinfo)

    def test_missed_morning_does_not_mask_the_afternoon(self):
        morning = {"startTime": "2026-09-21T05:40:00+0000",
                   "missedTime": "2026-09-21T05:45:00+0000"}
        afternoon = {"startTime": "2026-09-21T14:30:00+0000"}
        # Deliberately passed out of order: selection must not trust list order.
        chosen = select_route([afternoon, morning])
        self.assertIs(chosen, afternoon)
        self.assertEqual(route_state(chosen), "scheduled")


# Exactly as returned by a live run (ids and names replaced).
DELIVERY_LIVE = {
    "studentLocation": None,
    "busLocation": [
        {
            "busId": "00000000-0000-0000-0000-000000000000",
            "location": {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [50.79969, 4.34165]},
                "properties": {"name": "Unknown place"},
            },
            "lastLocatedTime": "2026-09-21T05:35:44+0000",
            "actual": True,
        }
    ],
    "schoolLocation": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [50.79967, 4.37518]},
            "properties": {"name": "1180 BRUXELLES"},
        }
    ],
    "stationLocation": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [50.79475, 4.3525]},
            "properties": {"name": "1180 BRUXELLES"},
        }
    ],
}


class TestLiveDeliveryShape(unittest.TestCase):
    """The backend labels positions "Feature" but emits [lat, lng].

    GeoJSON specifies [lng, lat]. Reading these the spec way keeps both values
    in range, so the bus would appear thousands of km away and nothing would
    look broken - hence an explicit test.
    """

    def test_bus_position_is_read_as_lat_lng(self):
        lat, lon = extract_latlon(DELIVERY_LIVE["busLocation"])
        self.assertAlmostEqual(lat, 50.79969)
        self.assertAlmostEqual(lon, 4.34165)

    def test_school_and_station_too(self):
        self.assertAlmostEqual(
            extract_latlon(DELIVERY_LIVE["schoolLocation"])[0], 50.79967
        )
        self.assertAlmostEqual(
            extract_latlon(DELIVERY_LIVE["stationLocation"])[1], 4.3525
        )

    def test_spec_order_still_works_when_unambiguous(self):
        """A real [lng, lat] source with |lng| > 90 must not be misread."""
        self.assertEqual(
            extract_latlon({"geometry": {"coordinates": [-122.4, 37.8]}}),
            (37.8, -122.4),
        )

    def test_fix_metadata(self):
        meta = bus_fix(DELIVERY_LIVE)
        self.assertEqual(meta["last_located"], "2026-09-21T05:35:44+0000")
        self.assertTrue(meta["position_is_current"])

    def test_fix_metadata_on_idle_payload(self):
        self.assertEqual(bus_fix(DELIVERY_IDLE), {})

    def test_student_state_marks_on_board(self):
        """The live run carries an explicit flag alongside the timestamps."""
        self.assertEqual(
            route_state({"studentState": "IS_ON_BOARD", "online": True}),
            "on_board",
        )

    def test_student_state_not_on_board_is_on_route(self):
        self.assertEqual(
            route_state({"studentState": "IS_NOT_ON_BOARD", "online": True}),
            "on_route",
        )


# Exactly as returned once the child has boarded (ids/names replaced).
ROUTE_BOARDED = {
    "studentState": "IS_ON_BOARD",
    "routeState": "ON_TIME",
    "direction": "WAY_TO",
    "schedule": "05:40:00+0000",
    "arrivalTime": "06:05:00+0000",
    # NB: bare time-of-day, unlike startTime.
    "checkInTime": "05:50:46+0000",
    "checkOutTime": None,
    "missedTime": None,
    "inOtherStop": True,
    "startTime": "2026-09-21T05:40:00+0000",
    "online": True,
}


class TestBoardedRun(unittest.TestCase):
    def test_check_in_is_anchored_to_the_run_date(self):
        """checkInTime is a bare UTC time; raw it reads two hours early."""
        checked_in = route_checkin(ROUTE_BOARDED)
        self.assertEqual(checked_in.date(), dt.date(2026, 9, 21))
        self.assertEqual(checked_in.hour, 5)
        self.assertIsNotNone(checked_in.tzinfo)
        local = checked_in.astimezone(dt.timezone(dt.timedelta(hours=2)))
        self.assertEqual((local.hour, local.minute), (7, 50))

    def test_absent_check_out_is_none(self):
        self.assertIsNone(route_checkout(ROUTE_BOARDED))

    def test_boarded_run_is_on_board(self):
        self.assertEqual(route_state(ROUTE_BOARDED), "on_board")

    def test_check_in_after_midnight_rolls_to_the_next_day(self):
        entry = {"startTime": "2026-09-21T23:50:00+0000",
                 "checkInTime": "00:05:00+0000"}
        self.assertEqual(route_checkin(entry).day, 22)

    def test_location_type_changes_to_embarked_and_still_parses(self):
        """The GeoJSON "type" flips to Embarked once on board."""
        embarked = [{
            "location": {
                "type": "Embarked",
                "geometry": {"type": "Point",
                             "coordinates": [50.79956, 4.36468]},
            }
        }]
        self.assertEqual(extract_latlon(embarked), (50.79956, 4.36468))


class TestTenantDiscovery(unittest.TestCase):
    """The tenant is per-school, so it must be read, never assumed."""

    ENV_JS = (
        '// eslint-disable-next-line\n'
        'var APP_CONFIG = {\n'
        "  API: '/api/v1',\n"
        '  WS_URL: "wss://example.together-school.com:443/api/v1",\n'
        '  TENANT_ID: "tenant_example",\n'
        '};\n'
    )

    def test_reads_double_quoted(self):
        self.assertEqual(parse_tenant_id(self.ENV_JS), "tenant_example")

    def test_reads_single_quoted(self):
        self.assertEqual(parse_tenant_id("TENANT_ID: 'abc',"), "abc")

    def test_tolerates_spacing(self):
        self.assertEqual(parse_tenant_id('TENANT_ID   :   "x"'), "x")

    def test_missing_returns_none(self):
        self.assertIsNone(parse_tenant_id("var APP_CONFIG = {};"))

    def test_non_string_returns_none(self):
        self.assertIsNone(parse_tenant_id(None))


class TestIdentity(unittest.TestCase):
    def test_me_uses_user_id(self):
        self.assertEqual(pick_id(ME), ME["userId"])

    def test_pupil_uses_id(self):
        self.assertEqual(pick_id(PUPILS[0]), PUPILS[0]["id"])

    def test_pupils_from_plain_array(self):
        pupils = extract_pupils(PUPILS)
        self.assertEqual(list(pupils), [PUPILS[0]["id"]])
        self.assertEqual(pupils[PUPILS[0]["id"]]["forename"], "ALEX")

    def test_pupils_tolerates_wrapped_object(self):
        self.assertEqual(len(extract_pupils({"items": PUPILS})), 1)

    def test_pupils_empty(self):
        self.assertEqual(extract_pupils([]), {})

    def test_display_name_title_cases_shouting_api(self):
        self.assertEqual(pupil_display_name(PUPILS[0], "x"), "Alex")

    def test_display_name_falls_back(self):
        self.assertEqual(pupil_display_name(None, "c48d05e7-dead"), "Pupil c48d05e7")


class TestLocations(unittest.TestCase):
    def test_idle_delivery_yields_no_position(self):
        self.assertIsNone(extract_latlon(DELIVERY_IDLE["busLocation"]))

    def test_lat_lng(self):
        self.assertEqual(extract_latlon({"lat": 50.8, "lng": 4.3}), (50.8, 4.3))

    def test_latitude_longitude(self):
        self.assertEqual(
            extract_latlon({"latitude": 50.8, "longitude": 4.3}), (50.8, 4.3)
        )

    def test_nested_location(self):
        node = {"busId": "b1", "location": {"lat": 50.85, "lng": 4.34}}
        self.assertEqual(extract_latlon(node), (50.85, 4.34))

    def test_ambiguous_coordinates_use_the_backend_order(self):
        """Despite the "Feature" wrapper the backend emits [lat, lng].

        When both values could be a latitude the order is undecidable, so the
        observed convention wins - verified against a known school address.
        """
        node = {"geometry": {"coordinates": [50.85, 4.34]}}
        self.assertEqual(extract_latlon(node), (50.85, 4.34))


class TestBusRoute(unittest.TestCase):
    """The real BUS_ROUTE schema: check-in/out are TIMESTAMPS, times are UTC.

    Station names here are invented - real ones reveal a home address.
    """

    def test_empty_agenda_has_no_route(self):
        self.assertEqual(bus_routes(AGENDA_EMPTY), [])
        self.assertIsNone(select_route(bus_routes(AGENDA_EMPTY)))

    def test_scheduled_run(self):
        self.assertEqual(route_state(ROUTE_SCHEDULED), "scheduled")
        self.assertFalse(is_on_bus(ROUTE_SCHEDULED))

    def test_online_but_not_boarded_is_on_route(self):
        entry = dict(ROUTE_SCHEDULED, online=True)
        self.assertEqual(route_state(entry), "on_route")
        self.assertFalse(is_on_bus(entry))

    def test_checked_in_means_on_board(self):
        entry = dict(ROUTE_SCHEDULED, checkInTime="2026-09-21T05:42:11+0000")
        self.assertEqual(route_state(entry), "on_board")
        self.assertTrue(is_on_bus(entry))

    def test_checked_out_completes_and_clears_on_bus(self):
        entry = dict(
            ROUTE_SCHEDULED,
            checkInTime="2026-09-21T05:42:11+0000",
            checkOutTime="2026-09-21T06:07:00+0000",
        )
        self.assertEqual(route_state(entry), "completed")
        self.assertFalse(is_on_bus(entry))

    def test_missed_wins_over_everything(self):
        entry = dict(ROUTE_SCHEDULED, missedTime="2026-09-21T05:45:00+0000")
        self.assertEqual(route_state(entry), "missed")

    def test_no_entry_is_no_service(self):
        self.assertEqual(route_state(None), "no_service")

    def test_select_prefers_the_active_run(self):
        later = dict(ROUTE_SCHEDULED, direction="WAY_BACK",
                     startTime="2026-09-21T14:30:00+0000",
                     checkInTime="2026-09-21T14:31:00+0000")
        self.assertIs(select_route([ROUTE_SCHEDULED, later]), later)

    def test_select_falls_back_to_unfinished(self):
        done = dict(ROUTE_SCHEDULED, checkOutTime="2026-09-21T06:07:00+0000")
        self.assertIs(select_route([done, ROUTE_SCHEDULED]), ROUTE_SCHEDULED)

    def test_select_last_when_all_finished(self):
        a = dict(ROUTE_SCHEDULED, checkOutTime="2026-09-21T06:07:00+0000")
        b = dict(ROUTE_SCHEDULED, checkOutTime="2026-09-21T16:07:00+0000")
        self.assertIs(select_route([a, b]), b)


class TestTimes(unittest.TestCase):
    def test_full_iso_with_compact_offset(self):
        parsed = parse_dt("2026-09-21T05:40:00+0000")
        self.assertEqual(parsed, dt.datetime(2026, 9, 21, 5, 40,
                                             tzinfo=dt.timezone.utc))

    def test_times_are_utc_not_local(self):
        """05:40+0000 is a 07:40 Brussels pickup - never show it raw."""
        parsed = parse_dt("2026-09-21T05:40:00+0000")
        brussels = parsed.astimezone(dt.timezone(dt.timedelta(hours=2)))
        self.assertEqual(brussels.hour, 7)
        self.assertEqual(brussels.minute, 40)

    def test_bare_time_needs_a_date(self):
        self.assertIsNone(parse_dt("05:40:00+0000"))
        anchored = parse_dt("05:40:00+0000", dt.date(2026, 9, 21))
        self.assertEqual(anchored.hour, 5)

    def test_garbage_and_empty(self):
        self.assertIsNone(parse_dt(None))
        self.assertIsNone(parse_dt(""))
        self.assertIsNone(parse_dt("not a time"))

    def test_departure_and_arrival(self):
        self.assertEqual(
            route_departure(ROUTE_SCHEDULED),
            dt.datetime(2026, 9, 21, 5, 40, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(
            route_arrival(ROUTE_SCHEDULED),
            dt.datetime(2026, 9, 21, 6, 5, tzinfo=dt.timezone.utc),
        )

    def test_arrival_crossing_midnight_moves_to_next_day(self):
        entry = dict(ROUTE_SCHEDULED, startTime="2026-09-21T23:50:00+0000",
                     arrivalTime="00:15:00+0000")
        self.assertEqual(route_arrival(entry).day, 22)


if __name__ == "__main__":
    unittest.main(verbosity=2)
