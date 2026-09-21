"""Dependency-free helpers for talking to Arbor over HTTP.

Kept apart from ``api.py`` so the pure string handling can be exercised without
aiohttp present.
"""

from __future__ import annotations

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
