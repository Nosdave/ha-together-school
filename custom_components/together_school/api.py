"""Async client for the Together School backend.

Authentication model
--------------------
Signing in returns a long-lived ``accessToken`` and nothing else - there is no
refresh token and no expiry field. So the integration exchanges the password for
a token exactly once (at setup, or during a re-auth) and afterwards persists
*only* the token. The password is never written to the config entry.

When the token is eventually rejected, the client raises
:class:`TogetherSchoolAuthError`; the integration turns that into Home
Assistant's re-auth flow, which asks for the password again.

Wire details discovered from the official Android app and verified live:

* ``Authorization`` carries the raw token - **no** ``Bearer `` prefix.
* ``X-TenantID`` and ``Locale`` are required on every request.
* Parent accounts must use ``clientType: MOBILE``; ``WEB`` returns 403.
* ``MOBILE`` requires a non-empty ``deviceToken`` (any opaque string).
* The login identifier is the **mobile phone number**, not the email address.
* Every response is wrapped as ``{"status": "SUCCESS", "data": <payload>}``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import (
    BASE_URL_TEMPLATE,
    CLIENT_TYPE,
    EP_ENVIRONMENT,
    EP_DELIVERY_WITH_BUS,
    EP_ME,
    EP_PARENT_PUPILS,
    EP_PUPIL,
    EP_PUPIL_COMBINED_AGENDA,
    EP_SIGNIN,
    HEADER_AUTHORIZATION,
    HEADER_LOCALE,
    HEADER_TENANT,
)
from .util import parse_tenant_id, unwrap_envelope

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def async_discover_tenant(
    session: aiohttp.ClientSession, school_code: str
) -> str | None:
    """Look up a deployment's tenant id from its public web config.

    The tenant is not shared between schools, so it cannot be guessed; it is
    read from the school's own front-end config.
    Returns ``None`` if it cannot be determined, leaving the caller to ask.
    """
    url = f"{BASE_URL_TEMPLATE.format(school_code=school_code)}{EP_ENVIRONMENT}"
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return parse_tenant_id(await resp.text())
    except (aiohttp.ClientError, asyncio.TimeoutError):
        # A timeout is not a ClientError; without this a slow school server
        # aborts the whole config flow instead of falling back to asking.
        return None


class TogetherSchoolError(Exception):
    """Backend or transport error."""


class TogetherSchoolAuthError(TogetherSchoolError):
    """Credentials or token rejected - re-authentication is required."""


class TogetherSchoolApi:
    """Thin async wrapper around the Together School REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        school_code: str,
        tenant_id: str,
        locale: str,
        device_token: str,
        access_token: str | None = None,
        login: str | None = None,
        password: str | None = None,
        on_token_refreshed: Callable[[str], None] | None = None,
    ) -> None:
        """Create a client.

        ``password`` is optional and only held in memory for the duration of a
        sign-in; it is never persisted by this class. ``on_token_refreshed`` is
        invoked whenever a new token is obtained so the caller can store it.
        """
        self._session = session
        self._base_url = BASE_URL_TEMPLATE.format(school_code=school_code)
        self._tenant_id = tenant_id
        self._locale = locale
        self._device_token = device_token
        self._access_token = access_token
        self._login = login
        self._password = password
        self._on_token_refreshed = on_token_refreshed

    @property
    def access_token(self) -> str | None:
        """The current access token, if we have one."""
        return self._access_token

    # -- low-level --------------------------------------------------------
    def _headers(self, *, with_auth: bool = True) -> dict[str, str]:
        headers = {
            HEADER_TENANT: self._tenant_id,
            HEADER_LOCALE: self._locale,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if with_auth and self._access_token:
            headers[HEADER_AUTHORIZATION] = self._access_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        with_auth: bool = True,
        _retry: bool = True,
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                json=json,
                headers=self._headers(with_auth=with_auth),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body_text = await resp.text()

                if resp.status in (401, 403) and with_auth:
                    # Token rejected. If we still hold the password (i.e. we are
                    # inside setup or a re-auth) we can silently sign in again.
                    if _retry and self._password:
                        _LOGGER.debug(
                            "Token rejected (%s); re-authenticating", resp.status
                        )
                        await self.async_login()
                        return await self._request(
                            method,
                            path,
                            json=json,
                            with_auth=with_auth,
                            _retry=False,
                        )
                    raise TogetherSchoolAuthError(
                        f"Unauthorized ({resp.status}) calling {path}"
                    )

                if resp.status >= 400:
                    raise TogetherSchoolError(
                        f"HTTP {resp.status} calling {path}: {body_text[:300]}"
                    )

                if not body_text:
                    return None
                try:
                    return unwrap_envelope(await resp.json(content_type=None))
                except ValueError:
                    return body_text
        except asyncio.TimeoutError as err:
            raise TogetherSchoolError(f"Timeout calling {path}") from err
        except aiohttp.ClientError as err:
            raise TogetherSchoolError(
                f"Connection error calling {path}: {err}"
            ) from err

    # -- auth -------------------------------------------------------------
    async def async_login(self) -> str:
        """Exchange the password for an access token.

        Returns the new token and notifies ``on_token_refreshed`` so the caller
        can persist it.
        """
        if not self._password or not self._login:
            raise TogetherSchoolAuthError(
                "No password available - re-authentication required"
            )
        payload = {
            "login": self._login,
            "clientType": CLIENT_TYPE,
            "deviceToken": self._device_token,
            "locale": self._locale,
            "password": self._password,
            "timeZoneOffset": 0,
        }
        data = await self._request(
            "POST", EP_SIGNIN, json=payload, with_auth=False, _retry=False
        )
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise TogetherSchoolAuthError(
                f"Sign-in returned no access token: {str(data)[:200]}"
            )
        self._access_token = data["accessToken"]
        if self._on_token_refreshed:
            self._on_token_refreshed(self._access_token)
        return self._access_token

    async def async_validate(self) -> dict[str, Any]:
        """Make sure we can talk to the API, signing in first if needed."""
        if not self._access_token:
            await self.async_login()
        return await self.async_get_me()

    def forget_password(self) -> None:
        """Drop the in-memory password once a token has been obtained."""
        self._password = None

    # -- data -------------------------------------------------------------
    async def async_get_me(self) -> dict[str, Any]:
        """The authenticated user; ``userId`` is the parent id."""
        return await self._request("GET", EP_ME)

    async def async_get_pupils(self, parent_id: str) -> Any:
        """The parent's children (a plain JSON array)."""
        return await self._request(
            "GET", EP_PARENT_PUPILS.format(parent_id=parent_id)
        )

    async def async_get_pupil(self, pupil_id: str) -> dict[str, Any]:
        return await self._request("GET", EP_PUPIL.format(pupil_id=pupil_id))

    async def async_get_delivery_with_bus(self, pupil_id: str) -> dict[str, Any]:
        """Live bus + pupil/school/station locations for one pupil."""
        return await self._request(
            "GET", EP_DELIVERY_WITH_BUS.format(pupil_id=pupil_id)
        )

    async def async_get_combined_agenda(
        self, pupil_id: str, date: str
    ) -> dict[str, Any]:
        """Agenda for a date (``YYYY-MM-DD``), incl. bus check-in/out."""
        return await self._request(
            "GET", EP_PUPIL_COMBINED_AGENDA.format(pupil_id=pupil_id, date=date)
        )
