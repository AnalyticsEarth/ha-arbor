"""HTTP client for the Arbor Education parent portal.

The parent portal has no public API, so this talks to the same endpoints the
portal's own JavaScript uses. The login handshake and the ``format=javascript``
page API were read out of Arbor's front-end bundle; docs/PROTOCOL.md records
where each piece came from.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .const import (
    ARBOR_LOGIN_HOST,
    AUTH_LOGIN_PATH,
    AUTH_LOGOUT_PATH,
    FORMAT_JAVASCRIPT,
    SCHOOL_SEARCH_PATH,
)
from .http_util import (
    RESPONSE_NOT_AVAILABLE,
    RESPONSE_SERVER_ERROR,
    RESPONSE_SESSION_STALE,
    build_page_url,
    classify_response,
    normalise_base_url,
    refusal_message,
    strip_json_prefix,
    strip_route_prefix,
)
from .models import ArborSchool

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)

# Arbor's own client posts a JSON body under jQuery's default content type.
# Mirroring that exactly avoids depending on how the PHP side sniffs the body.
_JQUERY_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 HomeAssistant-Arbor"
)


class ArborError(Exception):
    """Base error for the Arbor client."""


class ArborConnectionError(ArborError):
    """Arbor could not be reached, or answered in a way we cannot parse."""


class ArborAuthError(ArborError):
    """Arbor rejected the supplied credentials, or the session went away."""


class ArborNoSchoolsError(ArborAuthError):
    """The email address is not associated with any Arbor tenant."""


class ArborNotAvailableError(ArborError):
    """Arbor will not serve this page or endpoint to this account.

    Distinct from :class:`ArborAuthError`: the credentials are good, this
    particular resource is simply not on offer. Callers skip it.
    """


class ArborClient:
    """Authenticated session against one Arbor school tenant."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        base_url: str | None = None,
    ) -> None:
        """Store credentials; no network access happens here."""
        self._session = session
        self._email = email
        self._password = password
        self._base_url = normalise_base_url(base_url) if base_url else None
        self._logged_in = False
        self._login_lock = asyncio.Lock()

    @property
    def base_url(self) -> str | None:
        """Origin of the school tenant, once known."""
        return self._base_url

    @property
    def logged_in(self) -> bool:
        """Whether we believe the session cookie is still good."""
        return self._logged_in

    # -- school discovery ----------------------------------------------------

    async def async_list_schools(self) -> list[ArborSchool]:
        """Ask Arbor which tenants this email and password belong to.

        This is the same ``search-by-email`` call the central login page makes,
        and it validates the credentials as a side effect: wrong details come
        back as an empty payload.
        """
        payload = json.dumps({"email": self._email, "password": self._password})
        url = f"{ARBOR_LOGIN_HOST}{SCHOOL_SEARCH_PATH}"
        try:
            async with self._session.post(
                url,
                data=payload,
                headers={"Content-Type": _JQUERY_CONTENT_TYPE, "User-Agent": _USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 429:
                    raise ArborConnectionError(
                        "Arbor rate-limited the login request; try again in a few minutes"
                    )
                body = await response.text()
                if response.status >= 500:
                    raise ArborConnectionError(
                        f"Arbor login service returned HTTP {response.status}"
                    )
        except TimeoutError as err:
            raise ArborConnectionError("Timed out contacting the Arbor login service") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Cannot reach the Arbor login service: {err}") from err

        try:
            data = json.loads(strip_json_prefix(body))
        except ValueError as err:
            raise ArborConnectionError(
                "Arbor login service did not return JSON; the portal may be down"
            ) from err

        entries = data.get("payload") if isinstance(data, dict) else None
        if not entries:
            raise ArborNoSchoolsError(
                "Arbor did not return a school for this email and password"
            )

        schools: list[ArborSchool] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            sis_url = entry.get("sisUrl") or entry.get("sis_url")
            if not sis_url:
                continue
            try:
                base_url = normalise_base_url(str(sis_url))
            except ValueError:
                continue
            location = entry.get("postalCode") or entry.get("location") or None
            if isinstance(location, str):
                location = location.lstrip(", ").strip() or None
            schools.append(
                ArborSchool(
                    name=str(entry.get("name") or entry.get("shortName") or base_url),
                    base_url=base_url,
                    short_name=entry.get("shortName") or None,
                    location=location,
                    application_id=entry.get("applicationId") or None,
                )
            )

        if not schools:
            raise ArborNoSchoolsError("Arbor returned schools without a usable URL")
        return schools

    # -- authentication -----------------------------------------------------

    async def async_login(self) -> None:
        """Establish a portal session cookie for the configured school."""
        async with self._login_lock:
            if self._logged_in:
                # Another caller logged in while we waited for the lock.
                return
            await self._login_locked()

    async def _login_locked(self) -> None:
        if self._base_url is None:
            schools = await self.async_list_schools()
            if len(schools) > 1:
                raise ArborAuthError(
                    "This account has more than one Arbor school; pick one during setup"
                )
            self._base_url = schools[0].base_url

        self._logged_in = False
        payload = json.dumps({"items": [{"username": self._email, "password": self._password}]})
        url = f"{self._base_url}{AUTH_LOGIN_PATH}?lang=en"
        try:
            async with self._session.post(
                url,
                data=payload,
                headers={"Content-Type": _JQUERY_CONTENT_TYPE, "User-Agent": _USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                body = await response.text()
                status = response.status
        except TimeoutError as err:
            raise ArborConnectionError("Timed out logging in to Arbor") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Cannot reach {self._base_url}: {err}") from err

        if status == 429:
            raise ArborConnectionError("Arbor rate-limited the login; try again later")

        try:
            data = json.loads(strip_json_prefix(body))
        except ValueError as err:
            raise ArborConnectionError(
                f"Arbor login returned a non-JSON response (HTTP {status})"
            ) from err

        items = data.get("items") if isinstance(data, dict) else None
        first = items[0] if isinstance(items, list) and items else {}
        if not data.get("success") or not (
            isinstance(first, dict) and first.get("logged_in") is True
        ):
            raise ArborAuthError("Arbor rejected the email address or password")

        session_id = first.get("session_id")
        if not session_id:
            raise ArborAuthError("Arbor logged in but returned no session id")

        # The portal only becomes usable once this redirect has exchanged the
        # session id for the `mis` cookie.
        handshake = f"{self._base_url}/?session={quote(str(session_id))}&lang=en"
        try:
            async with self._session.get(
                handshake,
                headers={"User-Agent": _USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            ) as response:
                await response.read()
        except TimeoutError as err:
            raise ArborConnectionError("Timed out opening the Arbor session") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Could not open the Arbor session: {err}") from err

        if not self._has_session_cookie():
            raise ArborAuthError("Arbor did not issue a session cookie")

        self._logged_in = True
        _LOGGER.debug("Logged in to Arbor at %s", self._base_url)

    def _has_session_cookie(self) -> bool:
        """Whether the cookie jar holds a session cookie for this tenant."""
        if self._base_url is None:
            return False
        cookies = self._session.cookie_jar.filter_cookies(URL(self._base_url))
        return any(name in cookies for name in ("mis", "PHPSESSID", "arbor_session"))

    async def async_logout(self) -> None:
        """Best-effort invalidation of the portal session."""
        if not self._logged_in or self._base_url is None:
            return
        self._logged_in = False
        try:
            async with self._session.get(
                f"{self._base_url}{AUTH_LOGOUT_PATH}",
                headers={"User-Agent": _USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                await response.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("Ignoring error while logging out of Arbor: %s", err)

    # -- fetching -----------------------------------------------------------

    def page_url(self, path: str) -> str:
        """Build the JSON URL for a portal page path such as ``/guardians/...``."""
        if self._base_url is None:
            raise ArborError("No Arbor school selected yet")
        return build_page_url(self._base_url, path, FORMAT_JAVASCRIPT)

    def endpoint_url(self, path: str) -> str:
        """Build the URL for a direct ``/format/json`` style endpoint."""
        if self._base_url is None:
            raise ArborError("No Arbor school selected yet")
        route = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{route}"

    async def async_fetch_page(self, path: str) -> Any:
        """Fetch a portal page as its JSON component tree."""
        return await self._fetch(self.page_url(path), description=f"page {path}")

    async def async_fetch_json(self, path: str) -> Any:
        """Fetch a direct JSON endpoint."""
        return await self._fetch(self.endpoint_url(path), description=f"endpoint {path}")

    async def async_fetch_absolute(self, url: str) -> Any:
        """Fetch a JSON tree from a URL Arbor itself handed us.

        Only same-tenant URLs are followed; anything else is refused so a value
        in a scraped page can never redirect us off the school's own domain.
        """
        if self._base_url is None:
            raise ArborError("No Arbor school selected yet")
        candidate = strip_route_prefix(url.strip())
        if candidate.startswith("//"):
            # Protocol-relative: not ours, and not a portal route either.
            raise ArborError(f"Refusing to follow protocol-relative URL: {url}")
        if candidate.startswith("/"):
            return await self.async_fetch_page(candidate)
        if not candidate.startswith(f"{self._base_url}/"):
            raise ArborError(f"Refusing to follow off-tenant URL: {url}")
        return await self.async_fetch_page(candidate[len(self._base_url) :])

    async def _fetch(self, url: str, *, description: str, _retried: bool = False) -> Any:
        """GET a URL, re-authenticating once if the session has expired."""
        if not self._logged_in:
            await self.async_login()

        try:
            async with self._session.get(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                status = response.status
                body = await response.text()
        except TimeoutError as err:
            raise ArborConnectionError(f"Timed out fetching {description}") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Error fetching {description}: {err}") from err

        verdict = classify_response(status, body, retried=_retried)

        if verdict == RESPONSE_SESSION_STALE:
            _LOGGER.debug("Arbor session looks stale for %s, logging in again", description)
            self._logged_in = False
            await self.async_login()
            return await self._fetch(url, description=description, _retried=True)

        if verdict == RESPONSE_NOT_AVAILABLE:
            raise ArborNotAvailableError(
                f"Arbor will not serve {description} to this account (HTTP {status})"
            )

        if verdict == RESPONSE_SERVER_ERROR:
            raise ArborConnectionError(f"Arbor returned HTTP {status} for {description}")

        if not body.strip():
            return None
        try:
            payload = json.loads(strip_json_prefix(body))
        except ValueError as err:
            raise ArborConnectionError(
                f"Arbor returned unparseable JSON for {description}"
            ) from err

        if (refusal := refusal_message(payload)) is not None:
            raise ArborNotAvailableError(f"Arbor refused {description}: {refusal}")

        return payload
