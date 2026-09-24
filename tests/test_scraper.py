"""End-to-end tests for the scrape pipeline.

``ArborScraper`` takes its two fetchers as arguments, so a whole guardian account
can be simulated from fixtures. These are the tests that would have caught a
parser that understood nothing: every page is served successfully and the
assertions are about the data that comes out.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from datetime import datetime  # noqa: E402

from _loader import errors, load  # noqa: E402
from fixtures import pages  # noqa: E402

scraper_module = load("scraper")


class FakePortal:
    """Serves fixture pages, and records what was asked for."""

    def __init__(
        self,
        routes: dict[str, object],
        posts: dict[str, object] | None = None,
    ) -> None:
        self.routes = routes
        self.posts = posts or {}
        self.requested: list[str] = []
        self.posted: list[tuple[str, str]] = []

    async def fetch(self, path: str) -> object:
        self.requested.append(path)
        for route, payload in self.routes.items():
            if path.startswith(route):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise errors.ArborNotAvailableError(f"no such page: {path}")

    async def post(self, path: str, body: str) -> object:
        self.posted.append((path, body))
        for route, payload in self.posts.items():
            if path.startswith(route):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise errors.ArborNotAvailableError(f"no such endpoint: {path}")


def single_child_portal() -> FakePortal:
    refused = errors.ArborNotAvailableError("not for guardians")
    return FakePortal(
        {
            "/guardians/home-ui/dashboard": pages.SINGLE_CHILD_DASHBOARD,
            "/guardians/student-profile/index/student-id/40219": pages.PROFILE_PAGE,
            "/guardians/attendance/index/student-id/40219": pages.ATTENDANCE_PAGE,
            "/guardians/behaviour/index/student-id/40219": pages.BEHAVIOUR_PAGE,
            "/guardians/assignments/index/student-id/40219": pages.ASSIGNMENTS_PAGE,
            "/guardians/progress/index/student-id/40219": pages.PROGRESS_PAGE,
            "/guardians/meals/index/student-id/40219": pages.MEALS_PAGE,
            "/auth/current-user-settings/format/json": pages.CURRENT_USER_SETTINGS,
            "/widget-data/get-notices/format/json": pages.NOTICES_ENDPOINT,
            "/widget-data/get-calendar-data/format/json/": pages.CALENDAR_ENDPOINT,
            # Guardians cannot call this one; it must not break the scrape.
            "/calendar-entry/list-static/format/json/": refused,
            "/navigation/main-menu/format/json": refused,
        }
    )


class TestSingleChildScrape(unittest.IsolatedAsyncioTestCase):
    """The whole pipeline for a guardian with one child."""

    async def asyncSetUp(self) -> None:
        self.portal = single_child_portal()
        self.scraper = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await self.scraper.async_scrape()

    def test_finds_the_child_by_name(self) -> None:
        self.assertEqual(list(self.data.students), ["40219"])
        self.assertEqual(self.data.students["40219"].name, "Amelia Example")

    def test_reads_the_school_and_guardian_names(self) -> None:
        # A live account returns `organizationName` and `display_name`, not the
        # camelCase keys that were guessed first.
        self.assertEqual(self.data.school_name, "Example School")
        self.assertEqual(self.data.guardian_name, "Steven Example")

    def test_populates_every_domain(self) -> None:
        student = self.data.students["40219"]
        self.assertEqual(student.attendance.percentage, 96.4)
        self.assertEqual(student.behaviour_points_net, 114.0)
        self.assertEqual(len(student.assignments), 3)
        self.assertEqual(len(student.grades), 2)
        self.assertEqual(len(student.lessons), 3)
        self.assertTrue(student.accounts)
        self.assertTrue(student.notices)

    def test_reads_the_profile_fields(self) -> None:
        student = self.data.students["40219"]
        self.assertEqual(student.year_group, "Year 9")
        self.assertEqual(student.form_group, "9BQ")

    def test_a_refused_endpoint_does_not_break_the_scrape(self) -> None:
        self.assertTrue(self.data.students)
        self.assertEqual(self.data.warnings, [])

    def test_action_pages_are_not_fetched(self) -> None:
        self.assertNotIn(
            "/guardians/absence/new/student-id/40219", self.portal.requested
        )

    async def test_refusals_are_not_requested_again(self) -> None:
        before = sum(
            1 for path in self.portal.requested if path.startswith("/navigation/")
        )
        self.portal.requested.clear()
        await self.scraper.async_scrape()
        after = sum(
            1 for path in self.portal.requested if path.startswith("/navigation/")
        )
        self.assertEqual(before, 1)
        self.assertEqual(after, 0)


class TestSiblingsAreKeptApart(unittest.IsolatedAsyncioTestCase):
    """With two children, one child's pages must never feed the other."""

    async def asyncSetUp(self) -> None:
        # Every nav link on this dashboard is scoped to the first child.
        self.portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.GUARDIAN_DASHBOARD,
                "/guardians/student-profile/index/student-id/40219": pages.PROFILE_PAGE,
                "/guardians/attendance/index/student-id/40219": pages.ATTENDANCE_PAGE,
                "/guardians/behaviour/index/student-id/40219": pages.BEHAVIOUR_PAGE,
                "/guardians/assignments/index/student-id/40219": pages.ASSIGNMENTS_PAGE,
            }
        )
        self.scraper = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await self.scraper.async_scrape()

    def test_both_children_are_listed(self) -> None:
        self.assertEqual(sorted(self.data.students), ["40219", "40855"])

    def test_the_sibling_gets_no_data_rather_than_the_wrong_data(self) -> None:
        sibling = self.data.students["40855"]
        self.assertIsNone(sibling.attendance.percentage)
        self.assertEqual(sibling.assignments, [])
        self.assertIsNone(sibling.behaviour_points_net)

    def test_the_sibling_is_only_asked_for_its_own_profile(self) -> None:
        """Nothing but their own page may be fetched on a child's behalf.

        Their own profile is fair game -- it is scoped to them. What must not
        happen is the dashboard's first-child links being followed again while
        filling in the second child.
        """
        sibling_paths = [
            path for path in self.portal.requested if "student-id/40855" in path
        ]
        # Its own profile, plus the per-child endpoints, which are scoped by id.
        self.assertIn("/guardians/student-profile/index/student-id/40855", sibling_paths)
        for path in sibling_paths:
            self.assertNotIn("40219", path)

    def test_no_page_is_fetched_twice(self) -> None:
        # A first-child page fetched again would mean it was read for both.
        self.assertEqual(
            len(self.portal.requested), len(set(self.portal.requested))
        )

    def test_no_calendar_without_a_student_id_is_used_with_siblings(self) -> None:
        # A per-child feed is fine; one with no student id would be ambiguous.
        ambiguous = [
            path
            for path in self.portal.requested
            if ("calendar-data" in path or path.startswith("/calendar-entry/"))
            and "student-id/" not in path
        ]
        self.assertEqual(ambiguous, [])


