"""Dependency-free helpers for talking to Arbor over HTTP.

Kept apart from ``api.py`` so the pure string handling can be exercised without
aiohttp present.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Prefixes some servers use to make a JSON response non-executable as a script.
JSON_HIJACK_PREFIXES = ("while(1);", "for(;;);", ")]}'", "/**/")


def strip_json_prefix(text: str) -> str:
    """Remove any anti-JSON-hijacking prefix from a response body."""
    stripped = text.lstrip()
    for prefix in JSON_HIJACK_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].lstrip()
    return stripped


def looks_like_html(text: str) -> bool:
    """Whether a body is the HTML application shell rather than JSON.

    Arbor answers an unauthenticated request by serving the portal shell with a
    200, so the status code alone cannot tell us the session has gone.
    """
    head = text.lstrip()[:200].casefold()
    return head.startswith("<!doctype") or head.startswith("<html")


# Arbor answers an expired session on a ``/format/json`` endpoint with a
# perfectly well-formed ``{"success": true, "items": [{"logged_in": false, ...}]}``
# and a 200, so neither the status code nor the HTML check notices. The first
# endpoint of a refresh is the one that gets hit, which silently cost the school
# and guardian names every time a session timed out between updates.
_LOGGED_OUT_RE = re.compile(r'"logged_in"\s*:\s*false', re.IGNORECASE)


def says_logged_out(text: str) -> bool:
    """Whether a JSON body states that the session is not authenticated."""
    return bool(_LOGGED_OUT_RE.search(text))


# Path segments and file extensions that mean a URL serves a *file* rather than a
# page of data. The Attendance By Date page links a PDF certificate at
# /guardians/student/download-attendance-certificate/...; following it fetched a
# PDF, and decoding a PDF as text ended the whole refresh with a
# UnicodeDecodeError rather than skipping one page.
_DOWNLOAD_SEGMENT_PREFIXES = ("download", "export")
_DOWNLOAD_EXTENSIONS = (
    ".pdf",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".ics",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".xls",
    ".xlsx",
    ".zip",
)


def is_download_url(url: str) -> bool:
    """Whether a URL serves a file rather than a page of data."""
    path = url.split("?", 1)[0].casefold()
    if path.endswith(_DOWNLOAD_EXTENSIONS):
        return True
    return any(
        segment.startswith(_DOWNLOAD_SEGMENT_PREFIXES) for segment in path.split("/") if segment
    )


#: Content types a portal page or endpoint can legitimately arrive as.
_TEXTUAL_CONTENT_TYPE_TOKENS = ("json", "javascript", "text", "xml", "html")


def is_textual_content_type(content_type: str | None) -> bool:
    """Whether a body is worth trying to read as text at all.

    Arbor does not always state a type, and a missing one is not evidence of a
    binary body, so the benefit of the doubt goes to reading it.
    """
    if not content_type:
        return True
    lowered = content_type.casefold()
    return any(token in lowered for token in _TEXTUAL_CONTENT_TYPE_TOKENS)


def decode_body(raw: bytes, charset: str | None = None) -> str:
    """Decode a response body without letting one bad byte end a refresh.

    A substituted character makes the JSON unparseable, which is reported as one
    unreadable page; a raised ``UnicodeDecodeError`` took down every entity.
    """
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        # An encoding name the runtime does not know.
        return raw.decode("utf-8", errors="replace")


def normalise_base_url(raw: str) -> str:
    """Turn a school URL or bare host into a scheme-qualified origin."""
    candidate = raw.strip().rstrip("/")
    if not candidate:
        raise ValueError("empty school URL")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.netloc:
        raise ValueError(f"cannot parse school URL: {raw!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def strip_route_prefix(path: str) -> str:
    """Reduce a portal URL or address-bar route to its plain path.

    Mirrors Arbor's own loader, which drops everything up to and including a
    ``?`` that is immediately followed by ``/``. So the address-bar form
    ``https://school.uk.arbor.sc/?/guardians/home-ui/dashboard`` becomes
    ``/guardians/home-ui/dashboard``, while a genuine query string such as
    ``/guardians/x?page=2`` is left alone.
    """
    index = path.find("?")
    if index != -1 and path[index + 1 : index + 2] == "/":
        return path[index + 1 :]
    return path


def build_page_url(base_url: str, path: str, format_flag: str) -> str:
    """URL that returns a portal page as its JSON component tree.

    Portal routes are fetched as ordinary paths with a normal query string. The
    ``/?/route`` form is only how the single-page app represents the current
    route in the address bar; requesting that form returns the HTML application
    shell instead of data.
    """
    route = strip_route_prefix(path.strip())
    if not route.startswith("/"):
        route = f"/{route}"
    if format_flag in route:
        return f"{base_url}{route}"
    separator = "&" if "?" in route else "?"
    return f"{base_url}{route}{separator}{format_flag}"


# How a response to an authenticated page request should be treated.
RESPONSE_OK = "ok"
#: The session looks dead; log in again and retry once.
RESPONSE_SESSION_STALE = "session_stale"
#: Arbor will not serve this to this account; skip it and carry on.
RESPONSE_NOT_AVAILABLE = "not_available"
#: Arbor itself is unhappy; worth retrying on a later refresh.
RESPONSE_SERVER_ERROR = "server_error"


def classify_response(status: int, body: str, *, retried: bool) -> str:
    """Decide what a page response means.

    The important distinction is between a dead *session* and a resource this
    account may not see. They look identical on the wire -- both a 403 and the
    HTML shell -- but only the first is worth re-authenticating for. A dead
    session also has a third disguise: a 200 whose JSON says ``logged_in: false``.

    Logging in is what validates credentials: it returns ``logged_in: true`` and
    a session cookie or it fails outright. So once a fresh login has happened,
    a denial can only be about the resource, never the password. Treating it as
    an authentication failure takes the whole integration down and asks the user
    to re-enter a password that was never wrong.
    """
    if status in (401, 403) or looks_like_html(body) or says_logged_out(body):
        return RESPONSE_SESSION_STALE if not retried else RESPONSE_NOT_AVAILABLE
    if status == 404:
        return RESPONSE_NOT_AVAILABLE
    if status >= 400:
        return RESPONSE_SERVER_ERROR
    return RESPONSE_OK


def refusal_message(payload: object) -> str | None:
    """The reason Arbor gives for refusing a page, if it refused one.

    A page the account may not see comes back ``200`` with a JSON body of
    ``{"success": false, "message": "User is not allowed to access ..."}``
    rather than an error status.
    """
    if isinstance(payload, dict) and payload.get("success") is False:
        message = payload.get("message")
        return str(message) if message else "no reason given"
    return None
