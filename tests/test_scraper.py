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
        self.assertEqual(
            sibling_paths, ["/guardians/student-profile/index/student-id/40855"]
        )

    def test_no_page_is_fetched_twice(self) -> None:
        # A first-child page fetched again would mean it was read for both.
        self.assertEqual(
            len(self.portal.requested), len(set(self.portal.requested))
        )

    def test_shared_calendar_feeds_are_not_used_with_siblings(self) -> None:
        # School-wide notices are fine; a calendar with no student id is not.
        calendar_requests = [
            path
            for path in self.portal.requested
            if "calendar-data" in path or path.startswith("/calendar-entry/")
        ]
        self.assertEqual(calendar_requests, [])


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
            },
            posts={"/calendar-entry/list-static/format/json/": pages.CALENDAR_POST_RESPONSE},
        )
        self.scraper = scraper_module.ArborScraper(
            self.portal.fetch, self.portal.fetch, self.portal.post
        )
        self.data = await self.scraper.async_scrape()
        self.student = self.data.students["40219"]

    def test_the_action_form_is_not_parsed(self) -> None:
        # It is requested once, then discarded because it is a slideover.
        self.assertNotIn(
            "/guardians/attendance-ui/log-absence/student-id/1879",
            self.portal.requested,
            "an action caption should not even be followed",
        )

    def test_attendance_comes_from_the_kpi_tile(self) -> None:
        self.assertEqual(self.student.attendance.percentage, 96.4)

    def test_behaviour_comes_from_the_property_rows(self) -> None:
        self.assertEqual(len(self.student.behaviour_incidents), 2)
        self.assertEqual(self.student.behaviour_points_positive, 2.0)
        self.assertEqual(self.student.behaviour_points_negative, 1.0)
        self.assertEqual(self.student.behaviour_points_net, 1.0)

    def test_the_calendar_is_posted_with_an_object_filter(self) -> None:
        import json

        self.assertEqual(len(self.portal.posted), 1)
        path, body = self.portal.posted[0]
        self.assertEqual(path, "/calendar-entry/list-static/format/json/")
        params = json.loads(body)["action_params"]
        self.assertEqual(
            params["filters"],
            [
                {
                    "field_name": "object",
                    "value": {"_objectTypeId": 43, "_objectId": 1879},
                }
            ],
        )
        self.assertIn("startDate", params)
        self.assertIn("endDate", params)

    def test_lessons_come_from_that_response(self) -> None:
        self.assertEqual(len(self.student.lessons), 3)
        self.assertEqual(self.student.lessons[0].summary, "Biology")

    def test_the_generic_feeds_are_not_used_once_the_post_works(self) -> None:
        generic = [
            path
            for path in self.portal.requested
            if "get-calendar-data" in path or path.startswith("/calendar-entry/")
        ]
        self.assertEqual(generic, [])


if __name__ == "__main__":
    unittest.main()