class TestFollowsShellPagesToTheirContent(unittest.IsolatedAsyncioTestCase):
    """A guardian page that carries only a layout must still yield data.

    This is Wrotham School's architecture, and the reason a scrape could fetch
    six pages successfully and extract nothing at all from any of them.
    """

    async def asyncSetUp(self) -> None:
        self.portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.SINGLE_CHILD_DASHBOARD,
                "/guardians/student-profile/index/student-id/40219": pages.PROFILE_PAGE,
                # The discovered assignments page is a shell...
                "/guardians/assignments/index/student-id/40219": (
                    pages.SHELL_PAGE_WITH_CONTENT_URL
                ),
                # ...whose content lives here.
                "/guardians/student-ui/assignments-content/student-id/1879": (
                    pages.ASSIGNMENTS_CONTENT
                ),
                "/auth/current-user-settings/format/json": pages.CURRENT_USER_SETTINGS,
            }
        )
        self.scraper = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await self.scraper.async_scrape()

    def test_the_content_url_is_followed(self) -> None:
        self.assertIn(
            "/guardians/student-ui/assignments-content/student-id/1879",
            self.portal.requested,
        )

    def test_the_data_behind_the_shell_is_extracted(self) -> None:
        student = self.data.students["40219"]
        self.assertEqual(len(student.assignments), 2)
        self.assertEqual(
            sorted(item.title for item in student.assignments),
            ["Macbeth Act 2 essay", "Photosynthesis worksheet"],
        )

    def test_both_layers_are_kept_for_diagnostics(self) -> None:
        keys = self.data.students["40219"].raw
        self.assertTrue(any(key.endswith("> content 1") for key in keys), sorted(keys))

    def test_a_request_budget_is_enforced(self) -> None:
        self.assertLessEqual(
            len(self.portal.requested), scraper_module.MAX_REQUESTS_PER_STUDENT + 8
        )

    def test_nothing_is_fetched_twice(self) -> None:
        self.assertEqual(len(self.portal.requested), len(set(self.portal.requested)))


