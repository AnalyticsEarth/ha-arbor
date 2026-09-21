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

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    async def fetch(self, path: str) -> object:
        self.requested.append(path)
        for route, payload in self.routes.items():
            if path.startswith(route):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise errors.ArborNotAvailableError(f"no such page: {path}")


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

    def test_reads_the_school_name(self) -> None:
        self.assertEqual(self.data.school_name, "Example School")

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


if __name__ == "__main__":
    unittest.main()
