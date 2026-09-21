#!/usr/bin/env python3
"""Drive the Arbor integration's scraper from the command line.

Runs exactly the code Home Assistant runs -- the same login protocol, the same
page URLs, the same parser and the same orchestration -- against a real account,
so a change can be checked in a second instead of by restarting Home Assistant.
Only the HTTP transport differs: this uses the standard library, so there is
nothing to install.

Your password is never taken as an argument (it would land in your shell
history) and never written anywhere. It is read from the ARBOR_PASSWORD
environment variable if set, then from the macOS Keychain, and only then
prompted for. To store it once so no run ever has to ask again:

    security add-generic-password -a you@example.com -s arbor-probe -w

That prompts for the password without echo and keeps it in your Keychain; this
script only ever reads it. For a single terminal session instead:

    read -rs ARBOR_PASSWORD && export ARBOR_PASSWORD

    export ARBOR_SCHOOL=wrotham          # when the account has several schools
    python3 tools/arbor_probe.py report --email you@example.com
    python3 tools/arbor_probe.py shape /guardians/student-ui/assignments/student-id/12345 \
        --email you@example.com
    python3 tools/arbor_probe.py shapes --email you@example.com > shapes.txt

Output is redacted by default: names, comments and other free text are replaced
with a type-and-length placeholder so the result can be pasted into an issue.
Pass --show-values to see the real thing on your own screen.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import gzip
import http.client
import http.cookiejar
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from collections.abc import Callable
from typing import Any

# Import the integration's own modules without pulling in Home Assistant.
# `arbor/__init__.py` imports homeassistant, as every custom component's does, so
# the modules are loaded by path under a synthetic package instead. Everything
# loaded this way is standard-library only; nothing needs installing.
import importlib.util  # noqa: E402
import types  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_PKG_DIR = _ROOT / "custom_components" / "arbor"
_PKG_NAME = "arbor_probe_pkg"

if not _PKG_DIR.is_dir():
    raise SystemExit(f"cannot find the integration at {_PKG_DIR}")

_package = types.ModuleType(_PKG_NAME)
_package.__path__ = [str(_PKG_DIR)]
sys.modules[_PKG_NAME] = _package


def _load(name: str) -> types.ModuleType:
    """Load one integration module by file path."""
    full_name = f"{_PKG_NAME}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, _PKG_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


const = _load("const")
errors = _load("errors")
http_util = _load("http_util")
protocol = _load("protocol")
parser = _load("parser")
scraper = _load("scraper")

CURRENT_USER_SETTINGS_PATH = const.CURRENT_USER_SETTINGS_PATH
FORMAT_JAVASCRIPT = const.FORMAT_JAVASCRIPT
GUARDIAN_DASHBOARD_PAGE = const.GUARDIAN_DASHBOARD_PAGE

ArborAuthError = errors.ArborAuthError
ArborConnectionError = errors.ArborConnectionError
ArborError = errors.ArborError
ArborNotAvailableError = errors.ArborNotAvailableError
ArborConfigurationError = errors.ArborConfigurationError

RESPONSE_NOT_AVAILABLE = http_util.RESPONSE_NOT_AVAILABLE
RESPONSE_SERVER_ERROR = http_util.RESPONSE_SERVER_ERROR
RESPONSE_SESSION_STALE = http_util.RESPONSE_SESSION_STALE
build_page_url = http_util.build_page_url
classify_response = http_util.classify_response
refusal_message = http_util.refusal_message
strip_json_prefix = http_util.strip_json_prefix
strip_route_prefix = http_util.strip_route_prefix
normalise_base_url = http_util.normalise_base_url

describe_shape = parser.describe_shape
ArborScraper = scraper.ArborScraper

_LOGGER = logging.getLogger("arbor_probe")
TIMEOUT = 45

# Arbor nests a guardian page deeply -- page, column, section, subsection, row,
# then the field object -- so a shallow dump truncates exactly where the data is.
DEFAULT_SHAPE_DEPTH = 18


class UrllibArborClient:
    """The integration's ArborClient, over urllib instead of aiohttp.

    Deliberately mirrors ``api.ArborClient``: same protocol calls, same URL
    building, same response classification. Anything shared lives in the
    integration's modules so the two cannot disagree about what Arbor said.
    """

    def __init__(
        self,
        email: str,
        password: str | Callable[[], str],
        school: str | None = None,
        *,
        reuse_session: bool = True,
    ) -> None:
        self._email = email
        # Resolved only if a login is actually needed. With a saved session and a
        # remembered school, a run needs no credential at all -- which matters
        # wherever the Keychain cannot be reached, and means fewer logins.
        self._password_source = password
        # A school may be given as a URL, which needs no lookup, or as a name
        # fragment, which is resolved against the account's schools at login.
        self._base_url: str | None = None
        self._school_selector: str | None = None
        if school and (selector := school.strip()):
            if "." in selector or "://" in selector:
                self._base_url = normalise_base_url(selector)
            else:
                self._school_selector = selector
        self._logged_in = False
        self._jar = http.cookiejar.LWPCookieJar(str(_cookie_path()))
        if reuse_session:
            try:
                self._jar.load(ignore_discard=True, ignore_expires=True)
            except (OSError, http.cookiejar.LoadError):
                pass
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )

    @property
    def base_url(self) -> str | None:
        return self._base_url

    @property
    def email(self) -> str:
        return self._email

    @property
    def password(self) -> str:
        """The password, resolved on first use."""
        if callable(self._password_source):
            resolved = self._password_source()
            self._password_source = resolved
        return self._password_source

    def request_raw(self, request: Any) -> tuple[int, str]:
        """Make one protocol request and return its status and body verbatim."""
        return self._request(
            request.method, request.url, request.body, request.headers
        )

    @property
    def cookies(self) -> list[Any]:
        """Cookies held, for diagnosis. Names and domains only are ever shown."""
        return list(self._jar)

    # -- transport ----------------------------------------------------------

    def _request(
        self, method: str, url: str, body: str | None, headers: dict[str, str]
    ) -> tuple[int, str]:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8") if body is not None else None,
            headers={**headers, "Accept-Encoding": "gzip, deflate"},
            method=method,
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT) as response:
                return response.status, _decode(response)
        except urllib.error.HTTPError as err:
            return err.code, _decode(err)
        except urllib.error.URLError as err:
            raise ArborConnectionError(f"Cannot reach {url}: {err.reason}") from err
        except TimeoutError as err:
            raise ArborConnectionError(f"Timed out fetching {url}") from err
        except http.client.HTTPException as err:
            # RemoteDisconnected and friends are not URLErrors, so they escaped
            # as a raw traceback instead of a reportable failure.
            raise ArborConnectionError(
                f"Connection to {url} failed: {type(err).__name__}: {err}"
            ) from err
        except OSError as err:
            raise ArborConnectionError(f"Network error reaching {url}: {err}") from err

    # -- login --------------------------------------------------------------

    def list_schools(self) -> list[Any]:
        request = protocol.school_search_request(self._email, self.password)
        status, body = self._request(
            request.method, request.url, request.body, request.headers
        )
        if status == 429:
            raise ArborConnectionError("Arbor rate-limited the login; try again later")
        if status >= 500:
            raise ArborConnectionError(f"Arbor login service returned HTTP {status}")
        return protocol.parse_school_search(body)

    def resolve_school(self, schools: list[Any] | None = None) -> str:
        """The tenant to use, honouring --school and the remembered choice."""
        return self._resolve_school(schools)

    def _resolve_school(self, schools: list[Any] | None = None) -> str:
        """Work out which tenant to use.

        An explicit --school wins; otherwise the choice remembered from a previous
        successful run is used, which also saves a lookup request.
        """
        if self._school_selector is None:
            if (remembered := remembered_school(self._email)) is not None:
                print(
                    f"school:   {remembered} (remembered; --school to change)",
                    file=sys.stderr,
                )
                return remembered

        if schools is None:
            schools = self.list_schools()

        if self._school_selector:
            needle = self._school_selector.casefold()
            matches = [
                school
                for school in schools
                if needle in school.base_url.casefold()
                or needle in (school.name or "").casefold()
                or needle in (school.short_name or "").casefold()
            ]
            if len(matches) == 1:
                print(f"school:   {matches[0].label}", file=sys.stderr)
                return matches[0].base_url
            if not matches:
                raise ArborConfigurationError(
                    f"No school matches {self._school_selector!r}.\n\n"
                    + _school_listing(schools)
                )
            raise ArborConfigurationError(
                f"{self._school_selector!r} matches {len(matches)} schools.\n\n"
                + _school_listing(matches)
            )

        if len(schools) == 1:
            print(f"school:   {schools[0].label}", file=sys.stderr)
            return schools[0].base_url

        chosen = _choose_school(schools)
        print(f"school:   {chosen.label}", file=sys.stderr)
        return chosen.base_url

    def login(self) -> None:
        if self._base_url is None:
            self._base_url = self._resolve_school()

        request = protocol.login_request(self._base_url, self._email, self.password)
        status, body = self._request(
            request.method, request.url, request.body, request.headers
        )
        session_id = protocol.parse_login(body, status)
        self._request(
            "GET",
            protocol.session_handshake_url(self._base_url, session_id),
            None,
            {"User-Agent": protocol.USER_AGENT},
        )
        if not self._has_session_cookie():
            raise ArborConnectionError("Arbor did not issue a session cookie")
        self._logged_in = True
        remember_school(self._email, self._base_url)
        self._save_session()

    def _save_session(self) -> None:
        """Persist the session cookie so the next run need not log in again."""
        path = _cookie_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._jar.save(ignore_discard=True, ignore_expires=True)
            path.chmod(0o600)
        except OSError as err:
            _LOGGER.debug("Could not save the session: %s", err)

    def _has_session_cookie(self) -> bool:
        """Whether the jar holds a session cookie for *this tenant*.

        The jar also holds cookies from login.arbor.sc, set while looking the
        school up. Accepting one of those would report a good session when the
        school itself never issued one -- which is precisely the failure this
        check exists to catch.
        """
        if self._base_url is None:
            return False
        host = urllib.parse.urlsplit(self._base_url).hostname or ""
        for cookie in self._jar:
            if cookie.name not in protocol.SESSION_COOKIE_NAMES:
                continue
            domain = (cookie.domain or "").lstrip(".")
            if domain and (host == domain or host.endswith(f".{domain}")):
                return True
        return False

    # -- fetching -----------------------------------------------------------

    def _fetch(self, url: str, description: str, retried: bool = False) -> Any:
        if not self._logged_in:
            self.login()
        status, body = self._request("GET", url, None, dict(protocol.PAGE_HEADERS))
        verdict = classify_response(status, body, retried=retried)

        if verdict == RESPONSE_SESSION_STALE:
            if retried:
                raise ArborNotAvailableError(f"{description}: session refused")
            # The saved session may simply have expired; log in once and retry.
            self._logged_in = False
            self.login()
            return self._fetch(url, description, retried=True)
        if verdict == RESPONSE_NOT_AVAILABLE:
            raise ArborNotAvailableError(
                f"Arbor will not serve {description} to this account (HTTP {status})"
            )
        if verdict == RESPONSE_SERVER_ERROR:
            raise ArborConnectionError(f"HTTP {status} for {description}")

        if not body.strip():
            return None
        try:
            payload = json.loads(strip_json_prefix(body))
        except ValueError as err:
            raise ArborConnectionError(f"Unparseable JSON for {description}") from err
        if (refusal := refusal_message(payload)) is not None:
            raise ArborNotAvailableError(f"Arbor refused {description}: {refusal}")
        return payload

    async def fetch_page(self, path: str) -> Any:
        """Fetch a portal page. Async so the scraper can use it unchanged."""
        base = self._ensure_logged_in()
        candidate = strip_route_prefix(path.strip())
        if candidate.startswith("//"):
            raise ArborError(f"Refusing protocol-relative URL: {path}")
        if not candidate.startswith("/"):
            if not candidate.startswith(f"{base}/"):
                raise ArborError(f"Refusing off-tenant URL: {path}")
            candidate = candidate[len(base) :]
        return self._fetch(
            build_page_url(base, candidate, FORMAT_JAVASCRIPT), f"page {path}"
        )

    async def fetch_json(self, path: str) -> Any:
        """Fetch a JSON endpoint. Async so the scraper can use it unchanged."""
        base = self._ensure_logged_in()
        return self._fetch(f"{base}{path}", f"endpoint {path}")

    async def post_json(self, path: str, body: str) -> Any:
        """POST a JSON body, for the endpoints that need one."""
        base = self._ensure_logged_in()
        status, text = self._request(
            "POST",
            f"{base}{path}",
            body,
            {**protocol.PAGE_HEADERS, "Content-Type": "application/json"},
        )
        verdict = classify_response(status, text, retried=True)
        if verdict != http_util.RESPONSE_OK:
            raise ArborNotAvailableError(f"POST {path}: HTTP {status}")
        payload = _safe_json(text)
        if payload is None:
            raise ArborConnectionError(f"Unparseable JSON from POST {path}")
        if (refusal := refusal_message(payload)) is not None:
            raise ArborNotAvailableError(f"Arbor refused POST {path}: {refusal}")
        return payload

    def _ensure_logged_in(self) -> str:
        """Reuse a saved session if there is one, otherwise log in."""
        if not self._logged_in:
            if self._base_url is None and self._school_selector is None:
                # A remembered school avoids the lookup request as well.
                remembered = remembered_school(self._email)
                if remembered is not None:
                    self._base_url = remembered
            if self._base_url is not None and self._has_session_cookie():
                _LOGGER.debug("Reusing the saved Arbor session")
                self._logged_in = True
            else:
                self.login()
        if self._base_url is None:
            raise ArborConfigurationError("No Arbor school selected")
        return self._base_url


DEFAULT_KEYCHAIN_SERVICE = "arbor-probe"


def keychain_password(email: str, service: str) -> str | None:
    """Read the password from the macOS Keychain, if it is stored there.

    The user puts it there themselves with `security add-generic-password`; this
    only reads it, never writes it, and never logs it. Any failure -- no
    Keychain, no entry, access declined -- simply means "not available".
    """
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                email,
                "-s",
                service,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    password = result.stdout.rstrip("\n")
    return password or None


def keychain_store_command(email: str, service: str) -> str:
    """The one-time command that puts the password in the Keychain."""
    return (
        f"security add-generic-password -a {email} -s {service} -w"
    )


def _cookie_path() -> Path:
    """Where the portal session is kept between runs.

    A session cookie is a bearer credential, so the file is created 0600. It is
    the same kind of thing a browser's cookie jar holds, and keeping it is what
    stops every invocation logging in again -- which is what trips Arbor's limit
    on logins.
    """
    return _config_path().with_name("cookies.txt")


def _config_path() -> Path:
    """Where the chosen school is remembered.

    Only the school URL is ever stored. The password is never written anywhere.
    """
    override = os.environ.get("ARBOR_PROBE_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "arbor-probe" / "schools.json"


def _read_config() -> dict[str, Any]:
    try:
        return json.loads(_config_path().read_text())
    except (OSError, ValueError):
        return {}


def remembered_school(email: str) -> str | None:
    """The school last used successfully for this email address."""
    schools = _read_config().get("schools")
    if isinstance(schools, dict):
        value = schools.get(email.casefold())
        if isinstance(value, str) and value:
            return value
    return None


def remember_school(email: str, base_url: str) -> None:
    """Record the school that worked, so the flag is needed once only."""
    config = _read_config()
    schools = config.setdefault("schools", {})
    if not isinstance(schools, dict):
        schools = config["schools"] = {}
    if schools.get(email.casefold()) == base_url:
        return
    schools[email.casefold()] = base_url
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2) + "\n")
    except OSError as err:
        _LOGGER.debug("Could not remember the school choice: %s", err)


def forget_schools() -> None:
    """Drop every remembered school choice."""
    try:
        _config_path().unlink()
        print(f"forgot remembered schools ({_config_path()})", file=sys.stderr)
    except FileNotFoundError:
        print("nothing was remembered", file=sys.stderr)
    except OSError as err:
        print(f"could not forget: {err}", file=sys.stderr)


def _choose_school(schools: list[Any]) -> Any:
    """Ask which school to use, when a terminal is there to ask."""
    if not sys.stdin.isatty():
        raise ArborConfigurationError(
            f"This account covers {len(schools)} schools, so pick one:\n\n"
            + _school_listing(schools)
            + "\n\nA name fragment works too, e.g. --school wrotham."
        )
    print(f"\nThis account covers {len(schools)} schools:", file=sys.stderr)
    for index, school in enumerate(schools, start=1):
        print(f"  {index}) {school.label}", file=sys.stderr)
    while True:
        try:
            answer = input("Which one? [1-%d] " % len(schools)).strip()
        except EOFError as err:
            raise ArborConfigurationError("No school chosen") from err
        if answer.isdigit() and 1 <= int(answer) <= len(schools):
            chosen = schools[int(answer) - 1]
            print(
                "Remembering that choice; pass --school to change it.", file=sys.stderr
            )
            return chosen
        print(f"Enter a number between 1 and {len(schools)}.", file=sys.stderr)


def _school_listing(schools: list[Any]) -> str:
    """Copy-pasteable list of schools and the flag to select each."""
    return "\n".join(
        f"  --school {school.base_url}    # {school.label}" for school in schools
    )


def _decode(response: Any) -> str:
    """Read a urllib response, handling gzip and deflate."""
    raw = response.read()
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def redact(value: Any, show_values: bool, depth: int = DEFAULT_SHAPE_DEPTH) -> Any:
    """Replace free text with a type-and-length placeholder."""
    if show_values:
        return value
    return describe_shape(value, max_depth=depth)


def _fmt(value: Any) -> str:
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def _mask_name(name: str, show_values: bool) -> str:
    if show_values or not name:
        return name
    parts = name.split()
    return " ".join(part[0] + "…" for part in parts) or "…"


def report(data: Any, show_values: bool) -> None:
    """Print what each entity would show."""
    print(f"\nschool:   {data.school_name or '(not found)'}")
    print(f"guardian: {_mask_name(data.guardian_name or '', show_values) or '(not found)'}")
    print(f"children: {len(data.students)}")
    for warning in data.warnings:
        print(f"  ! {warning}")

    for student in data.students.values():
        print(f"\n── {_mask_name(student.name, show_values)}  (id {student.student_id})")
        print(f"   year group        {student.year_group or '-'}")
        print(f"   form group        {student.form_group or '-'}")
        print(f"   house / tutor     {student.house or '-'} / {student.tutor or '-'}")
        print(f"   attendance        {_or_dash(student.attendance.percentage, '%')}")
        print(f"   behaviour net     {_or_dash(student.behaviour_points_net)}")
        print(
            f"     positive {_or_dash(student.behaviour_points_positive)}"
            f"  negative {_or_dash(student.behaviour_points_negative)}"
            f"  incidents {len(student.behaviour_incidents)}"
        )
        for polarity, periods in student.behaviour_totals.items():
            spans = "  ".join(f"{period} {count:g}" for period, count in periods.items())
            print(f"     {polarity:<9} {spans}")
        print(
            f"   assignments       {len(student.assignments)} total,"
            f" {len(student.outstanding_assignments)} outstanding,"
            f" {len(student.overdue_assignments)} overdue"
        )
        lesson = student.next_lesson
        print(
            f"   next lesson       {lesson.summary if lesson else '-'}"
            f"{f' at {lesson.start}' if lesson and lesson.start else ''}"
        )
        print(f"   lessons known     {len(student.lessons)}")
        account = student.primary_account
        print(
            f"   meal balance      {account.balance if account else '-'}"
            f"{f' ({account.name})' if account else ''}"
        )
        print(f"   grades            {len(student.grades)}")
        print(f"   notices           {len(student.notices)}")
        # A sourced-but-empty domain is usually right: no homework due really is
        # none. An unsourced one means no page was found, which is a gap.
        quiet = sorted(student.empty_domains & student.sourced_domains)
        missing = sorted(student.unsourced_domains)
        if quiet:
            print(f"   none reported     {', '.join(quiet)}  (page read, nothing in it)")
        if missing:
            print(f"   NO SOURCE         {', '.join(missing)}  (no page found to read)")
        print(f"   pages scraped     {len(student.raw)}")
        for key in sorted(student.raw):
            print(f"     - {key}")

        if show_values:
            _print_samples(student)


def _print_samples(student: Any) -> None:
    """Show the real rows, to confirm the parse is actually right."""
    if student.assignments:
        print("   assignments:")
        for item in student.assignments:
            due = item.due.date() if hasattr(item.due, "date") else item.due
            print(f"     · {item.title}")
            print(
                f"         subject {item.subject or '-'}"
                f"   class {item.class_code or '-'}"
                f"   due {due or '-'}"
            )
            print(
                f"         status {item.status or '-'}"
                f"   marking {item.marking or '-'}"
                f"   hand in {item.submission_type or '-'}"
            )
            if item.instructions:
                print(f"         task: {_clip(item.instructions, 110)}")
    if student.behaviour_incidents:
        print("   behaviour incidents:")
        for item in student.behaviour_incidents:
            print(
                f"     · {item.occurred}  {item.polarity or '?':<8} {item.kind or '-'}"
                f"  [{item.event or 'no event'}]"
                f"{f'  {item.points:+g} pts' if item.points is not None else ''}"
            )
            details = [
                part
                for part in (
                    f"by {item.staff}" if item.staff else None,
                    f'"{_clip(item.comment, 90)}"' if item.comment else None,
                )
                if part
            ]
            if details:
                print(f"         {'  '.join(details)}")
        _print_behaviour_breakdown(student)
    if student.lessons:
        print("   sample lessons:")
        for item in student.lessons[:4]:
            print(f"     · {item.summary!r} {item.start} -> {item.end} at {item.location!r}")


def _print_behaviour_breakdown(student: Any) -> None:
    """Group the incidents the way a parent would ask about them."""
    from collections import Counter

    for title, key in (("by subject", "subject"), ("by type", "kind")):
        counts = Counter(
            getattr(item, key) or "(not stated)" for item in student.behaviour_incidents
        )
        print(f"   behaviour {title}:")
        for name, count in counts.most_common():
            print(f"     · {name}: {count}")


def _clip(text: str, limit: int) -> str:
    """Shorten *text* for one line of terminal output."""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _or_dash(value: Any, suffix: str = "") -> str:
    return "-" if value is None else f"{value}{suffix}"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


async def cmd_report(client: UrllibArborClient, args: argparse.Namespace) -> int:
    scraper = ArborScraper(
        client.fetch_page, client.fetch_json, client.post_json, logger=_LOGGER
    )
    data = await scraper.async_scrape()
    report(data, args.show_values)
    if scraper.unavailable:
        print("\nrefused by Arbor for this account:")
        for key in sorted(scraper.unavailable):
            print(f"  - {key}")
    unsourced = [s for s in data.students.values() if s.unsourced_domains]
    if unsourced:
        print(
            "\nSome domains have no page to read. Send the shape dump:\n"
            f"    python3 {sys.argv[0]} shapes --email {args.email} > shapes.txt",
            file=sys.stderr,
        )
    return 0


async def cmd_pages(client: UrllibArborClient, args: argparse.Namespace) -> int:
    scraper = ArborScraper(
        client.fetch_page, client.fetch_json, client.post_json, logger=_LOGGER
    )
    data = await scraper.async_scrape()
    print("\ndiscovered pages by domain:")
    for domain, entries in sorted(data.discovered_pages.items()):
        print(f"  {domain}")
        for caption, url in entries.items():
            label = caption if args.show_values else "<caption>"
            print(f"    {label}: {url}")
    return 0


async def cmd_shapes(client: UrllibArborClient, args: argparse.Namespace) -> int:
    """Dump the structure of every page a scrape actually reads.

    One command instead of running `shape` against each route by hand, which is
    what is needed to write extractors for a portal whose payloads have not been
    seen before.
    """
    runner = ArborScraper(
        client.fetch_page, client.fetch_json, client.post_json, logger=_LOGGER
    )
    data = await runner.async_scrape()

    print(f"# school   {data.school_name or '(not found)'}")
    print(f"# children {len(data.students)}")
    for reason in data.warnings:
        print(f"# note     {reason}")

    for key in sorted(data.raw):
        print(f"\n----- (guardian-wide) {key}")
        print(_fmt(redact(data.raw[key], args.show_values, args.depth)))

    for student in data.students.values():
        print(f"\n{'=' * 72}")
        print(f"# child {student.student_id}  empty: {sorted(student.empty_domains) or 'none'}")
        print("=" * 72)
        for key in sorted(student.raw):
            print(f"\n----- {key}")
            print(_fmt(redact(student.raw[key], args.show_values, args.depth)))
    return 0


async def cmd_shape(client: UrllibArborClient, args: argparse.Namespace) -> int:
    payload = await client.fetch_page(args.path)
    print(_fmt(redact(payload, args.show_values, args.depth)))
    return 0


async def cmd_json(client: UrllibArborClient, args: argparse.Namespace) -> int:
    payload = await client.fetch_json(args.path)
    print(_fmt(redact(payload, args.show_values, args.depth)))
    return 0


async def cmd_login(client: UrllibArborClient, args: argparse.Namespace) -> int:
    """Walk the three login steps, printing exactly what Arbor answers.

    When credentials that work in a browser are refused here, the reason is in
    Arbor's own response and nowhere else. Secrets are stripped; the messages
    that explain a refusal are not personal data.
    """
    print("step 1: POST /applications/search-by-email")
    request = protocol.school_search_request(client.email, client.password)
    for name in sorted(request.headers):
        if name.lower() != "content-type":
            print(f"    {name}: {request.headers[name]}")
    status, body = client.request_raw(request)
    print(f"  -> HTTP {status}, {len(body)} bytes")
    payload = _safe_json(body)
    if payload is None:
        print(f"  -> not JSON. First 300 characters:\n{body[:300]}")
        return 1
    schools = payload.get("payload") if isinstance(payload, dict) else None
    print(f"  -> schools returned: {len(schools) if isinstance(schools, list) else 0}")
    print(_fmt(protocol.redact_login_response(_without_school_names(payload))))
    if not schools:
        print(
            "\n  Arbor returned no school, which is how it reports credentials it "
            "does not accept at this step.",
            file=sys.stderr,
        )
        return 3

    # Resolve the school the same way every other command does. Taking
    # schools[0] instead sent one school's password to another, and Arbor
    # answered -- correctly -- that the credentials were wrong.
    try:
        parsed = protocol.parse_school_search(body)
    except ArborError as err:
        print(f"  -> could not read the school list: {err}", file=sys.stderr)
        return 1
    base_url = client.base_url or client.resolve_school(parsed)
    print(f"\nstep 2: POST {base_url}/auth/login")
    status, body = client.request_raw(
        protocol.login_request(base_url, client.email, client.password)
    )
    print(f"  -> HTTP {status}, {len(body)} bytes")
    payload = _safe_json(body)
    if payload is None:
        print(f"  -> not JSON. First 300 characters:\n{body[:300]}")
        return 1
    print(_fmt(protocol.redact_login_response(payload)))

    items = payload.get("items") if isinstance(payload, dict) else None
    first = items[0] if isinstance(items, list) and items else {}
    if not (isinstance(first, dict) and first.get("logged_in") is True):
        print(
            f"\n  Refused: {protocol.login_rejection_reason(payload)}\n"
            "  The JSON above is what Arbor said; send it as printed.",
            file=sys.stderr,
        )
        return 3

    print("\nstep 3: GET /?session=... (cookie handshake)")
    status, _ = client.request_raw(
        protocol.HttpRequest(
            "GET",
            protocol.session_handshake_url(base_url, str(first["session_id"])),
            None,
            {"User-Agent": protocol.USER_AGENT},
        )
    )
    print(f"  -> HTTP {status}")
    print("  cookies: " + (
        ", ".join(f"{c.name}@{c.domain}" for c in client.cookies) or "(none)"
    ))
    print("\nLogin completed.")
    return 0


def _safe_json(body: str) -> Any:
    try:
        return json.loads(strip_json_prefix(body))
    except ValueError:
        return None


def _without_school_names(payload: Any) -> Any:
    """Keep a school-search response's shape without listing the schools."""
    if not isinstance(payload, dict):
        return payload
    trimmed = dict(payload)
    entries = trimmed.get("payload")
    if isinstance(entries, list):
        trimmed["payload"] = [
            {key: value for key, value in entry.items() if key in ("sisUrl",)}
            if isinstance(entry, dict)
            else entry
            for entry in entries
        ]
    return trimmed


