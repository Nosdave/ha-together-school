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
    SCAN_INTERVAL_IDLE,
    SCAN_INTERVAL_LIVE,
)
from .util import (
    STATE_ON_BOARD,
    STATE_ON_ROUTE,
    bus_routes,
    extract_latlon,
    extract_pupils,
    pick_id,
    route_state,
    select_route,
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

