"""Config flow for the Arbor Education integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ArborAuthError, ArborClient, ArborConnectionError, ArborNoSchoolsError
from .const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_SCHOOL_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .models import ArborSchool

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class ArborConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collect guardian credentials and pick a school."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow state."""
        self._email: str = ""
        self._password: str = ""
        self._schools: list[ArborSchool] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the guardian's Arbor email address and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]
            errors = await self._async_load_schools()
            if not errors:
                if len(self._schools) == 1:
                    return await self._async_create(self._schools[0])
                return await self.async_step_school()

        return self.async_show_form(
            step_id="user", data_schema=CREDENTIALS_SCHEMA, errors=errors
        )

    async def async_step_school(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which school to connect when the account has several."""
        if user_input is not None:
            chosen = next(
                (
                    school
                    for school in self._schools
                    if school.base_url == user_input[CONF_BASE_URL]
                ),
                None,
            )
            if chosen is not None:
                return await self._async_create(chosen)

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": school.base_url, "label": school.label}
                            for school in self._schools
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="school", data_schema=schema)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a re-authentication flow for an existing entry."""
        self._email = str(entry_data.get(CONF_EMAIL, ""))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh password and verify it."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            self._email = entry.data[CONF_EMAIL]
            errors = await self._async_load_schools()
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: self._password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="current-password"
                        )
                    )
                }
            ),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    # -- helpers ------------------------------------------------------------

    async def _async_load_schools(self) -> dict[str, str]:
        """Validate the credentials and remember the tenants they unlock."""
        session = async_create_clientsession(self.hass)
        client = ArborClient(session, self._email, self._password)
        try:
            self._schools = await client.async_list_schools()
        except ArborNoSchoolsError:
            return {"base": "invalid_auth"}
        except ArborAuthError:
            return {"base": "invalid_auth"}
        except ArborConnectionError as err:
            _LOGGER.debug("Cannot reach Arbor during setup: %s", err)
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
            _LOGGER.exception("Unexpected error validating Arbor credentials")
            return {"base": "unknown"}
        return {}

    async def _async_create(self, school: ArborSchool) -> ConfigFlowResult:
        """Create the config entry for the chosen school."""
        await self.async_set_unique_id(f"{self._email.casefold()}@{school.base_url}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{school.short_name or school.name} ({self._email})",
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_BASE_URL: school.base_url,
                CONF_SCHOOL_NAME: school.short_name or school.name,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return ArborOptionsFlow()


class ArborOptionsFlow(OptionsFlow):
    """Let the user tune how often Arbor is polled."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL_MINUTES: int(
                        user_input[CONF_SCAN_INTERVAL_MINUTES]
                    )
                }
            )

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL_MINUTES, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=720,
                        step=5,
                        unit_of_measurement="minutes",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
