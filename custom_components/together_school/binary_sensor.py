"""Binary sensors derived from the pupil's bus route for the day."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity
from .util import STATE_MISSED, is_on_bus, route_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TogetherSchoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for pid in coordinator.pupil_ids:
        entities.append(OnBusBinarySensor(coordinator, pid))
        entities.append(MissedBusBinarySensor(coordinator, pid))
    async_add_entities(entities)


class OnBusBinarySensor(TogetherSchoolEntity, BinarySensorEntity):
    """On between check-in and check-out."""

    _attr_translation_key = "on_bus"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_icon = "mdi:seat-passenger"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_on_bus"

    @property
    def is_on(self) -> bool:
        return is_on_bus(self._route)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        route = self._route or {}
        return {
            "check_in_time": route.get("checkInTime"),
            "check_out_time": route.get("checkOutTime"),
        }


class MissedBusBinarySensor(TogetherSchoolEntity, BinarySensorEntity):
    """On when the school marked the child as having missed the bus."""

    _attr_translation_key = "missed_bus"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:bus-alert"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_missed_bus"

    @property
    def is_on(self) -> bool:
        return route_state(self._route) == STATE_MISSED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"missed_time": (self._route or {}).get("missedTime")}
