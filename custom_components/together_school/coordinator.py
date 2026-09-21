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
)
from .util import extract_pupils, pick_id

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
        if self._active_hours_only and not _in_active_window(
            now, self._active_windows
        ):
            # Keep the last known data; skip the network round-trip.
            return self.data or {"pupils": {}, "parent_id": self.parent_id}

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
                result["pupils"][pid] = entry
        except TogetherSchoolAuthError as err:
            # Token no longer valid and we hold no password -> ask the user.
            raise ConfigEntryAuthFailed(str(err)) from err
        except TogetherSchoolError as err:
            raise UpdateFailed(str(err)) from err
        return result

