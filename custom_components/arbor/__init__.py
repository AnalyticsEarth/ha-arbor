"""The Arbor Education integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ArborClient, ArborError
from .const import (
    ATTR_PATH,
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_SCHOOL_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
    SERVICE_DUMP_PAGE,
    SERVICE_REFRESH,
)
from .coordinator import ArborCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
    Platform.TODO,
]

type ArborConfigEntry = ConfigEntry[ArborCoordinator]

DUMP_PAGE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required(ATTR_PATH): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ArborConfigEntry) -> bool:
    """Set up Arbor from a config entry."""
    # A per-entry session keeps each school's cookie jar separate.
    session = async_create_clientsession(hass)
    client = ArborClient(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_BASE_URL),
    )

    minutes = max(
        MIN_SCAN_INTERVAL_MINUTES,
        int(entry.options.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)),
    )
    coordinator = ArborCoordinator(
        hass,
        entry,
        client,
        entry_title=entry.data.get(CONF_SCHOOL_NAME) or entry.title,
        update_interval=timedelta(minutes=minutes),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArborConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_logout()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ArborConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services once."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def async_refresh(call: ServiceCall) -> None:
        """Refresh every configured Arbor account now."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_request_refresh()

    async def async_dump_page(call: ServiceCall) -> ServiceResponse:
        """Return the raw JSON tree for one portal page.

        This exists so a user can capture what their own school's portal serves
        when a sensor comes back empty, without having to read Arbor's HTML.
        """
        entry_id = call.data["config_entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(f"No Arbor config entry with id {entry_id}")
        if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
            raise ServiceValidationError("That Arbor config entry is not loaded")
        try:
            tree = await entry.runtime_data.client.async_fetch_absolute(call.data[ATTR_PATH])
        except ArborError as err:
            raise ServiceValidationError(str(err)) from err
        return {"path": call.data[ATTR_PATH], "tree": tree}

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, async_refresh)
    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_PAGE,
        async_dump_page,
        schema=DUMP_PAGE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
