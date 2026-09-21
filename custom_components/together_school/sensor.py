"""Sensors for the pupil's bus run: status and the scheduled times."""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity
from .util import (
    STATE_COMPLETED,
    STATE_MISSED,
    STATE_NO_SERVICE,
    STATE_ON_BOARD,
    STATE_ON_ROUTE,
    STATE_SCHEDULED,
    route_arrival,
    route_departure,
    route_state,
)

# Exposed so automations and dashboards can rely on a documented set.
BUS_STATES = [
    STATE_NO_SERVICE,
    STATE_SCHEDULED,
    STATE_ON_ROUTE,
    STATE_ON_BOARD,
    STATE_COMPLETED,
    STATE_MISSED,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TogetherSchoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for pid in coordinator.pupil_ids:
        entities.append(BusStatusSensor(coordinator, pid))
        entities.append(DepartureSensor(coordinator, pid))
        entities.append(ArrivalSensor(coordinator, pid))
    async_add_entities(entities)


class BusStatusSensor(TogetherSchoolEntity, SensorEntity):
    """Where the child's run currently stands."""

    _attr_translation_key = "bus_status"
    _attr_icon = "mdi:bus-clock"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = BUS_STATES

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_bus_status"

    @property
    def native_value(self) -> str:
        return route_state(self._route)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        route = self._route or {}
        return {
            "bus_number": route.get("busNumber") or route.get("name"),
            # WAY_TO = morning run to school, WAY_BACK = run home.
            "direction": route.get("direction"),
            "boarding_station": route.get("boardingStation"),
            "arrival_station": route.get("arrivalStation"),
            "check_in_time": route.get("checkInTime"),
            "check_out_time": route.get("checkOutTime"),
            "missed_time": route.get("missedTime"),
            "online": route.get("online"),
        }


class DepartureSensor(TogetherSchoolEntity, SensorEntity):
    """Scheduled departure from the boarding stop."""

    _attr_translation_key = "departure"
    _attr_icon = "mdi:bus-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_departure"

    @property
    def native_value(self) -> dt.datetime | None:
        return route_departure(self._route)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"boarding_station": (self._route or {}).get("boardingStation")}


class ArrivalSensor(TogetherSchoolEntity, SensorEntity):
    """Scheduled arrival at the destination."""

    _attr_translation_key = "arrival"
    _attr_icon = "mdi:map-marker-check"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_arrival"

    @property
    def native_value(self) -> dt.datetime | None:
        return route_arrival(self._route)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"arrival_station": (self._route or {}).get("arrivalStation")}
