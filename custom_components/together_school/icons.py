"""Map marker images, served by the integration itself.

Home Assistant's map card falls back to the initials of an entity's name when
it has no picture, which turns three trackers of the same family into three
identical blobs. These views hand out a small SVG each instead: a house for the
stop, a school building, and a badge carrying the actual line number.

Served from the integration so nothing has to be copied into `config/www`.
"""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

URL_BASE = "/api/together_school/marker"

_STOP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
<circle cx="24" cy="24" r="23" fill="#1b5e20" stroke="#ffffff" stroke-width="2"/>
<path fill="#ffffff" d="M24 12 10 24h4v13h8v-8h4v8h8V24h4z"/></svg>"""

_SCHOOL = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
<circle cx="24" cy="24" r="23" fill="#0f3460" stroke="#ffffff" stroke-width="2"/>
<path fill="#ffffff" d="M24 10 8 18v3h32v-3zM12 24h24v13H12z"/>
<rect x="21" y="28" width="6" height="9" fill="#0f3460"/></svg>"""

_BUS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
<circle cx="24" cy="24" r="23" fill="{colour}" stroke="#ffffff" stroke-width="2"/>
<text x="24" y="29" text-anchor="middle" font-family="Arial,Helvetica,sans-serif"
      font-size="{size}" font-weight="bold" fill="#ffffff">{label}</text></svg>"""


def _bus_svg(label: str, on_board: bool) -> str:
    """A badge with the line number; green once the child is aboard."""
    label = (label or "BUS")[:6]
    # Long labels need a smaller face to stay inside the circle.
    size = {1: 22, 2: 22, 3: 20, 4: 17, 5: 14, 6: 12}.get(len(label), 12)
    return _BUS.format(colour="#1b5e20" if on_board else "#e94560",
                       size=size, label=label)


class _SvgView(HomeAssistantView):
    requires_auth = False          # the map card loads these without a token
    cors_allowed = True


class StopMarkerView(_SvgView):
    url = f"{URL_BASE}/stop.svg"
    name = "api:together_school:marker:stop"

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=_STOP, content_type="image/svg+xml")


class SchoolMarkerView(_SvgView):
    url = f"{URL_BASE}/school.svg"
    name = "api:together_school:marker:school"

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=_SCHOOL, content_type="image/svg+xml")


class BusMarkerView(_SvgView):
    url = URL_BASE + "/bus-{label}.svg"
    name = "api:together_school:marker:bus"

    async def get(self, request: web.Request, label: str) -> web.Response:
        on_board = request.query.get("aboard") == "1"
        safe = "".join(c for c in label if c.isalnum())[:6]
        return web.Response(text=_bus_svg(safe, on_board),
                            content_type="image/svg+xml")


def async_register(hass: HomeAssistant) -> None:
    """Register the marker views once."""
    if hass.data.get("together_school_markers"):
        return
    hass.http.register_view(StopMarkerView())
    hass.http.register_view(SchoolMarkerView())
    hass.http.register_view(BusMarkerView())
    hass.data["together_school_markers"] = True
