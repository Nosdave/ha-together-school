"""Device tracker: live position of each pupil's school bus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity, extract_latlon


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TogetherSchoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        BusTracker(coordinator, pid) for pid in coordinator.pupil_ids
    )


class BusTracker(TogetherSchoolEntity, TrackerEntity):
    """Shows the bus location on the HA map."""

    _attr_translation_key = "bus"
    _attr_icon = "mdi:bus-school"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_bus_tracker"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    def _bus_latlon(self) -> tuple[float, float] | None:
        delivery = self._pupil.get("delivery")
        if not isinstance(delivery, dict):
            return None
        return extract_latlon(delivery.get("busLocation"))

    @property
    def latitude(self) -> float | None:
        loc = self._bus_latlon()
        return loc[0] if loc else None

    @property
    def longitude(self) -> float | None:
        loc = self._bus_latlon()
        return loc[1] if loc else None

    @property
    def available(self) -> bool:
        return super().available and self._bus_latlon() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        delivery = self._pupil.get("delivery") or {}
        bus = delivery.get("busLocation") or {}
        attrs: dict[str, Any] = {}
        if isinstance(bus, dict) and bus.get("busId"):
            attrs["bus_id"] = bus["busId"]
        station = extract_latlon(delivery.get("stationLocation"))
        if station:
            attrs["station_lat"], attrs["station_lon"] = station
        return attrs
