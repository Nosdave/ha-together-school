"""Pure parsing helpers - no Home Assistant or third-party imports.

Kept dependency-free on purpose so they can be unit-tested standalone against
recorded API responses (see tests/test_parsing.py).
"""

from __future__ import annotations

from typing import Any

ENVELOPE_STATUS = "status"
ENVELOPE_DATA = "data"
ENVELOPE_OK = "SUCCESS"


def unwrap_envelope(body: Any) -> Any:
    """Strip the {"status": "SUCCESS", "data": ...} envelope the API uses.

    Error envelopes ({"status": "ERROR", "message": ...}) are returned as-is so
    the caller can surface the message.
    """
    if (
        isinstance(body, dict)
        and body.get(ENVELOPE_STATUS) == ENVELOPE_OK
        and ENVELOPE_DATA in body
    ):
        return body[ENVELOPE_DATA]
    return body


def parse_tenant_id(js_text: Any) -> str | None:
    """Read TENANT_ID out of a deployment's ``environment.values.js``.

    Every school serves its own web front-end config publicly, e.g.::

        var APP_CONFIG = {
          API: '/api/v1',
          TENANT_ID: "<tenant>",
          ...
        };

    The tenant differs per school, so it is discovered rather than guessed.
    """
    import re

    if not isinstance(js_text, str):
        return None
    match = re.search(
        r"""TENANT_ID\s*:\s*['"]([^'"]+)['"]""", js_text
    )
    return match.group(1) if match else None


def pick_id(obj: Any) -> str | None:
    """Pull an identifier out of a record.

    ``/auth/users/me`` uses ``userId``; pupil records use ``id``.
    """
    if isinstance(obj, dict):
        for key in ("userId", "id", "parentId", "uuid"):
            if obj.get(key):
                return str(obj[key])
    return None


def extract_pupils(payload: Any) -> dict[str, dict[str, Any]]:
    """Map pupil_id -> pupil record from ``/parents/{id}/pupils``.

    That endpoint returns a plain JSON array; a wrapped object is tolerated too.
    """
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("pupils") or []
    pupils: dict[str, dict[str, Any]] = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and (pid := pick_id(item)):
                pupils[pid] = item
    return pupils


def pupil_display_name(info: Any, pupil_id: str) -> str:
    """Friendly name for a pupil; the API returns names in SHOUTING CAPS."""
    if isinstance(info, dict):
        for key in ("forename", "fullName", "displayName", "name"):
            if value := info.get(key):
                return str(value).title()
    return f"Pupil {pupil_id[:8]}"


def extract_latlon(node: Any) -> tuple[float, float] | None:
    """Best-effort (lat, lon) from the API's location shapes.

    Seen/expected shapes:
      * {"lat": .., "lng": ..} / {"latitude": .., "longitude": ..}
      * {"location": {...}} nested one level
      * GeoJSON-ish {"geometry": {"coordinates": [lng, lat]}}
    """
    if isinstance(node, (list, tuple)):
        # Some payloads wrap the position in a single-element list.
        for item in node:
            if found := extract_latlon(item):
                return found
        return None
    if not isinstance(node, dict):
        return None
    lat = node.get("lat", node.get("latitude"))
    lon = node.get("lng", node.get("lon", node.get("longitude")))
    if (pair := _as_latlon(lat, lon)) is not None:
        return pair
    nested = node.get("location") or node.get("position") or node.get("coords")
    if nested is not None and nested is not node:
        if found := extract_latlon(nested):
            return found
    geom = node.get("geometry")
    if isinstance(geom, dict):
        coords = geom.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            if (pair := _coords_to_latlon(coords[0], coords[1])) is not None:
                return pair
    return None


def _coords_to_latlon(first: Any, second: Any) -> tuple[float, float] | None:
    """Interpret a GeoJSON-style coordinate pair.

    The backend wraps positions in ``"type": "Feature"`` objects but emits
    ``[lat, lng]``, while GeoJSON specifies ``[lng, lat]``. Verified against a
    known school address, whose coordinates come back as [50.79…, 4.37…] in
    Brussels. Reading it the spec way would silently place the bus thousands of
    kilometres away with both values still "valid", so this prefers the
    observed order and only falls back to the spec when the first value cannot
    possibly be a latitude.
    """
    try:
        a, b = float(first), float(second)
    except (TypeError, ValueError):
        return None
    if abs(a) > 90.0 >= abs(b):
        return _as_latlon(b, a)  # unambiguously [lng, lat]
    return _as_latlon(a, b)