async def cmd_whoami(client: UrllibArborClient, args: argparse.Namespace) -> int:
    """Establish a session and prove whether Arbor considers it logged in.

    Every portal route answers an unauthenticated request with the same 401 a
    forbidden one gives, so "the dashboard 401'd" does not say whether the
    session failed or the route is simply not on offer. ``logged_in`` from
    current-user-settings is what separates the two.
    """
    client.login()
    print(f"base_url:  {client.base_url}")
    print("cookies:   " + (
        ", ".join(f"{c.name}@{c.domain}" for c in client.cookies) or "(none)"
    ))

    session_ok: bool | None = None
    try:
        settings = await client.fetch_json(CURRENT_USER_SETTINGS_PATH)
        session_ok = _logged_in_flag(settings)
        print(f"logged_in: {session_ok}")
        if args.show_values:
            print(_fmt(settings))
        else:
            print(_fmt(redact(settings, False, args.depth)))
    except ArborError as err:
        print(f"logged_in: could not tell ({err})")

    print("\nhomepages:")
    reachable = 0
    for path in scraper.HOMEPAGE_CANDIDATES:
        try:
            tree = await client.fetch_page(path)
        except ArborError as err:
            print(f"  ✗ {path}\n      {err}")
            continue
        reachable += 1
        print(f"  ✓ {path}  ({_summarise(tree)})")

    print("\nendpoints:")
    for path in ("/navigation/main-menu/format/json", "/widget-data/get-notices/format/json"):
        try:
            tree = await client.fetch_json(path)
            print(f"  ✓ {path}  ({_summarise(tree)})")
        except ArborError as err:
            print(f"  ✗ {path}\n      {err}")

    if session_ok is False:
        print(
            "\nArbor does not consider this session logged in, so every 401 above is "
            "a session problem rather than a permissions one.",
            file=sys.stderr,
        )
        return 3
    if not reachable:
        print(
            "\nThe session is valid but no homepage is available to this account. "
            "Please send the output above; the route list is what is needed next.",
            file=sys.stderr,
        )
        return 4
    return 0


