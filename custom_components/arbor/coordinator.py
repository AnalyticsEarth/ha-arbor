"""Update coordinator for the Arbor integration."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArborClient
from .errors import ArborAuthError, ArborError, ArborNotAvailableError
from .const import (
    ALL_DATA_DOMAINS,
    DATA_REJECTIONS,
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
    DOMAIN,
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

# Upper bound on pages fetched per child per refresh, so an unusual portal
# layout cannot turn one update into hundreds of requests.
MAX_PAGES_PER_STUDENT = 12

# More candidates than this almost certainly means discovery matched something
# that is not a person.
MAX_PLAUSIBLE_CHILDREN = 8

# How many refreshes in a row Arbor must reject the stored credentials before the
# user is asked to re-enter them. Arbor rejects a login under load and while
# rate-limiting, and the password is almost never the real problem, so a single
# rejection is retried silently instead of interrupting the user.
REJECTIONS_BEFORE_REAUTH = 3

# Domains that are worth a page fetch of their own.
_FETCHED_DOMAINS = (
    DATA_ATTENDANCE,
    DATA_BEHAVIOUR,
    DATA_ASSIGNMENTS,
    DATA_TIMETABLE,
    DATA_PROGRESS,
    DATA_MEALS,
)


class ArborCoordinator(DataUpdateCoordinator[ArborData]):
    """Fetch and normalise everything the parent portal exposes."""

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
        # Page paths learned on a previous refresh, reused so discovery cost is
        # paid once rather than on every poll.
        self._page_cache: dict[str, dict[str, dict[str, str]]] = {}
        # Calendar endpoint shapes that answered last time, so the ones Arbor
        # rejects are not retried on every refresh.
        self._calendar_templates_cache: list[tuple[str, str]] = []
        # Endpoints Arbor has refused for this account, so they are not requested
        # again. Rebuilt daily in case the school switches a feature on.
        self._unavailable: set[str] = set()
        self._unavailable_day: date | None = None
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
            data = await self._async_scrape()
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

    # -- orchestration ------------------------------------------------------

    async def _async_scrape(self) -> ArborData:
        data = ArborData()
        self._forget_stale_refusals()

        settings = await self._try_json(CURRENT_USER_SETTINGS_PATH)
        if settings is not None:
            data.guardian_name = _first_text(settings, ("userName", "user_name", "fullName", "name"))
            data.school_name = _first_text(
                settings, ("schoolName", "school_name", "institutionName", "applicationName")
            )

        dashboard = await self._async_dashboard()
        menu = await self._try_json(MAIN_MENU_PATH)
        roots = [tree for tree in (dashboard, menu) if tree is not None]
        if not roots:
            raise UpdateFailed(
                "Could not read the Arbor dashboard. Signing in worked, so this is a "
                "page-level problem: run the arbor.dump_page service against "
                f"{GUARDIAN_DASHBOARD_PAGE} to see what your school returns"
            )

        data.discovered_pages = classify_pages(roots, DOMAIN_KEYWORDS)

        refs = extract_student_refs(roots)
        if not refs:
            # Some portals show a single child without linking by id. Treat the
            # dashboard itself as that child's page so data is not lost.
            _LOGGER.debug("No student links found; falling back to a single dashboard student")
            student = StudentData(student_id="dashboard", name=data.guardian_name or "Student")
            await self._async_fill_student(
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
                _LOGGER.warning(
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
                await self._async_fill_student(
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

    async def _async_dashboard(self) -> Any | None:
        """The guardian dashboard, falling back to the other portal homepages."""
        for path in (GUARDIAN_DASHBOARD_PAGE, STUDENT_DASHBOARD_PAGE, STAFF_HOME_PAGE):
            tree = await self._try_page(path)
            if tree is not None:
                _LOGGER.debug("Using %s as the Arbor homepage", path)
                return tree
        return None

    async def _async_fill_student(
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
                _LOGGER.debug(
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
            await self._async_calendar_trees(student) if allow_shared_sources else []
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
                _LOGGER.debug("Resolved child %s to a name from page data", student.student_id)
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

    async def _async_calendar_trees(self, student: StudentData) -> list[Any]:
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

        Only :class:`ArborAuthError` escapes, and after the login rework that can
        only come from the login handshake itself -- so a single forbidden
        endpoint can no longer take the whole integration down.
        """
        key = _endpoint_key(path)
        if key in self._unavailable:
            return None
        try:
            return await fetch(path)
        except ArborAuthError:
            raise
        except ArborNotAvailableError as err:
            _LOGGER.debug("Arbor does not offer %s %s: %s", label, path, err)
            self._unavailable.add(key)
            return None
        except ArborError as err:
            # Transient: worth trying again on the next refresh.
            _LOGGER.debug("Skipping Arbor %s %s: %s", label, path, err)
            return None

    async def _try_page(self, path: str) -> Any | None:
        """Fetch a portal page, returning None when it is unavailable."""
        return await self._try(path, self.client.async_fetch_absolute, "page")

    async def _try_json(self, path: str) -> Any | None:
        """Fetch a JSON endpoint, returning None when it is unavailable."""
        return await self._try(path, self.client.async_fetch_json, "endpoint")


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
