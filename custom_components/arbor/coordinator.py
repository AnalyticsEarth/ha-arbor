"""Update coordinator for the Arbor integration.

A thin adapter: it owns the Home Assistant concerns -- polling, the config entry,
and when to ask the user for their password again -- and delegates the actual
scraping to :class:`~.scraper.ArborScraper`, which knows nothing about Home
Assistant and can therefore be driven by ``tools/arbor_probe.py`` as well.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArborClient
from .const import DATA_REJECTIONS, DOMAIN
from .errors import ArborAuthError, ArborError
from .models import ArborData
from .scraper import ArborScraper

_LOGGER = logging.getLogger(__name__)

# How many refreshes in a row Arbor must reject the stored credentials before the
# user is asked to re-enter them. Arbor rejects a login under load and while
# rate-limiting, and the password is almost never the real problem, so a single
# rejection is retried silently instead of interrupting the user.
REJECTIONS_BEFORE_REAUTH = 3


class ArborCoordinator(DataUpdateCoordinator[ArborData]):
    """Polls one guardian account on Home Assistant's schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ArborClient,
        *,
        entry_title: str,
        update_interval: timedelta,
    ) -> None:
        """Set up the coordinator for one configured Arbor account."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry_title})",
            update_interval=update_interval,
        )
        self.client = client
        self.scraper = ArborScraper(
            client.async_fetch_absolute,
            client.async_fetch_json,
            client.async_post_json,
            logger=_LOGGER,
        )
        # Consecutive refreshes in which Arbor rejected the stored credentials.
        # Held in hass.data rather than on self, because a failed first refresh
        # makes Home Assistant retry setup with a brand new coordinator -- an
        # instance attribute would reset every time and never reach the
        # threshold, so a genuinely changed password would never be reported.
        self._entry_id = entry.entry_id
        self._rejections: dict[str, int] = hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_REJECTIONS, {}
        )

    async def _async_update_data(self) -> ArborData:
        """Run one full refresh.

        Only a credential rejection repeated across several refreshes asks the
        user for their password again. The stored password stays in the config
        entry throughout and is never cleared, so a transient rejection costs a
        failed update and nothing else.
        """
        try:
            data = await self.scraper.async_scrape()
        except ArborAuthError as err:
            count = self._rejections.get(self._entry_id, 0) + 1
            self._rejections[self._entry_id] = count
            if count < REJECTIONS_BEFORE_REAUTH:
                _LOGGER.warning(
                    "Arbor rejected the stored credentials (attempt %d of %d). "
                    "Retrying with the saved password before asking for a new one: %s",
                    count,
                    REJECTIONS_BEFORE_REAUTH,
                    err,
                )
                raise UpdateFailed(f"Arbor rejected the stored credentials: {err}") from err
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArborError as err:
            raise UpdateFailed(str(err)) from err

        self._rejections.pop(self._entry_id, None)
        return data
