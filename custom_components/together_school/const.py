"""Constants for the Together School integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "together_school"

# --- Backend model (reverse-engineered from the official Android app v1.4.5) ---
# Every school is a separate tenant on its own subdomain following the pattern
#   https://<school_code>.together-school.com
# Each deployment also has its own tenant id, which is NOT shared between
# schools; it is discovered at setup time from the deployment's public web
# config rather than hard-coded here.
BASE_URL_TEMPLATE = "https://{school_code}.together-school.com"

# HTTP headers required on (almost) every request.
HEADER_AUTHORIZATION = "Authorization"  # raw access token, NO "Bearer " prefix
HEADER_TENANT = "X-TenantID"
HEADER_LOCALE = "Locale"

# Parent accounts are rejected on clientType WEB ("You are not authorized to log
# in to the web application. Please use our mobile app instead."), so we sign in
# as the mobile client. MOBILE requires a non-empty deviceToken; we generate one
# per config entry and reuse it so HA stays a single, stable "device".
CLIENT_TYPE = "MOBILE"

# NB: all API responses are wrapped as {"status": "SUCCESS", "data": <payload>};
# unwrapping lives in util.unwrap_envelope.

# --- Config entry keys ---
CONF_SCHOOL_CODE = "school_code"
CONF_TENANT_ID = "tenant_id"
CONF_LOCALE = "locale"
CONF_ACCESS_TOKEN = "access_token"
CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_DEVICE_TOKEN = "device_token"
CONF_ACTIVE_HOURS_ONLY = "active_hours_only"

# No school or tenant default on purpose: the integration must not ship
# pointing at any particular school. The school code is supplied by the user
# and the tenant id is discovered from that deployment at setup time.
DEFAULT_LOCALE = "en"

# --- Polling ---
# Bus tracking only matters around commute windows. The coordinator polls at
# SCAN_INTERVAL, but when active_hours_only is set it stays idle outside them.
#
# While a bus is actually driving we poll as fast as the official app does
# (it refreshes every ~15 s); the rest of the time a slower beat is plenty and
# keeps the load off the school's backend.
SCAN_INTERVAL_LIVE = timedelta(seconds=15)
SCAN_INTERVAL = timedelta(seconds=60)

# Remembered coordinates of the fixed places, so the map still shows the stop
# and the school when no run is active (the API only sends them during a run).
CONF_STATION_LATLON = "station_latlon"
CONF_SCHOOL_LATLON = "school_latlon"

# Starting suggestion only - real timetables differ per school, so these are
# editable in the options flow and stored per config entry.
DEFAULT_ACTIVE_WINDOWS = [("06:45", "08:45"), ("14:45", "17:15")]

CONF_ACTIVE_WINDOWS = "active_windows"

# How far ahead the "arriving soon" trigger should fire. Kept as an option
# because it is a property of the household, not of the bus.
CONF_LEAD_MINUTES = "lead_minutes"
DEFAULT_LEAD_MINUTES = 5

# Optional: an external travel-time sensor (Waze, Google, HERE ...) measuring
# bus -> stop. Without it the forecast falls back to the bus's own movement,
# so the integration never depends on another one being installed.
CONF_TRAVEL_SENSOR = "travel_time_sensor"

# Learned offsets between the timetable and the moment the bus really reaches
# the stop, per direction. Measured from positions, never from the check-in:
# that is scanned by a person and lags the bus by minutes.
CONF_STOP_OFFSETS = "stop_offsets"

# How long the bus takes from pulling away at the start of its line to
# reaching this stop, per direction. Anchored on an observed event rather
# than on the timetable, which makes it both more accurate and - because it
# errs early - safer for "do not miss the bus": measured over six mornings it
# was at worst 18 seconds optimistic, against 92 for the speed estimate.
CONF_TRAVEL_MINUTES = "travel_minutes"

# The places the line actually stops at, counted across runs. Learned
# here rather than read from the recorder: a return run happens twice a
# week and the recorder keeps about ten days, which is never enough
# samples at once to tell a stop from a traffic light.
CONF_ROUTE_STOPS = "route_stops"

# Runs whose stop arrival has already been recorded, so a restart cannot
# replay the "arriving soon" trigger.
CONF_ARRIVED_RUNS = "arrived_runs"


def normalise_school_code(value) -> str:
    """Reduce whatever the user pasted to a bare subdomain label.

    Accepts "MYCODE ", "https://mycode.together-school.com/", "mycode.together-school.com".
    Returns "" if nothing usable is left, so the caller can show an error.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split("/", 1)[0]
    if text.endswith(".together-school.com"):
        text = text[: -len(".together-school.com")]
    text = text.strip(". ")
    # A subdomain label: letters, digits and hyphens only.
    if not text or not all(c.isalnum() or c == "-" for c in text):
        return ""
    return text


def windows_to_text(windows) -> str:
    """Render commute windows as the "HH:MM-HH:MM, HH:MM-HH:MM" option value."""
    return ", ".join(f"{start}-{end}" for start, end in windows)


def parse_windows(text):
    """Parse "HH:MM-HH:MM, ..." into window tuples.

    Returns None if anything is malformed, so the caller can report an error
    rather than silently polling at the wrong time.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    windows = []
    for chunk in text.split(","):
        part = chunk.strip()
        if not part:
            continue
        if part.count("-") != 1:
            return None
        start, end = (p.strip() for p in part.split("-"))
        for value in (start, end):
            if len(value) != 5 or value[2] != ":":
                return None
            hh, mm = value[:2], value[3:]
            if not (hh.isdigit() and mm.isdigit()):
                return None
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                return None
        if start > end:
            return None
        windows.append((start, end))
    return windows or None

# --- API endpoint paths (relative to base URL) ---
# Public web front-end config of a deployment; carries its TENANT_ID.
EP_ENVIRONMENT = "/environment.values.js"

EP_SIGNIN = "/api/v1/auth/signin"
EP_SIGNIN_CONFIRM = "/api/v1/auth/signin/confirm"
EP_SIGNOUT = "/api/v1/auth/signout"
EP_USER_VERIFY = "/api/v1/auth/users/verify"
EP_ME = "/api/v1/auth/users/me"
EP_PARENT_PUPILS = "/api/v1/parents/{parent_id}/pupils"
EP_PUPIL = "/api/v1/pupils/{pupil_id}"
EP_PUPIL_COMBINED_AGENDA = "/api/v1/pupils/{pupil_id}/combined/agenda/{date}"
EP_DELIVERY_WITH_BUS = (
    "/api/v1/transportation/pupils/{pupil_id}/deliveryWithBus/location"
)
EP_BUS_LOCATION = "/api/v1/transportation/buses/{bus_id}/location"
