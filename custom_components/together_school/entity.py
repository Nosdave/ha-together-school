"""Shared helpers and base entity for Together School."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TogetherSchoolCoordinator
from .util import bus_routes, extract_latlon, pupil_display_name, select_route

__all__ = ["TogetherSchoolEntity", "extract_latlon", "pupil_display_name"]


class TogetherSchoolEntity(CoordinatorEntity[TogetherSchoolCoordinator]):
    """Base entity bound to one pupil."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TogetherSchoolCoordinator, pupil_id: str
    ) -> None:
        super().__init__(coordinator)
        self._pupil_id = pupil_id

    @property
    def _pupil(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("pupils", {}).get(
            self._pupil_id, {}
        )

    @property
    def _route(self) -> dict[str, Any] | None:
        """The BUS_ROUTE entry that matters right now for this pupil."""
        return select_route(bus_routes(self._pupil.get("agenda")))

    @property
    def device_info(self) -> DeviceInfo:
        name = pupil_display_name(
            self.coordinator.pupils.get(self._pupil_id), self._pupil_id
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._pupil_id)},
            name=f"School bus - {name}",
            manufacturer="Together School",
            model="School transport",
        )
