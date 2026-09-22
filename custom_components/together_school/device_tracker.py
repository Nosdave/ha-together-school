"""Map entities: the bus itself plus the two fixed places of the run."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity, extract_latlon
from .util import bus_fix, route_arrival, route_departure


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TogetherSchoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[TrackerEntity] = []
    for pid in coordinator.pupil_ids:
        entities.append(BusTracker(coordinator, pid))
        entities.append(StationTracker(coordinator, pid))
        entities.append(SchoolTracker(coordinator, pid))
    async_add_entities(entities)


class BusTracker(TogetherSchoolEntity, TrackerEntity, RestoreEntity):
    """The bus on the map.

    Keeps the last known position after a run ends instead of disappearing:
    seeing where the bus stood, and for how long, is useful afterwards. The
    ``position_is_current`` attribute and ``last_located`` say how fresh it is.

    The last fix is also restored across restarts, so a restart outside service
    hours does not wipe the marker.
    """

    _attr_translation_key = "bus"
    _attr_icon = "mdi:bus-school"

    _restored: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            return
        lat = last.attributes.get("latitude")
        lon = last.attributes.get("longitude")
        if lat is None or lon is None:
            return
        self._restored = {
            "lat": lat,
            "lon": lon,
            "bus_id": last.attributes.get("bus_id"),
            "at": last.attributes.get("last_located"),
        }

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_bus_tracker"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    def _position(self) -> tuple[float, float] | None:
        delivery = self._pupil.get("delivery")
        if isinstance(delivery, dict):
            if (live := extract_latlon(delivery.get("busLocation"))) is not None:
                return live
        remembered = self._pupil.get("last_seen") or self._restored
        if isinstance(remembered, dict):
            lat, lon = remembered.get("lat"), remembered.get("lon")
            if lat is not None and lon is not None:
                return lat, lon
        return None

    @property
    def latitude(self) -> float | None:
        pos = self._position()
        return pos[0] if pos else None

    @property
    def longitude(self) -> float | None:
        pos = self._position()
        return pos[1] if pos else None

    @property
    def available(self) -> bool:
        return super().available and self._position() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        delivery = self._pupil.get("delivery") or {}
        attrs: dict[str, Any] = {
            k: v for k, v in bus_fix(delivery).items() if v is not None
        }
        if not attrs:
            # No live fix: report the remembered one and mark it as stale.
            remembered = self._pupil.get("last_seen") or self._restored or {}
            if remembered:
                attrs = {
                    "bus_id": remembered.get("bus_id"),
                    "last_located": remembered.get("at"),
                    "position_is_current": False,
                }
        route = self._route or {}
        attrs["bus_number"] = route.get("busNumber") or route.get("name")
        if (dep := route_departure(route)) is not None:
            attrs["scheduled_departure"] = dep.isoformat()
        if (arr := route_arrival(route)) is not None:
            attrs["scheduled_arrival"] = arr.isoformat()
        return {k: v for k, v in attrs.items() if v is not None}


class _PlaceTracker(TogetherSchoolEntity, TrackerEntity):
    """A fixed place of the run, shown on the map like the app does."""

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    def _place(self) -> tuple[float, float] | None:
        raise NotImplementedError

    @property
    def latitude(self) -> float | None:
        place = self._place()
        return place[0] if place else None

    @property
    def longitude(self) -> float | None:
        place = self._place()
        return place[1] if place else None

    @property
    def available(self) -> bool:
        return super().available and self._place() is not None


class StationTracker(_PlaceTracker):
    """The child's own boarding stop."""

    _attr_translation_key = "station"
    _attr_icon = "mdi:bus-stop"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_station"

    def _place(self) -> tuple[float, float] | None:
        return self.coordinator.station

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        route = self._route or {}
        # On the way home the roles swap: the stop is the destination.
        name = (
            route.get("arrivalStation")
            if route.get("direction") == "WAY_BACK"
            else route.get("boardingStation")
        )
        return {"name": name} if name else {}


class SchoolTracker(_PlaceTracker):
    """The school."""

    _attr_translation_key = "school"
    _attr_icon = "mdi:school"

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_school"

    def _place(self) -> tuple[float, float] | None:
        return self.coordinator.school