class TestContentFollowingIsBounded(unittest.IsolatedAsyncioTestCase):
    """A page that points at itself must not loop forever."""

    async def test_a_self_referencing_page_terminates(self) -> None:
        looping = {
            "type": "page",
            "content": [{"props": {"pageUrl": "/loop"}, "xtype": "mis-button-load-page"}],
        }
        portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.SINGLE_CHILD_DASHBOARD,
                "/guardians/student-profile/index/student-id/40219": looping,
                "/guardians/assignments/index/student-id/40219": looping,
                "/loop": looping,
            }
        )
        runner = scraper_module.ArborScraper(portal.fetch, portal.fetch)
        data = await runner.async_scrape()
        self.assertEqual(list(data.students), ["40219"])
        self.assertEqual(portal.requested.count("/loop"), 1)


class TestEmptyIsNotTheSameAsMissing(unittest.IsolatedAsyncioTestCase):
    """A child with no homework due really has none.

    Reporting that the same way as "no page found" sent me looking for a parser
    bug that did not exist.
    """

    async def asyncSetUp(self) -> None:
        empty_assignments = {
            "type": "page",
            "content": [
                {
                    "xtype": "mis-section",
                    "props": {
                        "title": "Assignments",
                        "hiddenRowsCount": 0,
                        "emptyText": "There are no assignments to display",
                    },
                }
            ],
        }
        self.portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.WROTHAM_SHAPED_DASHBOARD,
                "/guardians/student-profile/index/student-id/40219": pages.PROFILE_PAGE,
                "/guardians/assignments/index/student-id/40219": empty_assignments,
                "/auth/current-user-settings/format/json": pages.CURRENT_USER_SETTINGS,
            }
        )
        runner = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await runner.async_scrape()
        self.student = self.data.students["40219"]

    def test_assignments_are_empty(self) -> None:
        self.assertEqual(self.student.assignments, [])
        self.assertIn("assignments", self.student.empty_domains)

    def test_but_assignments_were_sourced(self) -> None:
        self.assertIn("assignments", self.student.sourced_domains)
        self.assertNotIn("assignments", self.student.unsourced_domains)

    def test_a_domain_with_no_page_is_unsourced(self) -> None:
        # Nothing served behaviour, so it is a genuine gap rather than "none".
        self.assertIn("behaviour", self.student.unsourced_domains)


