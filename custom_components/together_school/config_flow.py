"""Config and re-auth flow for Together School.

The user only ever types a mobile number and a password. The password is
exchanged for a long-lived access token and then discarded - only the token
(plus a generated device token) is written to the config entry.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    TogetherSchoolApi,
    TogetherSchoolAuthError,
    TogetherSchoolError,
    async_discover_tenant,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACTIVE_HOURS_ONLY,
    CONF_ACTIVE_WINDOWS,
    CONF_DEVICE_TOKEN,
    CONF_LOCALE,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCHOOL_CODE,
    CONF_TENANT_ID,
    DEFAULT_ACTIVE_WINDOWS,
    DEFAULT_LOCALE,
    DOMAIN,
    normalise_school_code,
    parse_windows,
    windows_to_text,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOGIN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEL)
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_SCHOOL_CODE): str,
        # Free text, not an allowlist: this platform is used well beyond the
        # Benelux and the header is only a language hint to the backend.
        vol.Required(CONF_LOCALE, default=DEFAULT_LOCALE): str,
        vol.Required(CONF_ACTIVE_HOURS_ONLY, default=True): bool,
    }
)

STEP_TENANT_SCHEMA = vol.Schema({vol.Required(CONF_TENANT_ID): str})

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


class TogetherSchoolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TogetherSchoolOptionsFlow:
        return TogetherSchoolOptionsFlow()

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials, trade them for a token, store only the token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._input = dict(user_input)
            # Users paste anything from a padded upper-case code to a full URL.
            school = normalise_school_code(user_input[CONF_SCHOOL_CODE])
            if not school:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self.add_suggested_values_to_schema(
                        STEP_USER_SCHEMA,
                        {k: v for k, v in user_input.items()
                         if k != CONF_PASSWORD},
                    ),
                    errors={CONF_SCHOOL_CODE: "invalid_school_code"},
                )
            self._input[CONF_SCHOOL_CODE] = school
            # The tenant id differs per school, so read it from that school's
            # own public web config instead of shipping a guess.
            tenant = await async_discover_tenant(
                async_get_clientsession(self.hass), school
            )
            if tenant is None:
                return await self.async_step_tenant()
            return await self._async_finish(tenant)
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_tenant(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Only reached when the tenant could not be discovered."""
        if user_input is not None:
            return await self._async_finish(user_input[CONF_TENANT_ID])
        return self.async_show_form(
            step_id="tenant",
            data_schema=STEP_TENANT_SCHEMA,
            description_placeholders={
                "school_code": self._input.get(CONF_SCHOOL_CODE, "")
            },
        )

    async def _async_finish(self, tenant_id: str) -> ConfigFlowResult:
        """Authenticate with the resolved tenant and create the entry."""
        user_input = self._input
        # One stable device token per entry: MOBILE sign-in requires a
        # non-empty deviceToken, and reusing it keeps HA as a single device
        # instead of registering a new one on every login.
        device_token = f"ha-{uuid.uuid4().hex}"
        token, me, errors = await self._async_authenticate(
            login=user_input[CONF_LOGIN],
            password=user_input[CONF_PASSWORD],
            school_code=user_input[CONF_SCHOOL_CODE],
            tenant_id=tenant_id,
            locale=user_input[CONF_LOCALE],
            device_token=device_token,
        )
        if errors:
            # Send the user back to the first step with what they typed still
            # filled in (minus the password).
            return self.async_show_form(
                step_id="user",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA,
                    {k: v for k, v in user_input.items() if k != CONF_PASSWORD},
                ),
                errors=errors,
            )

        account = me.get("userId") or me.get("id") or user_input[CONF_LOGIN]
        # Scope by tenant: user ids are only unique within a deployment, and a
        # parent with children at two schools shares one phone number.
        await self.async_set_unique_id(f"{tenant_id}:{account}")
        self._abort_if_unique_id_configured()
        name = me.get("fullName") or user_input[CONF_LOGIN]
        return self.async_create_entry(
            title=f"Together School ({name})",
            # NOTE: the password is deliberately NOT stored.
            data={
                CONF_LOGIN: user_input[CONF_LOGIN],
                CONF_SCHOOL_CODE: user_input[CONF_SCHOOL_CODE],
                CONF_TENANT_ID: tenant_id,
                CONF_LOCALE: user_input[CONF_LOCALE],
                CONF_ACTIVE_HOURS_ONLY: user_input[CONF_ACTIVE_HOURS_ONLY],
                CONF_DEVICE_TOKEN: device_token,
                CONF_ACCESS_TOKEN: token,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Token went stale - ask for the password again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            token, _me, errors = await self._async_authenticate(
                login=entry.data[CONF_LOGIN],
                password=user_input[CONF_PASSWORD],
                school_code=entry.data[CONF_SCHOOL_CODE],
                tenant_id=entry.data[CONF_TENANT_ID],
                locale=entry.data[CONF_LOCALE],
                device_token=entry.data[CONF_DEVICE_TOKEN],
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_ACCESS_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"login": entry.data[CONF_LOGIN]},
        )

    async def _async_authenticate(
        self,
        *,
        login: str,
        password: str,
        school_code: str,
        tenant_id: str,
        locale: str,
        device_token: str,
    ) -> tuple[str | None, dict[str, Any], dict[str, str]]:
        """Sign in and return (token, user_info, errors)."""
        api = TogetherSchoolApi(
            async_get_clientsession(self.hass),
            school_code=school_code,
            tenant_id=tenant_id,
            locale=locale,
            device_token=device_token,
            login=login,
            password=password,
        )
        try:
            token = await api.async_login()
            me = await api.async_get_me()
        except TogetherSchoolAuthError:
            return None, {}, {"base": "invalid_auth"}
        except TogetherSchoolError:
            return None, {}, {"base": "cannot_connect"}
        return token, me if isinstance(me, dict) else {}, {}


class TogetherSchoolOptionsFlow(OptionsFlow):
    """Let the user retune polling to their own school's timetable."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry
        current = entry.options.get(
            CONF_ACTIVE_WINDOWS,
            windows_to_text(DEFAULT_ACTIVE_WINDOWS),
        )
        current_only = entry.options.get(
            CONF_ACTIVE_HOURS_ONLY,
            entry.data.get(CONF_ACTIVE_HOURS_ONLY, True),
        )

        if user_input is not None:
            windows_text = user_input[CONF_ACTIVE_WINDOWS]
            if parse_windows(windows_text) is None:
                errors[CONF_ACTIVE_WINDOWS] = "invalid_windows"
            else:
                return self.async_create_entry(data=user_input)
            current = windows_text
            current_only = user_input[CONF_ACTIVE_HOURS_ONLY]

        schema = vol.Schema(
            {
                vol.Required(CONF_ACTIVE_HOURS_ONLY, default=current_only): bool,
                vol.Required(CONF_ACTIVE_WINDOWS, default=current): str,
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