def _as_latlon(lat: Any, lon: Any) -> tuple[float, float] | None:
    """Coerce a candidate pair, rejecting anything not a plausible position."""
    if lat is None or lon is None or isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if lat_f != lat_f or lon_f != lon_f:  # NaN
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    if lat_f == 0.0 and lon_f == 0.0:
        # Null Island: the backend's "no fix yet" value, not a real position.
        return None
    return lat_f, lon_f



# --- BUS_ROUTE entries -----------------------------------------------------
#
# A combined-agenda response looks like::
#
#     {"BUS_ROUTE": [{"direction": "WAY_TO",
#                     "schedule": "05:40:00+0000",      # UTC time-of-day
#                     "arrivalTime": "06:05:00+0000",
#                     "startTime": "2026-09-21T05:40:00+0000",
#                     "checkInTime": None, "checkOutTime": None,
#                     "missedTime": None, "online": False,
#                     "busNumber": "99XX1", "name": "99XX1",
#                     "boardingStation": "...", "arrivalStation": "...",
#                     "busId": None, "activeRouteId": None,
#                     "studentState": None, "routeState": None}]}
#
# Check-in/out are TIMESTAMPS, not booleans, and every time is UTC.

import datetime as _dt_mod

_EPOCH = _dt_mod.datetime.min.replace(tzinfo=_dt_mod.timezone.utc)

AGENDA_BUS_KEY = "BUS_ROUTE"

STATE_NO_SERVICE = "no_service"
STATE_SCHEDULED = "scheduled"
STATE_ON_ROUTE = "on_route"
STATE_ON_BOARD = "on_board"
STATE_COMPLETED = "completed"
STATE_MISSED = "missed"


def parse_dt(value: Any, on_date: Any = None) -> Any:
    """Parse the API's timestamps into aware datetimes.

    Handles both full ISO values (``2026-09-21T05:40:00+0000``) and bare
    times (``05:40:00+0000``), which need a date to be meaningful.
    """
    import datetime as _dt

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # "+0000" -> "+00:00" so fromisoformat accepts it on every version.
    if len(text) >= 5 and (text[-5] in "+-") and text[-5:].isascii() \
            and text[-4:].isdigit():
        text = f"{text[:-5]}{text[-5]}{text[-4:-2]}:{text[-2:]}"
    try:
        if "T" in text:
            parsed = _dt.datetime.fromisoformat(text)
            # A timestamp without an offset would be rejected by HA's
            # TIMESTAMP sensors; the backend speaks UTC, so assume that.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed
        parsed_time = _dt.time.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if on_date is None:
        return None
    combined = _dt.datetime.combine(on_date, parsed_time)
    if combined.tzinfo is None:
        combined = combined.replace(tzinfo=_dt.timezone.utc)
    return combined


def _progress(entry: Any) -> int:
    """How far along a run is - used to pick between duplicate records."""
    if not isinstance(entry, dict):
        return -1
    if entry.get("checkOutTime") or entry.get("missedTime"):
        return 4
    if entry.get("checkInTime"):
        return 3
    if entry.get("online") or entry.get("activeRouteId"):
        return 2
    if entry.get("routeState"):
        return 1
    return 0


def bus_routes(agenda: Any) -> list[dict[str, Any]]:
    """The BUS_ROUTE entries of a combined-agenda response.

    The backend sometimes returns the same run twice: once filled in and once
    as an empty twin (same direction and start, but no ids and no timestamps).
    Left in, the empty twin reads as "scheduled" and would outrank the finished
    record once the run is over. Only the more advanced of the pair is kept.
    """
    if not isinstance(agenda, dict):
        return []
    entries = agenda.get(AGENDA_BUS_KEY)
    if not isinstance(entries, list):
        return []
    best: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("direction"), entry.get("startTime"),
               entry.get("busNumber"))
        if key not in best:
            best[key] = entry
            order.append(key)
        elif _progress(entry) > _progress(best[key]):
            best[key] = entry
    return [best[k] for k in order]


# Values the live API reports once a run is active.
STUDENT_ON_BOARD = "IS_ON_BOARD"


