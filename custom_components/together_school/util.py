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


def bus_routes(agenda: Any) -> list[dict[str, Any]]:
    """The BUS_ROUTE entries of a combined-agenda response."""
    if isinstance(agenda, dict):
        entries = agenda.get(AGENDA_BUS_KEY)
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)]
    return []


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
    # A run that crosses midnight would otherwise land before its departure.
    if parsed < start:
        import datetime as _d

        parsed += _d.timedelta(days=1)
    return parsed


def route_checkin(entry: Any) -> Any:
    """When the child boarded, as an aware datetime."""
    return _anchored(entry, "checkInTime")


def route_checkout(entry: Any) -> Any:
    """When the child got off, as an aware datetime."""
    return _anchored(entry, "checkOutTime")


def route_missed(entry: Any) -> Any:
    """When the school recorded a missed pickup, as an aware datetime."""
    return _anchored(entry, "missedTime")


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
