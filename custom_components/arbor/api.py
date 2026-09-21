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

import aiohttp
from yarl import URL

from . import protocol
from .const import AUTH_LOGOUT_PATH, FORMAT_JAVASCRIPT
from .errors import (
    ArborAuthError,
    ArborConfigurationError,
    ArborConnectionError,
    ArborError,
    ArborNoSchoolsError,
    ArborNotAvailableError,
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

__all__ = [
    "ArborAuthError",
    "ArborClient",
    "ArborConnectionError",
    "ArborError",
    "ArborNoSchoolsError",
    "ArborNotAvailableError",
]

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)




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
        request = protocol.school_search_request(self._email, self._password)
        status, body = await self._post(request, "the Arbor login service")
        if status == 429:
            raise ArborConnectionError(
                "Arbor rate-limited the login request; try again in a few minutes"
            )
        if status >= 500:
            raise ArborConnectionError(f"Arbor login service returned HTTP {status}")
        return protocol.parse_school_search(body)

    async def _post(self, request: protocol.HttpRequest, what: str) -> tuple[int, str]:
        """Make one protocol request and return its status and body."""
        try:
            async with self._session.post(
                request.url,
                data=request.body,
                headers=request.headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                return response.status, await response.text()
        except TimeoutError as err:
            raise ArborConnectionError(f"Timed out contacting {what}") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Cannot reach {what}: {err}") from err

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
                raise ArborConfigurationError(
                    "This account has more than one Arbor school; pick one during setup"
                )
            self._base_url = schools[0].base_url

        self._logged_in = False
        request = protocol.login_request(self._base_url, self._email, self._password)
        status, body = await self._post(request, self._base_url)
        session_id = protocol.parse_login(body, status)

        # The portal only becomes usable once this redirect has exchanged the
        # session id for the `mis` cookie.
        handshake = protocol.session_handshake_url(self._base_url, session_id)
        try:
            async with self._session.get(
                handshake,
                headers={"User-Agent": protocol.USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            ) as response:
                await response.read()
        except TimeoutError as err:
            raise ArborConnectionError("Timed out opening the Arbor session") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Could not open the Arbor session: {err}") from err

        if not self._has_session_cookie():
            # The credentials were accepted; the handshake just did not leave us
            # with a usable cookie. Worth retrying, not reporting.
            raise ArborConnectionError("Arbor did not issue a session cookie")

        self._logged_in = True
        _LOGGER.debug("Logged in to Arbor at %s", self._base_url)

    def _has_session_cookie(self) -> bool:
        """Whether the cookie jar holds a session cookie for this tenant."""
        if self._base_url is None:
            return False
        cookies = self._session.cookie_jar.filter_cookies(URL(self._base_url))
        return any(name in cookies for name in protocol.SESSION_COOKIE_NAMES)

    async def async_logout(self) -> None:
        """Best-effort invalidation of the portal session."""
        if not self._logged_in or self._base_url is None:
            return
        self._logged_in = False
        try:
            async with self._session.get(
                f"{self._base_url}{AUTH_LOGOUT_PATH}",
                headers={"User-Agent": protocol.USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                await response.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("Ignoring error while logging out of Arbor: %s", err)

    # -- fetching -----------------------------------------------------------

    def page_url(self, path: str) -> str:
        """Build the JSON URL for a portal page path such as ``/guardians/...``."""
        if self._base_url is None:
            raise ArborConfigurationError("No Arbor school selected yet")
        return build_page_url(self._base_url, path, FORMAT_JAVASCRIPT)

    def endpoint_url(self, path: str) -> str:
        """Build the URL for a direct ``/format/json`` style endpoint."""
        if self._base_url is None:
            raise ArborConfigurationError("No Arbor school selected yet")
        route = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{route}"

    async def async_fetch_page(self, path: str) -> Any:
        """Fetch a portal page as its JSON component tree."""
        return await self._fetch(self.page_url(path), description=f"page {path}")

    async def async_fetch_json(self, path: str) -> Any:
        """Fetch a direct JSON endpoint."""
        return await self._fetch(self.endpoint_url(path), description=f"endpoint {path}")

    async def async_post_json(self, path: str, body: str) -> Any:
        """POST a JSON body to an endpoint and return its parsed response."""
        if self._base_url is None:
            raise ArborConfigurationError("No Arbor school selected yet")
        if not self._logged_in:
            await self.async_login()
        url = f"{self._base_url}{path if path.startswith('/') else '/' + path}"
        headers = {**protocol.PAGE_HEADERS, "Content-Type": "application/json"}
        try:
            async with self._session.post(
                url, data=body, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                status = response.status
                text = await response.text()
        except TimeoutError as err:
            raise ArborConnectionError(f"Timed out posting to {path}") from err
        except aiohttp.ClientError as err:
            raise ArborConnectionError(f"Error posting to {path}: {err}") from err

        verdict = classify_response(status, text, retried=True)
        if verdict == RESPONSE_NOT_AVAILABLE:
            raise ArborNotAvailableError(
                f"Arbor will not accept {path} from this account (HTTP {status})"
            )
        if verdict == RESPONSE_SERVER_ERROR:
            raise ArborConnectionError(f"Arbor returned HTTP {status} for {path}")
        try:
            payload = json.loads(strip_json_prefix(text))
        except ValueError as err:
            raise ArborConnectionError(f"Unparseable JSON from {path}") from err
        if (refusal := refusal_message(payload)) is not None:
            raise ArborNotAvailableError(f"Arbor refused {path}: {refusal}")
        return payload

    async def async_fetch_absolute(self, url: str) -> Any:
        """Fetch a JSON tree from a URL Arbor itself handed us.

        Only same-tenant URLs are followed; anything else is refused so a value
        in a scraped page can never redirect us off the school's own domain.
        """
        if self._base_url is None:
            raise ArborConfigurationError("No Arbor school selected yet")
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
                headers=protocol.PAGE_HEADERS,
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
