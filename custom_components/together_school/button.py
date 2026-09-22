"""A refresh button, mirroring the one the official app puts on its map."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TogetherSchoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(RefreshButton(coordinator, pid) for pid in coordinator.pupil_ids)


class RefreshButton(TogetherSchoolEntity, ButtonEntity):
    """Fetch the current position now instead of waiting for the next poll."""

    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_refresh"

    @property
    def available(self) -> bool:
        # Useful even when the bus reports nothing - that is often exactly
        # when you want to poke it.
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
