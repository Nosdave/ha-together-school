"""Binary sensors derived from the pupil's bus route for the day."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity
from .util import (
    SOURCE_LIVE,
    STATE_MISSED,
    is_on_bus,
    minutes_until,
    route_checkin,
    route_checkout,
    route_missed,
    route_state,
)


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
        entities.append(ArrivingSoonBinarySensor(coordinator, pid))
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
            "check_in_time": route_checkin(route),
            "check_out_time": route_checkout(route),
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
        return {"missed_time": route_missed(self._route or {})}


class ArrivingSoonBinarySensor(TogetherSchoolEntity, BinarySensorEntity):
    """The automation trigger: the bus is nearly at the stop.

    Latched on purpose. A raw comparison against the forecast crosses the
    threshold, slips back a few seconds later and crosses again - which would
    call a lift three times and repeat an announcement. Once it turns on it
    stays on for that run, and only resets when the next run comes round.
    """

    _attr_translation_key = "arriving_soon"
    _attr_icon = "mdi:bus-clock"

    _latched_run: str | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_arriving_soon"

    @property
    def is_on(self) -> bool:
        forecast = self._pupil.get("forecast") or {}
        run_id = forecast.get("run_id")
        if forecast.get("done") or not run_id:
            # A finished run releases the latch for the next one.
            if self._latched_run == run_id:
                self._latched_run = None
            return False
        if self._latched_run == run_id:
            return True
        minutes = minutes_until(forecast.get("eta"), dt_util.now())
        if minutes is not None and minutes <= self.coordinator.lead_minutes:
            self._latched_run = run_id
            return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        forecast = self._pupil.get("forecast") or {}
        return {
            "lead_minutes": self.coordinator.lead_minutes,
            "minutes_to_stop": minutes_until(forecast.get("eta"), dt_util.now()),
            "source": forecast.get("source"),
            "is_live": forecast.get("source") == SOURCE_LIVE,
        }