class TestWrothamShapedPortal(unittest.IsolatedAsyncioTestCase):
    """The architecture a real school actually serves.

    Data pages are shells; a KPI panel carries the numbers; behaviour is
    date-labelled property rows; the calendar needs its object reference; and an
    action button answers with a form that must not be parsed.
    """

    async def asyncSetUp(self) -> None:
        self.portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.WROTHAM_SHAPED_DASHBOARD,
                "/guardians/student-profile/index/student-id/40219": pages.PROFILE_PAGE,
                # Attendance: a shell whose only content URL is an action.
                "/guardians/attendance/index/student-id/40219": (
                    pages.PAGE_WITH_ACTION_BUTTON_ONLY
                ),
                "/guardians/attendance-ui/log-absence/student-id/1879": (
                    pages.LOG_ABSENCE_SLIDEOVER
                ),
                # Assignments: a KPI shell whose content holds the tiles.
                "/guardians/assignments/index/student-id/40219": pages.KPI_SHELL_PAGE,
                "/guardians/student-ui/attendance-kpi/student-id/1879": (
                    pages.KPI_TILE_CONTENT
                ),
                # Behaviour: property rows, inline.
                "/guardians/behaviour/index/student-id/40219": (
                    pages.BEHAVIOUR_PROPERTY_ROWS
                ),
                # Timetable: a calendar component naming its object.
                "/guardians/calendar/index/student-id/40219": (
                    pages.CALENDAR_COMPONENT_PAGE
                ),
                "/auth/current-user-settings/format/json": pages.CURRENT_USER_SETTINGS,
                # The two per-child endpoints the dashboard links to.
                "/guardians/student/kpis/id/40219/": pages.STUDENT_KPIS,
                "/guardians/widget-data/get-calendar-data/student-id/40219/": (
                    pages.GUARDIAN_CALENDAR
                ),
            }
        )
        self.scraper = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await self.scraper.async_scrape()
        self.student = self.data.students["40219"]

    def test_the_action_form_is_not_parsed(self) -> None:
        # It is requested once, then discarded because it is a slideover.
        self.assertNotIn(
            "/guardians/attendance-ui/log-absence/student-id/1879",
            self.portal.requested,
            "an action caption should not even be followed",
        )

    def test_attendance_comes_from_the_kpi_endpoint(self) -> None:
        """The attendance percentage is published only as a KPI.

        The attendance *page* holds nothing but a Log Absence button, which is
        why this looked unavailable for several rounds.
        """
        self.assertEqual(self.student.attendance.percentage, 100.0)
        self.assertIn("attendance", self.student.sourced_domains)

    def test_behaviour_totals_come_from_the_kpi_endpoint(self) -> None:
        # The school's own headline figures, which it publishes as counts.
        self.assertEqual(self.student.behaviour_points_positive, 35.0)
        self.assertEqual(self.student.behaviour_points_negative, 0.0)

    def test_incidents_still_come_from_the_property_rows(self) -> None:
        self.assertEqual(len(self.student.behaviour_incidents), 2)

    def test_no_points_are_invented_from_an_incident_fragment(self) -> None:
        """Only a number the text calls a point counts as one.

        Taking the first number in each fragment produced a total of 203 points
        across 31 incidents, which meant nothing.
        """
        first = self.student.behaviour_incidents[0]
        self.assertEqual(first.points, 2.0)  # its text says "2 points"

    def test_lessons_come_from_the_per_child_calendar_feed(self) -> None:
        self.assertEqual(len(self.student.lessons), 2)
        self.assertEqual(self.student.lessons[0].summary, "Biology")
        self.assertEqual(self.student.lessons[0].start, datetime(2026, 9, 22, 9, 0))
        self.assertEqual(self.student.lessons[0].location, "S4")

    def test_the_ambiguous_feeds_are_not_used_once_that_works(self) -> None:
        ambiguous = [
            path
            for path in self.portal.requested
            if ("get-calendar-data" in path and "student-id/" not in path)
            or path.startswith("/calendar-entry/")
        ]
        self.assertEqual(ambiguous, [])


