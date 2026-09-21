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


def build_page_url(base_url: str, path: str, format_flag: str) -> str:
    """URL that returns a portal page as its JSON component tree.

    Portal routes live in the query string, e.g.
    ``https://school.uk.arbor.sc/?/guardians/home-ui/dashboard``.
    """
    route = path if path.startswith("/") else f"/{path}"
    if route.startswith("/?"):
        route = route[2:]
    if format_flag in route:
        return f"{base_url}/?{route}"
    return f"{base_url}/?{route}&{format_flag}"
