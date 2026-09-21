"""The Arbor login protocol, independent of any HTTP library.

Each step is split into a request *description* and a response *parser*, so the
same protocol knowledge serves Home Assistant's aiohttp client and the
standard-library script in ``tools/arbor_probe.py``. Without this split the login
sequence would exist twice and drift.

See docs/PROTOCOL.md for where each step came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .const import ARBOR_LOGIN_HOST, AUTH_LOGIN_PATH, SCHOOL_SEARCH_PATH
from .errors import (
    ArborConnectionError,
    ArborAuthError,
    ArborNoSchoolsError,
)
from .http_util import normalise_base_url, strip_json_prefix
from .models import ArborSchool

# Arbor's own login page posts a JSON body under jQuery's default content type.
# Mirroring that exactly avoids depending on how the PHP side sniffs the body.
JQUERY_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 HomeAssistant-Arbor"
)

#: Cookie names that mean a portal session has been established.
SESSION_COOKIE_NAMES = ("mis", "PHPSESSID", "arbor_session")

# The browser posts both login steps from the central login page, so it sends an
# Origin and a Referer. Ours sent neither. These are what the real client sends,
# not an attempt to look like something we are not.
LOGIN_HEADERS = {
    "Content-Type": JQUERY_CONTENT_TYPE,
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": ARBOR_LOGIN_HOST,
    "Referer": f"{ARBOR_LOGIN_HOST}/",
}

PAGE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass(slots=True)
class HttpRequest:
    """Everything needed to make one request, whatever the client."""

    method: str
    url: str
    body: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def school_search_request(email: str, password: str) -> HttpRequest:
    """Step 1: ask Arbor which tenants these credentials belong to."""
    return HttpRequest(
        method="POST",
        url=f"{ARBOR_LOGIN_HOST}{SCHOOL_SEARCH_PATH}",
        body=json.dumps({"email": email, "password": password}),
        headers=dict(LOGIN_HEADERS),
    )


def parse_school_search(body: str) -> list[ArborSchool]:
    """Read the school list, treating an empty payload as bad credentials."""
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


def login_request(base_url: str, email: str, password: str) -> HttpRequest:
    """Step 2: log in to one school tenant."""
    return HttpRequest(
        method="POST",
        url=f"{base_url}{AUTH_LOGIN_PATH}?lang=en",
        body=json.dumps({"items": [{"username": email, "password": password}]}),
        headers=dict(LOGIN_HEADERS),
    )


def parse_login(body: str, status: int) -> str:
    """Read the session id from a login response.

    The only place an :class:`ArborAuthError` may originate: Arbor either says
    the credentials are not valid, or it does not. Everything else that can go
    wrong here happens *after* it has accepted them, so it is a malfunction to
    retry rather than a password to re-enter.
    """
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
        raise ArborAuthError(login_rejection_reason(data))

    session_id = first.get("session_id")
    if not session_id:
        raise ArborConnectionError("Arbor logged in but returned no session id")
    return str(session_id)


def login_rejection_reason(data: Any) -> str:
    """Why Arbor turned a login down, in its own words where it gives them.

    Arbor answers a locked or throttled account the same way it answers a wrong
    password -- ``success: false`` -- but it often explains itself in
    ``login_form_message``, and disables the form outright when an account is
    locked. Discarding that turned "your account is locked" into "check your
    password", which is the opposite of the right advice.
    """
    items = data.get("items") if isinstance(data, dict) else None
    first = items[0] if isinstance(items, list) and items else {}
    if not isinstance(first, dict):
        first = {}

    for candidate in (
        first.get("login_form_message"),
        first.get("message"),
        (data.get("action_params") or {}).get("message")
        if isinstance(data, dict) and isinstance(data.get("action_params"), dict)
        else None,
        data.get("message") if isinstance(data, dict) else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return f"Arbor refused the login: {candidate.strip()}"

    if first.get("login_form_enabled") is False:
        return (
            "Arbor has disabled the login form for this account, which usually means "
            "it is locked after too many attempts. Reset the password from "
            "login.arbor.sc rather than retrying"
        )

    return "Arbor rejected the email address or password"


#: Login-response fields that are a secret rather than a diagnosis.
SECRET_LOGIN_FIELDS = ("session_id", "jwt", "token", "stripePublishableKey")


def redact_login_response(data: Any) -> Any:
    """A login response with its secrets removed but its reasons intact.

    The reason a login was refused lives in strings such as
    ``login_form_message``, which are not personal data, so a response can be
    shared for diagnosis once the session id and any token are taken out.
    """
    if isinstance(data, dict):
        return {
            key: "<redacted>"
            if key in SECRET_LOGIN_FIELDS and value
            else redact_login_response(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_login_response(item) for item in data]
    return data


def session_handshake_url(base_url: str, session_id: str) -> str:
    """Step 3: the redirect that exchanges a session id for the session cookie.

    Note this is a genuine query parameter, not the ``/?/route`` page form.
    """
    return f"{base_url}/?session={quote(session_id)}&lang=en"