class TestAssignmentAndBehaviourDetail(unittest.IsolatedAsyncioTestCase):
    """The detail a parent actually asks for, end to end.

    Every list of work Arbor shows a guardian carries a class code and a
    deadline; the subject, the marking scheme and the task itself are only on the
    piece of work's own page, which the list links to. Behaviour is the mirror
    image: the page lists each incident with its type, lesson and teacher, and
    publishes no points at all.
    """

    async def asyncSetUp(self) -> None:
        self.portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.SINGLE_CHILD_DASHBOARD,
                # Deliberately no page carrying a "Year group" label: Wrotham has
                # none, and the bare-labelled profile panel is the only source.
                "/guardians/assignments/index/student-id/40219": (
                    pages.ASSIGNMENTS_DUE_SECTION
                ),
                "/guardians/student-ui/schoolwork-overview/schoolwork-id/1708": (
                    pages.ASSIGNMENT_DETAIL_PAGE
                ),
                "/guardians/student-ui/schoolwork-overview/schoolwork-id/1961": (
                    pages.ASSIGNMENT_DETAIL_COURSE_IN_DUE
                ),
                "/guardians/behaviour/index/student-id/40219": (
                    pages.BEHAVIOUR_INCIDENT_BREAKDOWN
                ),
                "/guardians/student-ui/overview/id/40219": pages.STUDENT_PROFILE_PANEL,
                "/auth/current-user-settings/format/json": pages.CURRENT_USER_SETTINGS,
            }
        )
        self.scraper = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await self.scraper.async_scrape()
        self.student = self.data.students["40219"]

    def test_every_assignment_is_named_with_its_deadline(self) -> None:
        by_title = {item.title: item for item in self.student.assignments}
        self.assertEqual(
            set(by_title), {"Term 1 - Task 1", "Cell biology", "Stage evaluation"}
        )
        self.assertEqual(by_title["Term 1 - Task 1"].due, datetime(2026, 9, 24))

    def test_the_subject_is_read_from_the_work_s_own_page(self) -> None:
        task = next(
            item for item in self.student.assignments if item.title == "Term 1 - Task 1"
        )
        self.assertEqual(task.subject, "English Language KS4")
        self.assertEqual(task.class_code, "9En4")
        self.assertEqual(task.submission_type, "Physical/Other")
        self.assertIn("Metaphor", task.instructions or "")

    def test_an_unlinked_assignment_keeps_the_class_code_as_its_subject(self) -> None:
        # Better than nothing, and honest about what the page said.
        submitted = next(
            item for item in self.student.assignments if item.title == "Stage evaluation"
        )
        self.assertEqual(submitted.subject, "9D/Dr")
        self.assertTrue(submitted.is_submitted)

    def test_each_behaviour_incident_names_its_subject_and_teacher(self) -> None:
        self.assertEqual(len(self.student.behaviour_incidents), 5)
        latest = self.student.behaviour_incidents[0]
        self.assertEqual(latest.kind, "Motivation")
        self.assertEqual(latest.subject, "Maths KS4")
        self.assertEqual(latest.staff, "Mr Fuller")
        self.assertEqual(latest.polarity, "positive")

    def test_behaviour_totals_say_which_period_they_cover(self) -> None:
        self.assertEqual(self.student.behaviour_totals["positive"]["Autumn"], 35.0)
        self.assertEqual(self.student.behaviour_totals["positive"]["Lifetime"], 129.0)

    def test_the_profile_panel_is_read_wherever_it_was_loaded(self) -> None:
        """Arbor loads the Form/Year/House/Tutor panel as content of other pages.

        Reading it only from the page discovery called the profile reported no
        year group at a school whose profile panel states it plainly.
        """
        self.assertEqual(self.student.year_group, "9")
        self.assertEqual(self.student.form_group, "9X1")
        self.assertEqual(self.student.house, "Wimbledon")
        self.assertEqual(self.student.tutor, "Miss Blamire")

    def test_behaviour_counts_as_sourced_without_a_kpi_endpoint(self) -> None:
        self.assertIn("behaviour", self.student.sourced_domains)

    def test_the_headline_is_the_year_not_the_lifetime(self) -> None:
        """A school with no KPI panel still gets a number, and the right one.

        Reading the first total on the page would report 129 where the portal
        itself shows 35.
        """
        self.assertEqual(self.student.behaviour_points_positive, 35.0)
        self.assertEqual(self.student.behaviour_points_negative, 1.0)
        self.assertEqual(self.student.behaviour_points_net, 34.0)


