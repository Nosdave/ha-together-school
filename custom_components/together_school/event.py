"""One event entity per child, for automations to hang off.

State changes are awkward to automate on: they race, they repeat on restart,
and "it became on" says nothing about which run it was. Events carry the
context with them and fire exactly once each.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TogetherSchoolConfigEntry
from .entity import TogetherSchoolEntity
from .util import bus_routes, route_state, select_route

EVENT_APPROACHING = "approaching"
EVENT_CHECKED_IN = "checked_in"
EVENT_CHECKED_OUT = "checked_out"
EVENT_MISSED = "missed"

EVENT_TYPES = [EVENT_APPROACHING, EVENT_CHECKED_IN, EVENT_CHECKED_OUT,
               EVENT_MISSED]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TogetherSchoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BusEvent(coordinator, pid) for pid in coordinator.pupil_ids)


class BusEvent(TogetherSchoolEntity, EventEntity):
    """Fires when something worth reacting to happens on the run."""

    _attr_translation_key = "bus_event"
    _attr_icon = "mdi:bus-alert"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator, pupil_id: str) -> None:
        super().__init__(coordinator, pupil_id)
        # Remember per run what has already been announced, so a restart or a
        # repeated poll cannot fire the same event twice.
        self._fired: dict[str, set[str]] = {}

    @property
    def unique_id(self) -> str:
        return f"{self._pupil_id}_event"

    @callback
    def _handle_coordinator_update(self) -> None:
        route = select_route(bus_routes(self._pupil.get("agenda")))
        forecast = self._pupil.get("forecast") or {}
        run_id = str((route or {}).get("activeRouteId")
                     or (route or {}).get("startTime") or "")
        if not run_id:
            super()._handle_coordinator_update()
            return
        seen = self._fired.setdefault(run_id, set())
        # Only the current run matters; drop the rest so this cannot grow.
        for old in [k for k in self._fired if k != run_id]:
            del self._fired[old]

        state = route_state(route)
        for name, happened, extra in (
            (EVENT_MISSED, state == "missed", {}),
            (EVENT_CHECKED_OUT, bool((route or {}).get("checkOutTime")), {}),
            (EVENT_CHECKED_IN, bool((route or {}).get("checkInTime")), {}),
            (EVENT_APPROACHING,
             forecast.get("minutes") is not None
             and forecast.get("minutes") <= self.coordinator.lead_minutes
             and not forecast.get("done"),
             {"minutes_to_stop": forecast.get("minutes"),
              "source": forecast.get("source")}),
        ):
            if happened and name not in seen:
                seen.add(name)
                self._trigger_event(name, {
                    "direction": (route or {}).get("direction"),
                    "bus_number": (route or {}).get("busNumber"),
                    **extra,
                })
                break
        super()._handle_coordinator_update()