def route_state(entry: Any) -> str:
    """Classify one BUS_ROUTE entry into a single status."""
    if not isinstance(entry, dict):
        return STATE_NO_SERVICE
    if entry.get("missedTime"):
        return STATE_MISSED
    if entry.get("checkOutTime"):
        return STATE_COMPLETED
    if entry.get("checkInTime"):
        return STATE_ON_BOARD
    # The live run also carries an explicit flag; trust it if the timestamps
    # have not caught up yet.
    if entry.get("studentState") == STUDENT_ON_BOARD:
        return STATE_ON_BOARD
    if entry.get("online"):
        return STATE_ON_ROUTE
    return STATE_SCHEDULED


PUNCTUALITY_UNKNOWN = "unknown"


def punctuality(entry: Any) -> str:
    """The backend's own verdict on the run, normalised to a few values.

    Seen so far: ON_TIME while driving, COMPLETED afterwards. Anything else is
    passed through lower-cased so a new value shows up rather than hiding.
    """
    if not isinstance(entry, dict):
        return PUNCTUALITY_UNKNOWN
    raw = entry.get("routeState")
    if not raw:
        return PUNCTUALITY_UNKNOWN
    return str(raw).strip().lower()


def bus_fix(delivery: Any) -> dict[str, Any]:
    """Metadata about the bus position fix, if the payload carries one.

    ``busLocation`` is a LIST of per-bus entries, each wrapping a GeoJSON-ish
    Feature plus ``lastLocatedTime`` and ``actual``.
    """
    if not isinstance(delivery, dict):
        return {}
    entries = delivery.get("busLocation")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return {}
    for item in entries:
        if isinstance(item, dict):
            return {
                "bus_id": item.get("busId"),
                "last_located": item.get("lastLocatedTime"),
                "position_is_current": item.get("actual"),
            }
    return {}


def is_on_bus(entry: Any) -> bool:
    """True while the child is checked in and not yet checked out."""
    return route_state(entry) == STATE_ON_BOARD


