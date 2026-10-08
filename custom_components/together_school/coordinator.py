"""DataUpdateCoordinator for Together School."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TogetherSchoolApi, TogetherSchoolAuthError, TogetherSchoolError
from .const import (
    DEFAULT_ACTIVE_WINDOWS,
    DOMAIN,
    SCAN_INTERVAL,
    SCAN_INTERVAL_LIVE,
)
from .util import (
    LEARNED_KEEP,
    MOVED_AWAY_M,
    SOURCE_LIVE,
    SOURCE_TIMETABLE,
    STATE_ON_BOARD,
    STATE_ON_ROUTE,
    STOP_RADIUS_M,
    bus_fix,
    bus_routes,
    distance_m,
    earliest,
    eta_from_position,
    extract_latlon,
    extract_pupils,
    median,
    parse_dt,
    pick_id,
    route_arrival,
    route_departure,
    route_state,
    select_route,
    stop_was_served,
)

_LOGGER = logging.getLogger(__name__)


def _in_active_window(now: dt.datetime, windows: list[tuple[str, str]]) -> bool:
    """True if the local time falls inside one of the commute windows."""
    hm = now.strftime("%H:%M")
    return any(start <= hm <= end for start, end in windows)


class TogetherSchoolCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls live bus + check-in/out data per pupil.

    ``data`` shape::

        {
          "pupils": {<pupil_id>: {"info": {...}, "delivery": {...},
                                   "agenda": {...}}},
          "parent_id": <str>,
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: TogetherSchoolApi,
        *,
        active_hours_only: bool,
        active_windows: list[tuple[str, str]] | None = None,
        station: tuple[float, float] | None = None,
        school: tuple[float, float] | None = None,
        on_places_learned: Any = None,
        lead_minutes: int = 5,
        travel_sensor: str | None = None,
        stop_offsets: dict[str, list[float]] | None = None,
        travel_minutes: dict[str, list[float]] | None = None,
        arrived_runs: dict[str, str] | None = None,
        on_offsets_learned: Any = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self._active_hours_only = active_hours_only
        self._active_windows = active_windows or DEFAULT_ACTIVE_WINDOWS
        self.parent_id: str | None = None
        self.pupil_ids: list[str] = []
        # pupil_id -> the pupil record from /parents/{id}/pupils (name, address…)
        self.pupils: dict[str, dict[str, Any]] = {}
        # The API only sends these while a run is active, so hold on to them -
        # and persist them, or every restart would blank the map until the
        # next run.
        self.station: tuple[float, float] | None = station
        self.school: tuple[float, float] | None = school
        self._on_places_learned = on_places_learned
        # Filled in by async_setup_entry; see _async_entry_updated.
        self.options_snapshot: dict[str, Any] | None = None
        # Recent position fixes per pupil, for speed and live arrival.
        self._trail: dict[str, list[tuple[Any, float, float]]] = {}
        # Recent live forecasts per pupil, for smoothing.
        self._eta_history: dict[str, list[tuple[str, Any, Any]]] = {}
        # Last usable live forecast, to ride out brief gaps.
        self._last_live: dict[str, tuple[str, Any, Any]] = {}
        # Which run we have already recorded a stop arrival for. Persisted:
        # a restart in the gap between the bus passing and the supervisor's
        # scan would otherwise resume the forecast and could fire the
        # "arriving soon" trigger a second time - calling a lift twice.
        self._arrived: dict[str, str] = dict(arrived_runs or {})
        # Closest the bus has come to the stop on the current run, so an
        # arrival is still recognised when the position feed goes quiet across
        # it. Per run, in memory only: it is worthless once the run is over.
        self._closest: dict[str, tuple[str, float]] = {}
        # Learned timetable->reality offsets in minutes, per direction.
        self.stop_offsets: dict[str, list[float]] = dict(stop_offsets or {})
        # Learned minutes from the bus pulling away at the start of its line
        # to reaching this stop, per direction. The better of the two: the
        # timetable is a promise, this is a measurement anchored on something
        # that actually happened.
        self.travel_minutes: dict[str, list[float]] = dict(travel_minutes or {})
        # Per run: where the bus first appeared, and when it left that spot.
        # In memory only - meaningless once the run is over.
        self._run_start: dict[str, tuple[str, tuple[float, float], Any]] = {}
        self._departed: dict[str, tuple[str, Any]] = {}
        self._on_offsets_learned = on_offsets_learned
        self.travel_sensor = travel_sensor
        self.lead_minutes = lead_minutes
        # Last known bus position per pupil, so the map keeps showing where the
        # bus was instead of vanishing the moment a run ends.
        self.last_seen: dict[str, dict[str, Any]] = {}

    async def async_setup(self) -> None:
        """One-time discovery of parent id and pupils.

        Deliberately does NOT sign in: the runtime client holds only the stored
        token (the password is never persisted). A rejected token surfaces as
        TogetherSchoolAuthError and becomes Home Assistant's re-auth flow.
        """
        me = await self.api.async_get_me()
        self.parent_id = pick_id(me)
        if not self.parent_id:
            raise UpdateFailed(f"Could not determine parent id from {me}")
        # /parents/{id}/pupils returns a plain JSON array of pupil objects.
        self.pupils = extract_pupils(await self.api.async_get_pupils(self.parent_id))
        self.pupil_ids = list(self.pupils)
        if not self.pupil_ids:
            _LOGGER.warning("No pupils found for parent %s", self.parent_id)

    async def _async_update_data(self) -> dict[str, Any]:
        # HA's configured timezone, not the process one: a container running
        # in UTC would otherwise shift every window.
        now = dt_util.now()
        # Always fetch once, even outside the windows: after a restart at noon
        # we would otherwise show nothing until the next commute window, hiding
        # today's timetable and the completed morning run.
        if (
            self._active_hours_only
            and self.data
            and not _in_active_window(now, self._active_windows)
        ):
            # Keep the last known data; skip the network round-trip.
            return self.data

        result: dict[str, Any] = {"pupils": {}, "parent_id": self.parent_id}
        today = now.date().isoformat()
        try:
            for pid in self.pupil_ids:
                entry: dict[str, Any] = {}
                # NB: TogetherSchoolAuthError is a subclass of
                # TogetherSchoolError, so it must be re-raised here - otherwise
                # an expired token is swallowed per pupil and re-auth never
                # fires, leaving the entities silently stale forever.
                try:
                    entry["delivery"] = await self.api.async_get_delivery_with_bus(pid)
                except TogetherSchoolAuthError:
                    raise
                except TogetherSchoolError as err:
                    _LOGGER.debug("delivery for %s failed: %s", pid, err)
                    entry["delivery"] = None
                try:
                    entry["agenda"] = await self.api.async_get_combined_agenda(
                        pid, today
                    )
                except TogetherSchoolAuthError:
                    raise
                except TogetherSchoolError as err:
                    _LOGGER.debug("agenda for %s failed: %s", pid, err)
                    entry["agenda"] = None
                self._remember(pid, entry)
                entry["last_seen"] = self.last_seen.get(pid)
                self._observe_stop_arrival(pid, entry, now)
                entry["forecast"] = self._forecast(pid, entry, now)
                result["pupils"][pid] = entry
        except TogetherSchoolAuthError as err:
            # Token no longer valid and we hold no password -> ask the user.
            raise ConfigEntryAuthFailed(str(err)) from err
        except TogetherSchoolError as err:
            raise UpdateFailed(str(err)) from err

        self._retune(result)
        return result

    def _remember(self, pupil_id: str, entry: dict[str, Any]) -> None:
        """Keep the fixed places and the last bus fix across quiet periods.

        The backend blanks every location once a run ends. Dropping them would
        make the map disappear exactly when you want to look back at where the
        bus waited, so the last fix is kept and flagged as no longer current.
        """
        delivery = entry.get("delivery")
        if not isinstance(delivery, dict):
            return
        learned = False
        if (station := extract_latlon(delivery.get("stationLocation"))) is not None:
            if station != self.station:
                self.station, learned = station, True
        if (school := extract_latlon(delivery.get("schoolLocation"))) is not None:
            if school != self.school:
                self.school, learned = school, True
        if learned and self._on_places_learned:
            self._on_places_learned(self.station, self.school)
        if (bus := extract_latlon(delivery.get("busLocation"))) is not None:
            fix = {"lat": bus[0], "lon": bus[1]}
            entries = delivery.get("busLocation")
            first = entries[0] if isinstance(entries, list) and entries else {}
            if isinstance(first, dict):
                fix["bus_id"] = first.get("busId")
                fix["at"] = first.get("lastLocatedTime")
            self.last_seen[pupil_id] = fix

    def _retune(self, result: dict[str, Any]) -> None:
        """Match the poll rate to what is happening.

        While a bus is driving we keep pace with the official app (~15 s);
        otherwise a slower beat is plenty.
        """
        live = False
        for entry in result.get("pupils", {}).values():
            route = select_route(bus_routes(entry.get("agenda")))
            if route_state(route) in (STATE_ON_BOARD, STATE_ON_ROUTE):
                live = True
                break
        wanted = SCAN_INTERVAL_LIVE if live else SCAN_INTERVAL
        if self.update_interval != wanted:
            _LOGGER.debug("Poll interval -> %s", wanted)
            self.update_interval = wanted


    # -- arrival forecast -------------------------------------------------

    def _bus_position(self, entry: dict[str, Any]) -> tuple[float, float] | None:
        delivery = entry.get("delivery")
        if isinstance(delivery, dict):
            return extract_latlon(delivery.get("busLocation"))
        return None

    def _observe_stop_arrival(
        self, pupil_id: str, entry: dict[str, Any], now: dt.datetime
    ) -> None:
        """Learn how late the bus really is at this stop.

        Measured from the position, never from the check-in: that is scanned by
        a supervisor and trails the bus by minutes, which would push every
        forecast late. One measurement per run, keyed by the run itself so a
        restart cannot double-count it.
        """
        position = self._bus_position(entry)
        if position is None:
            return
        # Use the fix's OWN timestamp and skip repeats: the backend refreshes
        # roughly every 30 s while we poll every 15, so half of the samples
        # would be the same position again. Counted as movement-in-15-seconds
        # they halve the apparent speed, and the next real fix doubles it back
        # - which swung the forecast by ten minutes between two polls.
        fix_time = parse_dt((bus_fix(entry.get("delivery")) or {}).get("last_located")) or now
        trail = self._trail.setdefault(pupil_id, [])
        if trail and trail[-1][0] == fix_time:
            return
        trail.append((fix_time, position[0], position[1]))
        del trail[:-8]

        if self.station is None:
            return
        route = select_route(bus_routes(entry.get("agenda")))
        if not route:
            return
        run_id = str(route.get("activeRouteId") or route.get("startTime") or "")
        if not run_id or self._arrived.get(pupil_id) == run_id:
            return

        # Where this run started, and the moment the bus left that spot.
        # The start of the line is the same place every day (11 m of scatter
        # over seven mornings), and how long the bus waits there is not: 3.9
        # to 9.3 minutes. So the wait says nothing, and pulling away says
        # almost everything - from there the run takes a consistent time.
        begun = self._run_start.get(pupil_id)
        if begun is None or begun[0] != run_id:
            self._run_start[pupil_id] = (run_id, position, fix_time)
            self._departed.pop(pupil_id, None)
        else:
            gone = self._departed.get(pupil_id)
            if (gone is None or gone[0] != run_id) and \
                    (distance_m(position, begun[1]) or 0.0) > MOVED_AWAY_M:
                self._departed[pupil_id] = (run_id, fix_time)
                _LOGGER.debug("Bus left the start of its line at %s", fix_time)

        here = distance_m(position, self.station)
        seen = self._closest.get(pupil_id)
        if seen is None or seen[0] != run_id:
            self._closest[pupil_id] = (run_id, here if here is not None else 1e9)
        elif here is not None and here < seen[1]:
            self._closest[pupil_id] = (run_id, here)
        closest = self._closest[pupil_id][1]

        if (here or 1e9) > STOP_RADIUS_M:
            # No fix landed at the stop - but the feed drops out for minutes at
            # a time, sometimes exactly while the bus is there. Coming close
            # and then drawing away again is proof enough that it has been.
            if stop_was_served(closest, here):
                _LOGGER.debug(
                    "Stop served during a gap in the feed (closest %.0f m, now %.0f m)",
                    closest, here,
                )
                self._arrived[pupil_id] = run_id
                if self._on_offsets_learned:
                    # End the forecast, but learn nothing: the moment it
                    # actually arrived is inside the gap and unknown.
                    self._on_offsets_learned(self.stop_offsets, self._arrived, self.travel_minutes)
            return

        # The timetable moment this stop was due: the departure on the way out,
        # the arrival on the way home.
        due = (route_arrival(route) if route.get("direction") == "WAY_BACK"
               else route_departure(route))
        self._arrived[pupil_id] = run_id
        if due is None:
            if self._on_offsets_learned:
                self._on_offsets_learned(self.stop_offsets, self._arrived, self.travel_minutes)
            return
        gone = self._departed.get(pupil_id)
        if gone and gone[0] == run_id:
            minutes = (now - gone[1]).total_seconds() / 60.0
            if 0 < minutes <= 60:
                series = self.travel_minutes.setdefault(
                    route.get("direction") or "?", [])
                series.append(round(minutes, 1))
                del series[:-LEARNED_KEEP]
                _LOGGER.debug("Learned travel from the line start: %.1f min", minutes)

        offset = (now - due).total_seconds() / 60.0
        if abs(offset) > 45:
            # Implausible: a stale fix or a mismatched run, not a real delay.
            return
        direction = route.get("direction") or "?"
        values = self.stop_offsets.setdefault(direction, [])
        values.append(round(offset, 1))
        del values[:-LEARNED_KEEP]
        _LOGGER.debug("Learned stop offset %s %+.1f min", direction, offset)
        if self._on_offsets_learned:
            self._on_offsets_learned(self.stop_offsets, self._arrived, self.travel_minutes)

    def _smooth(self, pupil_id: str, run_id: str, eta: dt.datetime,
                now: dt.datetime) -> dt.datetime:
        """Median of the recent live forecasts.

        A single slow stretch - a red light, a turn - drags the measured speed
        down and balloons the raw estimate. Taking the median of the last few
        keeps a genuine change tracking while a one-off spike cannot move the
        number a lift is waiting on.
        """
        history = self._eta_history.setdefault(pupil_id, [])
        if history and history[0][0] != run_id:
            history.clear()
        history.append((run_id, now, eta))
        # Drop anything older than a few minutes: stale guesses should not
        # hold back a forecast that has genuinely moved.
        cutoff = now - dt.timedelta(minutes=3)
        history[:] = [h for h in history if h[1] >= cutoff][-5:]
        stamps = [h[2].timestamp() for h in history]
        return dt.datetime.fromtimestamp(median(stamps), tz=eta.tzinfo)

    def _travel_sensor_minutes(self) -> float | None:
        """Minutes to the stop from the user's own travel-time sensor, if any."""
        if not self.travel_sensor:
            return None
        state = self.hass.states.get(self.travel_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _forecast(
        self, pupil_id: str, entry: dict[str, Any], now: dt.datetime
    ) -> dict[str, Any]:
        """When the bus should reach this child's own stop.

        Three sources, in descending order of trust: a routing sensor the user
        configured, the bus's own movement, and the timetable plus what we have
        learned. Which one was used is reported, so an automation can insist on
        a live figure before doing something irreversible.
        """
        route = select_route(bus_routes(entry.get("agenda")))
        blank = {"eta": None, "source": None, "delay": None,
                 "direction": None, "run_id": None, "done": True}
        if not route:
            return blank

        direction = route.get("direction")
        state = route_state(route)
        run_id = str(route.get("activeRouteId") or route.get("startTime") or "")
        # Once the bus has been AT the stop, the question "when does it get
        # here" is answered - and that is known from the position, not from the
        # check-in. The check-in is scanned by a supervisor and lags by
        # minutes; waiting for it leaves the forecast running while the bus
        # drives away, so the remaining time starts growing again.
        done = (
            self._arrived.get(pupil_id) == run_id
            or state in ("completed", "missed", "no_service")
            or (direction == "WAY_TO" and route.get("checkInTime"))
            or (direction == "WAY_BACK" and route.get("checkOutTime"))
        )
        if done or direction not in ("WAY_TO", "WAY_BACK"):
            return {**blank, "direction": direction, "run_id": run_id}

        eta = None
        source = None
        live = state in (STATE_ON_ROUTE, STATE_ON_BOARD)

        if live and (minutes := self._travel_sensor_minutes()) is not None:
            eta = now + dt.timedelta(minutes=minutes)
            source = SOURCE_LIVE
        elif live:
            eta = eta_from_position(self._trail.get(pupil_id), self.station, now)
            if eta is not None:
                source = SOURCE_LIVE

        if eta is not None and source == SOURCE_LIVE:
            eta = self._smooth(pupil_id, run_id, eta, now)

        # Second opinion, anchored on an event rather than on a speed: the bus
        # left the start of its line at a known moment, and the run from there
        # takes a consistent time. It is available within fifteen seconds of
        # the speed estimate, so it buys no warning - what it buys is safety.
        # Measured over six mornings the speed estimate was up to 92 seconds
        # optimistic; taking whichever of the two is earlier caps that at 18.
        gone = self._departed.get(pupil_id)
        typical_run = median(self.travel_minutes.get(direction))
        if gone and gone[0] == run_id and typical_run:
            from_departure = gone[1] + dt.timedelta(minutes=typical_run)
            eta = earliest(eta, from_departure)
            source = SOURCE_LIVE

        if eta is not None and source == SOURCE_LIVE:
            self._last_live[pupil_id] = (run_id, now, eta)
        elif live:
            # The bus is on the move but momentarily gives no usable speed - at
            # a light, or between fixes. Falling back to the timetable here
            # swings the forecast by minutes and can fire the trigger far too
            # early; the most recent live figure is the better answer.
            previous = self._last_live.get(pupil_id)
            if previous and previous[0] == run_id and \
                    (now - previous[1]) <= dt.timedelta(minutes=5):
                eta, source = previous[2], SOURCE_LIVE

        # What history says this stop is usually served at. Reported alongside
        # the headline rather than as it: before the bus has moved, nothing is
        # known about *today*, and a figure that silently carries an average
        # delay reads like a delay that has already been observed.
        due = (route_arrival(route) if direction == "WAY_BACK"
               else route_departure(route))
        offset = median(self.stop_offsets.get(direction))
        typical = due + dt.timedelta(minutes=offset) if due and offset else None

        if eta is None and due is not None:
            # The timetable, plain. The bus has not shown itself yet, so the
            # published promise is the one the school made.
            eta, source = due, SOURCE_TIMETABLE

        # No minutes figure here on purpose: outside the commute windows this
        # payload is kept rather than refreshed, so a countdown stored in it
        # would freeze with it. The entities derive it from `eta` when read.
        # The question this whole layer exists to answer: on time, or how
        # much later? Only once the bus has shown itself - before that the
        # forecast IS the timetable, and "0 minutes late" would be a claim
        # about a bus nobody has seen.
        delay = None
        if due is not None and eta is not None and source == SOURCE_LIVE:
            delay = round((eta - due).total_seconds() / 60.0, 1)

        return {"eta": eta, "source": source, "typical": typical,
                "offset": offset, "delay": delay,
                "appeared": (self._run_start.get(pupil_id) or (None, None, None))[2],
                "departed": (self._departed.get(pupil_id) or (None, None))[1],
                "typical_run": typical_run,
                "direction": direction, "run_id": run_id, "done": False,
                "samples": len(self.stop_offsets.get(direction or "", []))}
