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
from homeassistant.util import dt as dt_util

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity
from .util import (
    PUNCTUALITY_UNKNOWN,
    STATE_COMPLETED,
    STATE_MISSED,
    STATE_NO_SERVICE,
    STATE_ON_BOARD,
    STATE_ON_ROUTE,
    STATE_SCHEDULED,
    minutes_until,
    route_arrival,
    route_checkin,
    route_checkout,
    route_departure,
    route_missed,
    route_state,
    punctuality,
)

# Values the backend has been seen to use, plus a catch-all.
PUNCTUALITY_STATES = ["on_time", "delayed", "completed", PUNCTUALITY_UNKNOWN]

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
        entities.append(PunctualitySensor(coordinator, pid))
        entities.append(CheckInSensor(coordinator, pid))
        entities.append(CheckOutSensor(coordinator, pid))
        entities.append(StopEtaSensor(coordinator, pid))
        entities.append(MinutesToStopSensor(coordinator, pid))
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
            # ON_TIME / delayed etc. - the backend's own punctuality verdict.
            "route_state": route.get("routeState"),
            "student_state": route.get("studentState"),
            # Anchored to the run's date: the raw values are bare UTC times
            # and would read two hours off for a reader in Brussels.
            "check_in_time": route_checkin(route),
            "check_out_time": route_checkout(route),
            "missed_time": route_missed(route),
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


class PunctualitySensor(TogetherSchoolEntity, SensorEntity):
    """Whether the run is on time - the backend reports this itself."""

    _attr_translation_key = "punctuality"
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PUNCTUALITY_STATES

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_punctuality"

    @property
    def native_value(self) -> str:
        value = punctuality(self._route)
        # An ENUM sensor must never report a value outside its options.
        return value if value in PUNCTUALITY_STATES else PUNCTUALITY_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = (self._route or {}).get("routeState")
        return {"reported": raw} if raw else {}


class CheckInSensor(TogetherSchoolEntity, SensorEntity):
    """When the child boarded.

    A timestamp sensor rather than an attribute: an absent attribute renders as
    the epoch (01:00 local), which reads like a real time and is worse than
    showing nothing.
    """

    _attr_translation_key = "checked_in"
    _attr_icon = "mdi:login"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_checked_in"

    @property
    def native_value(self) -> dt.datetime | None:
        return route_checkin(self._route)


class CheckOutSensor(TogetherSchoolEntity, SensorEntity):
    """When the child got off."""

    _attr_translation_key = "checked_out"
    _attr_icon = "mdi:logout"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_checked_out"

    @property
    def native_value(self) -> dt.datetime | None:
        return route_checkout(self._route)


class StopEtaSensor(TogetherSchoolEntity, SensorEntity):
    """When the bus should reach this child's own stop.

    The headline figure for automations: it answers "when do we need to be
    downstairs". `source` says whether that is live or an estimate, so an
    automation can require a live figure before, say, calling a lift.
    """

    _attr_translation_key = "stop_eta"
    _attr_icon = "mdi:bus-marker"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_stop_eta"

    @property
    def native_value(self) -> dt.datetime | None:
        return (self._pupil.get("forecast") or {}).get("eta")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = self._pupil.get("forecast") or {}
        return {k: v for k, v in {
            "source": f.get("source"),
            "direction": f.get("direction"),
            # What history says this stop is usually served at, and by how
            # much it differs from the timetable. Kept beside the headline
            # rather than folded into it: until the bus has moved, nothing is
            # known about today, and an average delay baked into the forecast
            # reads like one that has already happened.
            "typical": f.get("typical"),
            "typical_offset_minutes": f.get("offset"),
            "learned_samples": f.get("samples"),
        }.items() if v is not None}


class MinutesToStopSensor(TogetherSchoolEntity, SensorEntity):
    """Whole minutes until the bus reaches the stop.

    Counted here from the forecast timestamp rather than taken from the
    coordinator's snapshot. Outside the commute windows the coordinator keeps
    its last payload instead of polling, so a figure computed there would
    freeze: on one quiet afternoon it sat at 454 minutes from a quarter to
    nine until a quarter to three. The timestamp stays true all day, and a
    display or a voice answer can be asked at any hour.
    """

    _attr_translation_key = "minutes_to_stop"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_minutes_to_stop"

    @property
    def native_value(self) -> int | None:
        eta = (self._pupil.get("forecast") or {}).get("eta")
        return minutes_until(eta, dt_util.now())
