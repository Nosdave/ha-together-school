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
    CONF_SCHOOL_CODE,
    CONF_TENANT_ID,
    DEFAULT_ACTIVE_WINDOWS,
    parse_windows,
)
from .coordinator import TogetherSchoolCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]

type TogetherSchoolConfigEntry = ConfigEntry[TogetherSchoolCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: TogetherSchoolConfigEntry
) -> bool:
    """Set up Together School from a config entry."""

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
    coordinator = TogetherSchoolCoordinator(
        hass,
        api,
        active_hours_only=entry.options.get(
            CONF_ACTIVE_HOURS_ONLY, entry.data.get(CONF_ACTIVE_HOURS_ONLY, True)
        ),
        active_windows=windows,
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

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: TogetherSchoolConfigEntry
) -> None:
    """Apply changed options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: TogetherSchoolConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