def _logged_in_flag(settings: Any) -> bool | None:
    """Read `logged_in` out of a current-user-settings payload."""
    items = settings.get("items") if isinstance(settings, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        value = items[0].get("logged_in")
        if isinstance(value, bool):
            return value
    return None


def _summarise(tree: Any) -> str:
    """One-line description of a payload, with no content in it."""
    if isinstance(tree, dict):
        return f"dict, {len(tree)} keys: {', '.join(list(tree)[:6])}"
    if isinstance(tree, list):
        return f"list of {len(tree)}"
    return type(tree).__name__


COMMANDS = {
    "login": cmd_login,
    "report": cmd_report,
    "shapes": cmd_shapes,
    "pages": cmd_pages,
    "shape": cmd_shape,
    "json": cmd_json,
    "whoami": cmd_whoami,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="report",
        choices=sorted(COMMANDS),
        help=(
            "report: what every entity would show. "
            "pages: the portal pages discovered. "
            "shapes: the structure of every page a scrape reads. "
            "shape: one page's structure. "
            "json: one /format/json endpoint. "
            "login: the three login steps and exactly what Arbor answers. "
            "whoami: login plus the dashboard."
        ),
    )
    parser.add_argument("path", nargs="?", help="portal path, for shape and json")
    parser.add_argument(
        "--email",
        default=os.environ.get("ARBOR_EMAIL"),
        help="your Arbor email address. Defaults to $ARBOR_EMAIL.",
    )
    parser.add_argument(
        "--school",
        "--school-url",
        dest="school",
        default=os.environ.get("ARBOR_SCHOOL") or os.environ.get("ARBOR_SCHOOL_URL"),
        help="which school, when the account covers more than one. Either a URL "
        "(https://your-school.uk.arbor.education) or a name fragment (wrotham). "
        "Defaults to $ARBOR_SCHOOL.",
    )
    parser.add_argument(
        "--show-values",
        action="store_true",
        help="print real values instead of redacted shapes. Your own screen only: "
        "the output will contain your child's personal data.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="ignore the saved session and log in again. Arbor limits logins, so "
        "prefer reusing the session unless you are testing the login itself.",
    )
    parser.add_argument(
        "--keychain-service",
        default=os.environ.get("ARBOR_KEYCHAIN_SERVICE", DEFAULT_KEYCHAIN_SERVICE),
        help="macOS Keychain service name to read the password from "
        f"(default {DEFAULT_KEYCHAIN_SERVICE!r}).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_SHAPE_DEPTH,
        help=f"how deep to describe a payload's structure (default {DEFAULT_SHAPE_DEPTH}). "
        "Raise it if the output shows '<max depth>' where you need detail.",
    )
    parser.add_argument(
        "--forget-school",
        action="store_true",
        help="forget the remembered school choice and exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.forget_school:
        forget_schools()
        return 0

    if not args.email:
        print("error: --email is required (or set $ARBOR_EMAIL)", file=sys.stderr)
        return 2

    if args.command in ("shape", "json") and not args.path:
        print(f"error: '{args.command}' needs a path argument", file=sys.stderr)
        return 2

    def resolve_password() -> str:
        """Find a password, only once one is actually needed."""
        # Never a command-line argument: it would be in shell history.
        if password := os.environ.get("ARBOR_PASSWORD"):
            _LOGGER.debug("Password taken from $ARBOR_PASSWORD")
            return password
        if password := keychain_password(args.email, args.keychain_service):
            _LOGGER.debug("Password taken from the macOS Keychain")
            return password
        if sys.stdin.isatty():
            return getpass.getpass(f"Arbor password for {args.email}: ")
        raise ArborConfigurationError(
            "a login is needed but no password is available, and there is no "
            "terminal to ask on.\n\n"
            "Store it once in your Keychain (it prompts without echo, and nothing\n"
            "is written to your shell history):\n"
            f"    {keychain_store_command(args.email, args.keychain_service)}\n\n"
            "Or set it for one terminal session:\n"
            "    read -rs ARBOR_PASSWORD && export ARBOR_PASSWORD"
        )

    client = UrllibArborClient(
        args.email, resolve_password, args.school, reuse_session=not args.fresh
    )
    try:
        return asyncio.run(COMMANDS[args.command](client, args))
    except ArborConfigurationError as err:
        print(f"\n{err}", file=sys.stderr)
        return 2
    except ArborAuthError as err:
        print(
            f"\nauthentication failed: {err}\n\n"
            "Arbor is reporting the credentials themselves as wrong. Check the\n"
            "password -- and check it is the password for the right school, since\n"
            "each Arbor school is a separate account even under one email address.",
            file=sys.stderr,
        )
        return 3
    except ArborNotAvailableError as err:
        print(f"\nnot available: {err}", file=sys.stderr)
        return 4
    except ArborConnectionError as err:
        if protocol.is_rate_limited(str(err)):
            print(
                f"\n{err}\n\n"
                "That is a rate limit, not a password problem. Wait a couple of\n"
                "minutes and run the same command again.",
                file=sys.stderr,
            )
            return 5
        print(f"\nconnection problem: {err}", file=sys.stderr)
        return 5
    except ArborError as err:
        print(f"\nerror: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