class TestAttendanceDetail(unittest.IsolatedAsyncioTestCase):
    """Individual registration sessions, and the periods the KPI tile compares.

    A percentage says a child was in; it cannot say which morning they were not.
    """

    async def asyncSetUp(self) -> None:
        self.portal = FakePortal(
            {
                "/guardians/home-ui/dashboard": pages.SINGLE_CHILD_DASHBOARD,
                "/guardians/attendance/index/student-id/40219": (
                    pages.ATTENDANCE_BY_DATE_PAGE
                ),
                "/guardians/student/kpis/id/40219/": (
                    pages.STUDENT_KPIS_WITH_COMPARISONS
                ),
                "/auth/current-user-settings/format/json": pages.CURRENT_USER_SETTINGS,
            }
        )
        self.scraper = scraper_module.ArborScraper(self.portal.fetch, self.portal.fetch)
        self.data = await self.scraper.async_scrape()
        self.student = self.data.students["40219"]

    def test_every_session_is_reported(self) -> None:
        self.assertEqual(len(self.student.attendance_marks), 10)

    def test_a_particular_absence_can_be_named(self) -> None:
        absences = [mark for mark in self.student.attendance_marks if mark.is_absence]
        self.assertEqual(
            [(mark.on.isoformat(), mark.session, mark.mark) for mark in absences],
            [
                ("2026-09-17", "PM", "Unauthorised Absence"),
                ("2026-09-17", "AM", "Illness"),
            ],
        )

    def test_the_session_counts_are_filled_in_from_the_marks(self) -> None:
        # These were all None before: the summary page states a percentage and
        # the individual sessions were never read.
        self.assertEqual(self.student.attendance.authorised_absences, 1)
        self.assertEqual(self.student.attendance.unauthorised_absences, 1)
        self.assertEqual(self.student.attendance.late_sessions, 1)

    def test_the_schools_own_percentage_still_wins(self) -> None:
        """The KPI headline is the school's figure and is not recomputed.

        The marks give 66.67% over the sessions listed; the school says 100% over
        the year. Overwriting its own headline with our arithmetic would be wrong.
        """
        self.assertEqual(self.student.attendance.percentage, 100.0)

    def test_the_kpi_tile_periods_are_kept(self) -> None:
        self.assertEqual(
            self.student.attendance.by_period, {"Year": 100.0, "Last 4 weeks": 96.0}
        )

    def test_the_pdf_certificate_is_never_fetched(self) -> None:
        """The page links a PDF, and a PDF is not page data.

        Following it fetched the file, and decoding it as text raised
        UnicodeDecodeError out of the HTTP layer -- which is not one of the
        errors a page fetch is allowed to fail with, so it took down every
        entity for every child instead of skipping one page.
        """
        self.assertNotIn(pages.ATTENDANCE_CERTIFICATE_URL, self.portal.requested)
        self.assertFalse(
            [path for path in self.portal.requested if "download" in path],
            "no download route should be requested",
        )

    def test_the_sessions_are_still_read_from_that_page(self) -> None:
        # Skipping the certificate must not skip the page that links it.
        self.assertEqual(len(self.student.attendance_marks), 10)

    def test_last_terms_behaviour_comes_from_the_tile(self) -> None:
        # The behaviour page states this term, this year and lifetime. Only the
        # KPI tile carries the term before.
        self.assertEqual(self.student.behaviour_totals["positive"]["Last term"], 32.0)


if __name__ == "__main__":
    unittest.main()
