#!/usr/bin/env python3
"""Drive the Arbor integration's scraper from the command line.

Runs exactly the code Home Assistant runs -- the same login protocol, the same
page URLs, the same parser and the same orchestration -- against a real account,
so a change can be checked in a second instead of by restarting Home Assistant.
Only the HTTP transport differs: this uses the standard library, so there is
nothing to install.

Your password is never taken as an argument (it would land in your shell
history) and never written anywhere. It is read from the ARBOR_PASSWORD
environment variable if set, otherwise prompted for without echo.

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
import http.cookiejar
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
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


class UrllibArborClient:
    """The integration's ArborClient, over urllib instead of aiohttp.

    Deliberately mirrors ``api.ArborClient``: same protocol calls, same URL
    building, same response classification. Anything shared lives in the
    integration's modules so the two cannot disagree about what Arbor said.
    """

    def __init__(self, email: str, password: str, school: str | None = None) -> None:
        self._email = email
        self._password = password
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
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )

    @property
    def base_url(self) -> str | None:
        return self._base_url

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

    # -- login --------------------------------------------------------------

    def list_schools(self) -> list[Any]:
        request = protocol.school_search_request(self._email, self._password)
        status, body = self._request(
            request.method, request.url, request.body, request.headers
        )
        if status == 429:
            raise ArborConnectionError("Arbor rate-limited the login; try again later")
        if status >= 500:
            raise ArborConnectionError(f"Arbor login service returned HTTP {status}")
        return protocol.parse_school_search(body)

    def _resolve_school(self) -> str:
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

        request = protocol.login_request(self._base_url, self._email, self._password)
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

    def _ensure_logged_in(self) -> str:
        """Log in if needed and return the tenant base URL."""
        if not self._logged_in:
            self.login()
        if self._base_url is None:
            raise ArborConfigurationError("No Arbor school selected")
        return self._base_url


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


def redact(value: Any, show_values: bool) -> Any:
    """Replace free text with a type-and-length placeholder."""
    if show_values:
        return value
    return describe_shape(value)


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
        print(f"   attendance        {_or_dash(student.attendance.percentage, '%')}")
        print(f"   behaviour net     {_or_dash(student.behaviour_points_net)}")
        print(
            f"     positive {_or_dash(student.behaviour_points_positive)}"
            f"  negative {_or_dash(student.behaviour_points_negative)}"
            f"  incidents {len(student.behaviour_incidents)}"
        )
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
        if student.empty_domains:
            print(f"   EMPTY             {', '.join(sorted(student.empty_domains))}")
        print(f"   pages scraped     {len(student.raw)}")
        for key in sorted(student.raw):
            print(f"     - {key}")

        if show_values:
            _print_samples(student)


def _print_samples(student: Any) -> None:
    """Show a couple of real rows, to confirm the parse is actually right."""
    if student.assignments:
        print("   sample assignments:")
        for item in student.assignments[:3]:
            print(
                f"     · {item.title!r} subject={item.subject!r} due={item.due} "
                f"status={item.status!r} grade={item.grade!r}"
            )
    if student.behaviour_incidents:
        print("   sample behaviour:")
        for item in student.behaviour_incidents[:3]:
            print(
                f"     · {item.occurred} {item.kind!r} points={item.points} "
                f"subject={item.subject!r}"
            )
    if student.lessons:
        print("   sample lessons:")
        for item in student.lessons[:4]:
            print(f"     · {item.summary!r} {item.start} -> {item.end} at {item.location!r}")


def _or_dash(value: Any, suffix: str = "") -> str:
    return "-" if value is None else f"{value}{suffix}"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


async def cmd_report(client: UrllibArborClient, args: argparse.Namespace) -> int:
    scraper = ArborScraper(client.fetch_page, client.fetch_json, logger=_LOGGER)
    data = await scraper.async_scrape()
    report(data, args.show_values)
    if scraper.unavailable:
        print("\nrefused by Arbor for this account:")
        for key in sorted(scraper.unavailable):
            print(f"  - {key}")
    empty = [s for s in data.students.values() if s.empty_domains]
    if empty:
        print(
            "\nSome domains are empty. Run:  "
            f"python3 {sys.argv[0]} shape <path> --email {args.email}",
            file=sys.stderr,
        )
    return 0


async def cmd_pages(client: UrllibArborClient, args: argparse.Namespace) -> int:
    scraper = ArborScraper(client.fetch_page, client.fetch_json, logger=_LOGGER)
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
    runner = ArborScraper(client.fetch_page, client.fetch_json, logger=_LOGGER)
    data = await runner.async_scrape()

    print(f"# school   {data.school_name or '(not found)'}")
    print(f"# children {len(data.students)}")
    for reason in data.warnings:
        print(f"# note     {reason}")

    for student in data.students.values():
        print(f"\n{'=' * 72}")
        print(f"# child {student.student_id}  empty: {sorted(student.empty_domains) or 'none'}")
        print("=" * 72)
        for key in sorted(student.raw):
            print(f"\n----- {key}")
            print(_fmt(redact(student.raw[key], args.show_values)))
    return 0


async def cmd_shape(client: UrllibArborClient, args: argparse.Namespace) -> int:
    payload = await client.fetch_page(args.path)
    print(_fmt(redact(payload, args.show_values)))
    return 0


async def cmd_json(client: UrllibArborClient, args: argparse.Namespace) -> int:
    payload = await client.fetch_json(args.path)
    print(_fmt(redact(payload, args.show_values)))
    return 0


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
            print(_fmt(redact(settings, False)))
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

    # Never a command-line argument: it would be recorded in shell history.
    password = os.environ.get("ARBOR_PASSWORD")
    if not password:
        password = getpass.getpass(f"Arbor password for {args.email}: ")
    if not password:
        print("error: no password given", file=sys.stderr)
        return 2

    client = UrllibArborClient(args.email, password, args.school)
    try:
        return asyncio.run(COMMANDS[args.command](client, args))
    except ArborConfigurationError as err:
        print(f"\n{err}", file=sys.stderr)
        return 2
    except ArborAuthError as err:
        print(f"\nauthentication failed: {err}", file=sys.stderr)
        return 3
    except ArborNotAvailableError as err:
        print(f"\nnot available: {err}", file=sys.stderr)
        return 4
    except ArborError as err:
        print(f"\nerror: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