def select_route(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the entry that matters right now.

    Ordered by scheduled departure rather than trusting the API's list order:
    a run in progress wins, else the earliest run still ahead of us, else the
    last run of the day. A MISSED morning run must not mask a later one, so it
    is not treated as "still to come".
    """
    if not entries:
        return None

    def sort_key(entry):
        departure = route_departure(entry)
        # Entries without a parsable departure sort last but keep their order.
        return (departure is None, departure or _EPOCH)

    ordered = sorted(entries, key=sort_key)

    for entry in ordered:
        if route_state(entry) in (STATE_ON_BOARD, STATE_ON_ROUTE):
            return entry
    for entry in ordered:
        if route_state(entry) == STATE_SCHEDULED:
            return entry
    return ordered[-1]


def _anchored(entry: Any, key: str) -> Any:
    """Parse a per-run timestamp, anchoring bare times to the run's date.

    The backend mixes formats: ``startTime`` is a full ISO datetime, while
    ``checkInTime``/``checkOutTime``/``missedTime`` are time-of-day only
    (e.g. "05:50:46+0000"). Surfacing those raw shows a UTC clock time that
    looks two hours wrong to the reader.
    """
    if not isinstance(entry, dict):
        return None
    raw = entry.get(key)
    if not raw:
        return None
    start = parse_dt(entry.get("startTime"))
    parsed = parse_dt(raw, start.date() if start else None)
    if parsed is None or start is None:
        return parsed
    return _roll_past_midnight(parsed, start)


def route_checkin(entry: Any) -> Any:
    """When the child boarded, as an aware datetime."""
    return _anchored(entry, "checkInTime")


def route_checkout(entry: Any) -> Any:
    """When the child got off, as an aware datetime."""
    return _anchored(entry, "checkOutTime")


def route_missed(entry: Any) -> Any:
    """When the school recorded a missed pickup, as an aware datetime."""
    return _anchored(entry, "missedTime")


# Boarding legitimately happens before the scheduled departure - children are
# let on well ahead of time - so "earlier than departure" alone must NOT be
# read as "the run crossed midnight". Only a gap this large means a wrap.
_MIDNIGHT_WRAP = 12 * 3600


def _roll_past_midnight(moment: Any, start: Any) -> Any:
    """Move a bare time onto the next day only if it really wrapped."""
    import datetime as _d

    if (start - moment).total_seconds() > _MIDNIGHT_WRAP:
        return moment + _d.timedelta(days=1)
    return moment


def route_departure(entry: Any) -> Any:
    """Scheduled departure from the boarding station, as an aware datetime."""
    if not isinstance(entry, dict):
        return None
    return parse_dt(entry.get("startTime"))


def route_arrival(entry: Any) -> Any:
    """Scheduled arrival, as an aware datetime.

    ``arrivalTime`` is only a time-of-day, so it is anchored to the date of the
    run taken from ``startTime``.
    """
    if not isinstance(entry, dict):
        return None
    start = parse_dt(entry.get("startTime"))
    if start is None:
        return None
    arrival = parse_dt(entry.get("arrivalTime"), start.date())
    if arrival is None:
        return None
    # A run that crosses midnight would land before its own departure.
    if arrival < start:
        import datetime as _d

        arrival += _d.timedelta(days=1)
    return arrival


# --- Arrival forecast for the child's own stop ------------------------------
#
# The timetable alone is not what happens, and the check-in is no substitute:
# it is scanned by a supervisor and lags the bus by minutes. The reliable
# signal is the bus position, so the offset between the timetable and the
# moment the bus actually reaches the stop is measured per installation and
# kept as a rolling median.

STOP_RADIUS_M = 60          # "the bus is at the stop"
MIN_SPEED_KMH = 3.0         # below this, treat the bus as standing
MAX_SPEED_KMH = 90.0        # above this, assume a bad fix
ROAD_FACTOR = 1.35          # straight line -> road distance, rough but stable
LEARNED_KEEP = 12           # how many past runs feed the median

SOURCE_LIVE = "live"
SOURCE_LEARNED = "timetable+learned"
SOURCE_TIMETABLE = "timetable"


def distance_m(a: Any, b: Any) -> float | None:
    """Metres between two (lat, lon) pairs, flat-earth - fine at city scale."""
    import math

    if not a or not b:
        return None
    dlat = (a[0] - b[0]) * 111320.0
    dlon = (a[1] - b[1]) * 111320.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def median(values: Any) -> float | None:
    """Median of a sequence; None when empty.

    Deliberately not the mean: one freak run (roadworks, a breakdown) should
    not move the everyday expectation.
    """
    items = sorted(v for v in (values or []) if isinstance(v, (int, float)))
    if not items:
        return None
    mid = len(items) // 2
    if len(items) % 2:
        return float(items[mid])
    return (items[mid - 1] + items[mid]) / 2.0


def speed_kmh(fixes: Any) -> float | None:
    """Recent ground speed from consecutive position fixes.

    ``fixes`` is a sequence of ``(timestamp, lat, lon)`` oldest-first. Returns
    None when the bus is standing or the fixes are implausible, so the caller
    can fall back rather than extrapolate nonsense.
    """
    if not fixes or len(fixes) < 2:
        return None
    total_m = 0.0
    total_s = 0.0
    for (t0, la0, lo0), (t1, la1, lo1) in zip(fixes, fixes[1:]):
        seconds = (t1 - t0).total_seconds()
        if seconds <= 0:
            continue
        step = distance_m((la0, lo0), (la1, lo1))
        if step is None:
            continue
        total_m += step
        total_s += seconds
    if total_s <= 0:
        return None
    kmh = (total_m / total_s) * 3.6
    if kmh < MIN_SPEED_KMH or kmh > MAX_SPEED_KMH:
        return None
    return kmh


def eta_from_position(fixes: Any, stop: Any, now: Any) -> Any:
    """When the bus should reach the stop, from its own movement.

    Straight-line distance scaled by a road factor, divided by the recent
    speed. Crude next to a routing service, but it needs no extra integration
    and degrades honestly: if the bus is standing, it returns None instead of
    promising an arrival.
    """
    import datetime as _dt

    if not fixes or not stop:
        return None
    last = fixes[-1]
    remaining = distance_m((last[1], last[2]), stop)
    if remaining is None:
        return None
    if remaining <= STOP_RADIUS_M:
        return now
    kmh = speed_kmh(fixes)
    if not kmh:
        return None
    seconds = (remaining * ROAD_FACTOR) / (kmh / 3.6)
    return now + _dt.timedelta(seconds=seconds)
