"""Transport-independent orchestration of an Arbor scrape.

Everything that decides *what* to read and how to interpret it lives here, and
the two async callables it is given decide *how* to fetch. Home Assistant passes
in an aiohttp-backed client; ``tools/arbor_probe.py`` passes in a
standard-library one, so a change can be verified against a real account without
restarting Home Assistant and without the two paths drifting.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from .errors import (
    ArborAuthError,
    ArborConfigurationError,
    ArborError,
    ArborNotAvailableError,
)
from .const import (
    ALL_DATA_DOMAINS,
    CALENDAR_DATA_PATH,
    CALENDAR_ENTRY_LIST_PATH,
    CURRENT_USER_SETTINGS_PATH,
    DATA_ASSIGNMENTS,
    DATA_ATTENDANCE,
    DATA_BEHAVIOUR,
    DATA_MEALS,
    DATA_NOTICES,
    DATA_PROGRESS,
    DATA_TIMETABLE,
    DOMAIN_KEYWORDS,
    GUARDIAN_DASHBOARD_PAGE,
    MAIN_MENU_PATH,
    NOTICES_PATH,
    STAFF_HOME_PAGE,
    STUDENT_DASHBOARD_PAGE,
)
from .models import ArborData, StudentData
from .parser import (
    classify_pages,
    filter_pages_for_student,
    extract_accounts,
    extract_assignments,
    extract_attendance,
    extract_behaviour,
    extract_grades,
    extract_lessons_from_calendar,
    extract_lessons_from_tables,
    extract_notices,
    extract_profile_fields,
    extract_student_name,
    extract_student_refs,
    text_of,
    walk,
)

_LOGGER = logging.getLogger(__name__)

#: Fetches a portal page, by path or by a URL Arbor itself supplied.
type PageFetcher = Callable[[str], Awaitable[Any]]
#: Fetches a direct `/format/json` endpoint.
type JsonFetcher = Callable[[str], Awaitable[Any]]

# Portal homepages, in the order they are worth trying for a guardian.
HOMEPAGE_CANDIDATES = (
    GUARDIAN_DASHBOARD_PAGE,
    STUDENT_DASHBOARD_PAGE,
    STAFF_HOME_PAGE,
)

# Upper bound on pages fetched per child per refresh, so an unusual portal
# layout cannot turn one update into hundreds of requests.
MAX_PAGES_PER_STUDENT = 12

# More candidates than this almost certainly means discovery matched something
# that is not a person.
MAX_PLAUSIBLE_CHILDREN = 8


# Domains that are worth a page fetch of their own.
_FETCHED_DOMAINS = (
    DATA_ATTENDANCE,
    DATA_BEHAVIOUR,
    DATA_ASSIGNMENTS,
    DATA_TIMETABLE,
    DATA_PROGRESS,
    DATA_MEALS,
)




class ArborScraper:
    """Reads one guardian account and normalises what it finds."""

    def __init__(
        self,
        fetch_page: PageFetcher,
        fetch_json: JsonFetcher,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Take the two fetchers this scraper will use."""
        self._fetch_page = fetch_page
        self._fetch_json = fetch_json
        self._log = logger or _LOGGER
        # Page paths learned on a previous scrape, reused so discovery cost is
        # paid once rather than on every poll.
        self._page_cache: dict[str, dict[str, dict[str, str]]] = {}
        # Calendar endpoint shapes that answered last time, so the ones Arbor
        # rejects are not retried on every scrape.
        self._calendar_templates_cache: list[tuple[str, str]] = []
        # Endpoints Arbor has refused for this account, so they are not requested
        # again. Rebuilt daily in case the school switches a feature on.
        self._unavailable: set[str] = set()
        self._unavailable_day: date | None = None

    @property
    def unavailable(self) -> set[str]:
        """Endpoint keys Arbor has refused, for diagnostics."""
        return self._unavailable

    async def async_scrape(self) -> ArborData:
        data = ArborData()
        self._forget_stale_refusals()

        settings = await self._try_json(CURRENT_USER_SETTINGS_PATH)
        if settings is not None:
            # `display_name` and `organizationName` are what a live guardian
            # account actually returns; the camelCase variants are kept for
            # tenants on other Arbor releases.
            data.guardian_name = _first_text(
                settings,
                ("display_name", "displayName", "userName", "user_name", "fullName"),
            )
            data.school_name = _first_text(
                settings,
                (
                    "organizationName",
                    "organization_name",
                    "schoolName",
                    "school_name",
                    "institutionName",
                ),
            )

        dashboard, homepage_reasons = await self._dashboard()
        menu = await self._try_json(MAIN_MENU_PATH)
        roots = [tree for tree in (dashboard, menu) if tree is not None]
        if not roots:
            detail = "; ".join(homepage_reasons) or "no reason reported"
            raise ArborError(
                "Could not read any Arbor homepage, so there is nothing to discover "
                f"children from. Signing in succeeded, so this is page-level. Tried: "
                f"{detail}"
            )
        data.warnings.extend(homepage_reasons)

        data.discovered_pages = classify_pages(roots, DOMAIN_KEYWORDS)

        refs = extract_student_refs(roots)
        if not refs:
            # Some portals show a single child without linking by id. Treat the
            # dashboard itself as that child's page so data is not lost.
            self._log.debug("No student links found; falling back to a single dashboard student")
            student = StudentData(student_id="dashboard", name=data.guardian_name or "Student")
            await self._fill_student(
                student, roots, dashboard_only=True, allow_shared_sources=True
            )
            data.students[student.student_id] = student
            data.warnings.append(
                "No child profile links were found on the dashboard, so all data was read "
                "from the dashboard itself."
            )
        else:
            # Guardian-wide pages show whichever child is selected, so they can
            # only be read as a child's own data when there is just one child.
            if len(refs) > MAX_PLAUSIBLE_CHILDREN:
                # Discovery has misfired before by mistaking portal links for
                # people; say so loudly rather than inventing a dozen devices.
                self._log.warning(
                    "Arbor discovery found %d children for this account, which is "
                    "more than expected. Download the integration's diagnostics and "
                    "check the profile URL shapes if these are not real children",
                    len(refs),
                )
                data.warnings.append(
                    f"Discovery found {len(refs)} children, which looks implausible."
                )
            single_child = len(refs) == 1
            for ref in refs:
                student = StudentData(
                    student_id=ref.student_id, name=ref.name, profile_url=ref.url
                )
                await self._fill_student(
                    student, roots, allow_shared_sources=single_child
                )
                data.students[student.student_id] = student

        notices = await self._try_json(NOTICES_PATH)
        if notices is not None:
            data.school_notices = extract_notices([notices])

        for student in data.students.values():
            if not student.notices:
                student.notices = data.school_notices

        return data

    async def _dashboard(self) -> tuple[Any | None, list[str]]:
        """The guardian dashboard, falling back to the other portal homepages.

        Returns the tree and, whether or not one was found, why each candidate
        was rejected. Throwing those reasons away made a failure here impossible
        to diagnose: every homepage answers an unauthenticated request with the
        same 401 a forbidden one does.
        """
        reasons: list[str] = []
        for path in HOMEPAGE_CANDIDATES:
            key = _endpoint_key(path)
            if key in self._unavailable:
                reasons.append(f"{path}: refused earlier in this session")
                continue
            try:
                tree = await self._fetch_page(path)
            except (ArborAuthError, ArborConfigurationError):
                raise
            except ArborNotAvailableError as err:
                self._unavailable.add(key)
                reasons.append(f"{path}: {err}")
                continue
            except ArborError as err:
                reasons.append(f"{path}: {err}")
                continue
            if tree is None:
                reasons.append(f"{path}: empty response")
                continue
            self._log.debug("Using %s as the Arbor homepage", path)
            return tree, reasons
        return None, reasons

    async def _fill_student(
        self,
        student: StudentData,
        roots: list[Any],
        *,
        dashboard_only: bool = False,
        allow_shared_sources: bool = False,
    ) -> None:
        """Fetch and parse every page that belongs to one child.

        ``allow_shared_sources`` says whether guardian-wide pages -- the
        dashboard, the menu, the calendar feeds -- may be read as this child's
        data. They show whichever child the portal currently has selected, so
        with siblings they would attribute one child's figures to another.
        """
        # Link discovery always gets to see the shared pages; only the data
        # extraction is restricted.
        discovery_trees = list(roots)
        shared: list[Any] = list(roots) if allow_shared_sources else []
        trees: dict[str, list[Any]] = {domain: list(shared) for domain in ALL_DATA_DOMAINS}
        profile_trees: list[Any] = list(shared)

        if not dashboard_only and student.profile_url:
            profile = await self._try_page(student.profile_url)
            if profile is not None:
                discovery_trees.append(profile)
                profile_trees.append(profile)
                for bucket in trees.values():
                    bucket.append(profile)
                student.raw["profile"] = profile

        pages = filter_pages_for_student(
            classify_pages(discovery_trees, DOMAIN_KEYWORDS),
            student.student_id,
            keep_unscoped=allow_shared_sources or dashboard_only,
        )
        for domain, entries in self._page_cache.get(student.student_id, {}).items():
            pages.setdefault(domain, {}).update(entries)

        fetched = 0
        resolved: dict[str, dict[str, str]] = {}
        for domain in _FETCHED_DOMAINS:
            for caption, url in list(pages.get(domain, {}).items()):
                if fetched >= MAX_PAGES_PER_STUDENT:
                    break
                tree = await self._try_page(url)
                fetched += 1
                if tree is None:
                    continue
                resolved.setdefault(domain, {})[caption] = url
                trees[domain].append(tree)
                student.raw[f"{domain}:{caption}"] = tree
            if fetched >= MAX_PAGES_PER_STUDENT:
                self._log.debug(
                    "Reached the per-student page limit for %s; skipping remaining pages",
                    student.name,
                )
                break
        if resolved:
            self._page_cache[student.student_id] = resolved

        timetable_pages = [
            tree for key, tree in student.raw.items() if key.startswith(f"{DATA_TIMETABLE}:")
        ]
        calendar_trees = (
            await self._calendar_trees(student) if allow_shared_sources else []
        )
        trees[DATA_TIMETABLE].extend(calendar_trees)

        student.attendance = extract_attendance(trees[DATA_ATTENDANCE])
        (
            student.behaviour_points_positive,
            student.behaviour_points_negative,
            student.behaviour_incidents,
        ) = extract_behaviour(trees[DATA_BEHAVIOUR])
        student.assignments = extract_assignments(trees[DATA_ASSIGNMENTS])
        student.grades = extract_grades(trees[DATA_PROGRESS])
        student.accounts = extract_accounts(trees[DATA_MEALS])
        student.notices = extract_notices(trees[DATA_NOTICES])

        # A page fetched for this child beats any guardian-wide feed.
        lessons = extract_lessons_from_tables(timetable_pages)
        if not lessons and calendar_trees:
            lessons = [
                lesson
                for tree in calendar_trees
                for lesson in extract_lessons_from_calendar(tree)
            ]
        if not lessons and allow_shared_sources:
            lessons = extract_lessons_from_tables(trees[DATA_TIMETABLE])
        student.lessons = sorted(lessons, key=lambda lesson: lesson.sort_key)

        # Discovery can find a child's id without ever seeing their name, when
        # Arbor puts it in page data rather than a link caption. Look again in
        # everything actually fetched for this child before settling for the
        # "Student <id>" placeholder.
        if student.name.startswith("Student "):
            named = extract_student_name(
                [tree for key, tree in student.raw.items() if key == "profile"]
                or list(student.raw.values())
            )
            if named:
                self._log.debug("Resolved child %s to a name from page data", student.student_id)
                student.name = named

        fields = extract_profile_fields(profile_trees)
        student.year_group = _field(fields, "year group", "year", "national curriculum year")
        student.form_group = _field(
            fields, "form group", "form", "registration group", "tutor group"
        )

        if not student.assignments:
            student.empty_domains.add(DATA_ASSIGNMENTS)
        if student.attendance.percentage is None:
            student.empty_domains.add(DATA_ATTENDANCE)
        if student.behaviour_points_net is None:
            student.empty_domains.add(DATA_BEHAVIOUR)
        if not student.lessons:
            student.empty_domains.add(DATA_TIMETABLE)

    async def _calendar_trees(self, student: StudentData) -> list[Any]:
        """Calendar payloads for the coming week, if the portal serves them.

        Arbor's calendar endpoints take their date range as path segments and the
        accepted shape varies by release, so the first shape that answers is
        remembered and the others are not tried again on later refreshes.
        """
        today = date.today()
        end = today + timedelta(days=7)
        trees: list[Any] = []
        working: list[tuple[str, str]] = []

        for base, templates in self._calendar_templates():
            for template in templates:
                path = base + template.format(start=today.isoformat(), end=end.isoformat())
                tree = await self._try_json(path)
                if tree is not None:
                    trees.append(tree)
                    working.append((base, template))
                    student.raw[f"calendar:{path}"] = tree
                    break

        self._calendar_templates_cache = working
        return trees

    def _calendar_templates(self) -> list[tuple[str, tuple[str, ...]]]:
        """Endpoint bases paired with the path suffixes worth trying."""
        if self._calendar_templates_cache:
            return [(base, (template,)) for base, template in self._calendar_templates_cache]
        suffixes = (
            "start-date/{start}/end-date/{end}",
            "date/{start}",
            "",
        )
        return [(base, suffixes) for base in (CALENDAR_DATA_PATH, CALENDAR_ENTRY_LIST_PATH)]

    # -- tolerant fetch helpers ---------------------------------------------

    def _forget_stale_refusals(self) -> None:
        """Re-probe refused endpoints once a day.

        A school can switch a portal feature on at any time, so a refusal is
        remembered to save requests, not treated as permanent.
        """
        today = date.today()
        if self._unavailable_day != today:
            self._unavailable.clear()
            self._unavailable_day = today

    async def _try(self, path: str, fetch: Any, label: str) -> Any | None:
        """Fetch something optional, remembering what Arbor refuses.

        A forbidden endpoint cannot take a whole scrape down. A bad password or a
        client that is not configured still must, so those two are re-raised.
        """
        key = _endpoint_key(path)
        if key in self._unavailable:
            return None
        try:
            return await fetch(path)
        except (ArborAuthError, ArborConfigurationError):
            raise
        except ArborNotAvailableError as err:
            self._log.debug("Arbor does not offer %s %s: %s", label, path, err)
            self._unavailable.add(key)
            return None
        except ArborError as err:
            # Transient: worth trying again on the next scrape.
            self._log.debug("Skipping Arbor %s %s: %s", label, path, err)
            return None

    async def _try_page(self, path: str) -> Any | None:
        """Fetch a portal page, returning None when it is unavailable."""
        return await self._try(path, self._fetch_page, "page")

    async def _try_json(self, path: str) -> Any | None:
        """Fetch a JSON endpoint, returning None when it is unavailable."""
        return await self._try(path, self._fetch_json, "endpoint")


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _endpoint_key(path: str) -> str:
    """A refusal key that survives the date rolling over.

    Only date-like segments are masked. Student ids are left intact, because one
    child being denied a page says nothing about their sibling.
    """
    return _ISO_DATE.sub("<date>", path)


def _first_text(tree: Any, keys: tuple[str, ...]) -> str | None:
    """First non-empty value for any of ``keys`` anywhere in a tree."""
    for node in walk(tree):
        for key in keys:
            if key in node:
                value = text_of(node[key])
                if value:
                    return value
    return None


def _field(fields: dict[str, str], *names: str) -> str | None:
    """Look up a profile field by any of several captions."""
    for name in names:
        if name in fields:
            return fields[name]
    for name in names:
        for key, value in fields.items():
            if name in key:
                return value
    return None
