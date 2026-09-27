"""The Together School integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TogetherSchoolApi, TogetherSchoolAuthError, TogetherSchoolError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACTIVE_HOURS_ONLY,
    CONF_ACTIVE_WINDOWS,
    CONF_DEVICE_TOKEN,
    CONF_LOCALE,
    CONF_LOGIN,
    CONF_LEAD_MINUTES,
    CONF_SCHOOL_CODE,
    CONF_SCHOOL_LATLON,
    CONF_STOP_OFFSETS,
    CONF_TRAVEL_SENSOR,
    CONF_STATION_LATLON,
    CONF_TENANT_ID,
    DEFAULT_ACTIVE_WINDOWS,
    DEFAULT_LEAD_MINUTES,
    parse_windows,
)
from .coordinator import TogetherSchoolCoordinator
from .icons import async_register as async_register_markers

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
]

type TogetherSchoolConfigEntry = ConfigEntry[TogetherSchoolCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: TogetherSchoolConfigEntry
) -> bool:
    """Set up Together School from a config entry."""
    async_register_markers(hass)

    api = TogetherSchoolApi(
        async_get_clientsession(hass),
        school_code=entry.data[CONF_SCHOOL_CODE],
        tenant_id=entry.data[CONF_TENANT_ID],
        locale=entry.data[CONF_LOCALE],
        device_token=entry.data[CONF_DEVICE_TOKEN],
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        login=entry.data.get(CONF_LOGIN),
        # No password is stored, so this client can never sign in by itself and
        # never mints a new token; a rejected one becomes the re-auth flow.
        # (That also means the update listener below only ever sees option
        # changes, so it cannot loop.)
    )

    windows = parse_windows(entry.options.get(CONF_ACTIVE_WINDOWS)) \
        or DEFAULT_ACTIVE_WINDOWS

    def _as_pair(value):
        """Stored as a [lat, lon] list in JSON."""
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return float(value[0]), float(value[1])
        return None

    def _remember_offsets(offsets) -> None:
        """Persist what we have learned, so a restart keeps the calibration."""
        if entry.data.get(CONF_STOP_OFFSETS) != offsets:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_STOP_OFFSETS: dict(offsets)}
            )

    def _remember_places(station, school) -> None:
        """Persist the fixed places so a restart keeps them on the map."""
        data = {**entry.data}
        if station:
            data[CONF_STATION_LATLON] = list(station)
        if school:
            data[CONF_SCHOOL_LATLON] = list(school)
        if data != entry.data:
            hass.config_entries.async_update_entry(entry, data=data)
    coordinator = TogetherSchoolCoordinator(
        hass,
        api,
        active_hours_only=entry.options.get(
            CONF_ACTIVE_HOURS_ONLY, entry.data.get(CONF_ACTIVE_HOURS_ONLY, True)
        ),
        active_windows=windows,
        station=_as_pair(entry.data.get(CONF_STATION_LATLON)),
        school=_as_pair(entry.data.get(CONF_SCHOOL_LATLON)),
        on_places_learned=_remember_places,
        lead_minutes=int(entry.options.get(CONF_LEAD_MINUTES,
                                           DEFAULT_LEAD_MINUTES)),
        travel_sensor=entry.options.get(CONF_TRAVEL_SENSOR) or None,
        stop_offsets=entry.data.get(CONF_STOP_OFFSETS) or {},
        on_offsets_learned=_remember_offsets,
    )
    try:
        await coordinator.async_setup()
    except TogetherSchoolAuthError as err:
        # Surfaces in the UI as "reconfigure"/"re-authenticate" - the flow then
        # asks only for the password.
        raise ConfigEntryAuthFailed(str(err)) from err
    except TogetherSchoolError as err:
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_config_entry_first_refresh()

    # Remember which options this setup was built with, so the listener below
    # can tell an options change from a routine data write (the learned stop
    # and school coordinates) and avoid reloading itself in a loop.
    coordinator.options_snapshot = dict(entry.options)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: TogetherSchoolConfigEntry
) -> None:
    """Reload only when the user actually changed the options."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None and dict(entry.options) == getattr(
        coordinator, "options_snapshot", None
    ):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: TogetherSchoolConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
