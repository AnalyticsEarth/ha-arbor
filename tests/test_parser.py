"""Tests for the Arbor component-tree parser."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import const, parser  # noqa: E402
from fixtures import pages  # noqa: E402


class TestPrimitives(unittest.TestCase):
    """Tables, metrics and links are the base the extractors build on."""

    def test_finds_grid_keyed_by_data_index(self) -> None:
        tables = parser.find_tables(pages.ASSIGNMENTS_PAGE)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table.title, "Assignments")
        self.assertIn("Assignment", table.columns)
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(table.rows[0]["Assignment"], "Photosynthesis worksheet")
        self.assertEqual(table.rows[0]["Subject"], "Biology")

    def test_finds_grid_with_positional_rows(self) -> None:
        tables = parser.find_tables(pages.BEHAVIOUR_PAGE)
        grid = next(table for table in tables if table.title == "Behaviour incidents")
        self.assertEqual(grid.rows[0]["Date"], "21/09/2026")
        self.assertEqual(grid.rows[0]["Points"], "2")
        self.assertEqual(grid.rows[1]["Comment"], "Arrived 8 minutes late")

    def test_finds_labelled_metrics(self) -> None:
        metrics = {
            metric.label: metric.value for metric in parser.find_metrics(pages.ATTENDANCE_PAGE)
        }
        self.assertEqual(metrics["Attendance"], "96.4%")
        self.assertEqual(metrics["Unauthorised absences"], "2")

    def test_finds_links(self) -> None:
        links = {link.text: link.url for link in parser.find_links(pages.GUARDIAN_DASHBOARD)}
        self.assertEqual(
            links["Attendance"], "/guardians/attendance/index/student-id/40219"
        )

    def test_strips_html_from_values(self) -> None:
        self.assertEqual(
            parser.strip_html("<p>Bookings open on <strong>Monday</strong>.</p>"),
            "Bookings open on Monday .",
        )

    def test_walk_is_depth_limited(self) -> None:
        deep: dict = {}
        node = deep
        for _ in range(200):
            node["items"] = {}
            node = node["items"]
        # Should terminate rather than recurse to the bottom of a hostile tree.
        self.assertLessEqual(len(list(parser.walk(deep))), parser._MAX_DEPTH + 1)


class TestValueCoercion(unittest.TestCase):
    """Arbor writes the same value several ways depending on the page."""

    def test_percentages(self) -> None:
        self.assertEqual(parser.parse_percentage("96.4%"), 96.4)
        self.assertEqual(parser.parse_percentage("96.4"), 96.4)
        self.assertEqual(parser.parse_percentage("0.964"), 96.4)
        self.assertEqual(parser.parse_percentage("96"), 96)
        self.assertIsNone(parser.parse_percentage(""))

    def test_currency(self) -> None:
        self.assertEqual(parser.parse_currency("£12.45"), 12.45)
        self.assertEqual(parser.parse_currency("-£3.20"), -3.20)
        self.assertEqual(parser.parse_currency("£-3.20"), -3.20)
        self.assertEqual(parser.parse_currency("(£3.20)"), -3.20)
        self.assertEqual(parser.parse_currency("£1,204.50"), 1204.50)
        self.assertEqual(parser.parse_currency("12.45"), 12.45)

    def test_numbers(self) -> None:
        self.assertEqual(parser.parse_number("128"), 128.0)
        self.assertEqual(parser.parse_number("-14"), -14.0)
        self.assertEqual(parser.parse_int("2 points"), 2)
        self.assertIsNone(parser.parse_number(True))
        self.assertIsNone(parser.parse_number(None))

    def test_dates_in_every_dialect(self) -> None:
        expected = date(2026, 9, 21)
        for raw in (
            "2026-09-21",
            "21/09/2026",
            "21-09-2026",
            "21 Sep 2026",
            "21 September 2026",
            "Mon 21 Sep 2026",
            "21st September 2026",
            "2026-09-21T09:00:00",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parser.parse_date(raw), expected)

    def test_date_without_a_year_assumes_this_year(self) -> None:
        parsed = parser.parse_date("21 Sep")
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.month, parsed.day), (9, 21))
        self.assertEqual(parsed.year, date.today().year)

    def test_rejects_unparseable_dates(self) -> None:
        self.assertIsNone(parser.parse_date("sometime next week"))
        self.assertIsNone(parser.parse_date(""))

    def test_datetime_keeps_the_time(self) -> None:
        self.assertEqual(
            parser.parse_datetime("2026-09-21 09:30"), datetime(2026, 9, 21, 9, 30)
        )
        self.assertEqual(
            parser.parse_datetime("21/09/2026 14:05"), datetime(2026, 9, 21, 14, 5)
        )

    def test_clock_and_ranges(self) -> None:
        self.assertEqual(parser.parse_clock("09:05"), time(9, 5))
        self.assertEqual(parser.parse_clock("1:05pm"), time(13, 5))
        self.assertEqual(parser.parse_clock("12:30am"), time(0, 30))
        self.assertEqual(parser.parse_time_range("09:00 - 10:00"), (time(9), time(10)))
        self.assertEqual(parser.parse_time_range("9:00 – 10:30"), (time(9), time(10, 30)))
        self.assertEqual(
            parser.parse_time_range("1:00 - 2:15pm"), (time(13), time(14, 15))
        )
        self.assertIsNone(parser.parse_time_range("Period 1"))


class TestAssignments(unittest.TestCase):
    """Homework extraction."""

    def setUp(self) -> None:
        self.assignments = parser.extract_assignments([pages.ASSIGNMENTS_PAGE])

    def test_extracts_every_row(self) -> None:
        self.assertEqual(len(self.assignments), 3)

    def test_orders_by_due_date(self) -> None:
        self.assertEqual(
            [item.title for item in self.assignments],
            [
                "Quadratic equations problem set",
                "Macbeth Act 2 essay",
                "Photosynthesis worksheet",
            ],
        )

    def test_reads_the_fields(self) -> None:
        essay = next(item for item in self.assignments if "Macbeth" in item.title)
        self.assertEqual(essay.subject, "English")
        self.assertEqual(essay.due, datetime(2026, 9, 18))
        self.assertEqual(essay.grade, "B+")
        self.assertEqual(essay.teacher, "Mr T Hale")
        self.assertTrue(essay.is_submitted)

    def test_not_submitted_is_not_read_as_submitted(self) -> None:
        worksheet = next(item for item in self.assignments if "Photosynthesis" in item.title)
        self.assertFalse(worksheet.is_submitted)

    def test_overdue_needs_a_past_deadline_and_no_submission(self) -> None:
        maths = next(item for item in self.assignments if "Quadratic" in item.title)
        self.assertEqual(maths.due, datetime(2026, 9, 14))
        # is_overdue compares against today, so assert the rule not the date.
        self.assertEqual(maths.is_overdue, maths.due.date() < date.today())

    def test_deduplicates_repeated_rows(self) -> None:
        doubled = {
            "items": [
                pages.ASSIGNMENTS_PAGE["items"][0],
                pages.ASSIGNMENTS_PAGE["items"][0],
            ]
        }
        self.assertEqual(len(parser.extract_assignments([doubled])), 3)


class TestRecordListDialect(unittest.TestCase):
    """Pages that return records without declaring columns.

    Regression: only the declared-column dialect was understood, so a school
    whose pages return plain record lists produced entities with no data at all
    even though every page fetched successfully.
    """

    def test_assignments_from_a_record_list(self) -> None:
        items = parser.extract_assignments([pages.ASSIGNMENTS_RECORD_LIST])
        self.assertEqual(len(items), 2)
        essay = next(item for item in items if "Macbeth" in item.title)
        self.assertEqual(essay.subject, "English")
        self.assertEqual(essay.due, datetime(2026, 9, 18, 15, 30))
        self.assertEqual(essay.grade, "B+")
        self.assertEqual(essay.teacher, "Mr T Hale")
        self.assertTrue(essay.is_submitted)
        worksheet = next(item for item in items if "Photosynthesis" in item.title)
        self.assertFalse(worksheet.is_submitted)

    def test_behaviour_from_a_record_list(self) -> None:
        positive, negative, incidents = parser.extract_behaviour(
            [pages.BEHAVIOUR_RECORD_LIST]
        )
        self.assertEqual(len(incidents), 2)
        self.assertEqual(incidents[0].occurred, datetime(2026, 9, 21))
        self.assertEqual(incidents[0].subject, "Biology")
        self.assertTrue(incidents[0].is_positive)
        self.assertFalse(incidents[1].is_positive)
        self.assertEqual(positive, 2.0)
        self.assertEqual(negative, 1.0)

    def test_layout_lists_are_not_read_as_records(self) -> None:
        # Column definitions and component lists must not become tables.
        titles = [table.title for table in parser.find_tables(pages.ASSIGNMENTS_PAGE)]
        self.assertEqual(titles, ["Assignments"])

    def test_rows_are_not_reported_twice(self) -> None:
        # `store.data` is reachable both as a declared row source and as a bare
        # record list; it must be counted once.
        self.assertEqual(len(parser.extract_assignments([pages.ASSIGNMENTS_PAGE])), 3)


class TestShellPages(unittest.TestCase):
    """Guardian pages that carry a layout and load their data separately.

    Regression: every page fetched successfully and every extractor returned
    zero, because the data was never in the page to begin with.
    """

    def test_finds_the_content_url_behind_a_load_page_button(self) -> None:
        self.assertEqual(
            parser.extract_content_urls(pages.SHELL_PAGE_WITH_CONTENT_URL),
            ["/guardians/student-ui/assignments-content/student-id/1879"],
        )

    def test_finds_the_content_url_on_a_kpi_panel(self) -> None:
        self.assertEqual(
            parser.extract_content_urls(pages.KPI_SHELL_PAGE),
            ["/guardians/student-ui/attendance-kpi/student-id/1879"],
        )

    def test_the_shell_itself_yields_no_assignments(self) -> None:
        self.assertEqual(parser.extract_assignments([pages.SHELL_PAGE_WITH_CONTENT_URL]), [])

    def test_the_content_yields_them(self) -> None:
        items = parser.extract_assignments([pages.ASSIGNMENTS_CONTENT])
        self.assertEqual(len(items), 2)
        essay = next(item for item in items if "Macbeth" in item.title)
        self.assertEqual(essay.subject, "English")
        self.assertEqual(essay.grade, "B+")
        self.assertTrue(essay.is_submitted)

    def test_off_tenant_content_urls_are_ignored(self) -> None:
        tree = {
            "props": {"pageUrl": "https://evil.example/steal"},
            "content": [{"props": {"url": "//evil.example/steal"}}],
        }
        self.assertEqual(parser.extract_content_urls(tree), [])


class TestKpiTiles(unittest.TestCase):
    """KPI tiles report their number as `mainValue`."""

    def test_tiles_become_metrics(self) -> None:
        metrics = {m.label: m.value for m in parser.find_metrics(pages.KPI_TILE_CONTENT)}
        self.assertEqual(metrics["Overdue Assignments"], "1")
        self.assertEqual(metrics["Attendance this year"], "96.4%")

    def test_attendance_is_read_from_a_tile(self) -> None:
        summary = parser.extract_attendance([pages.KPI_TILE_CONTENT])
        self.assertEqual(summary.percentage, 96.4)


class TestBehaviourPropertyRows(unittest.TestCase):
    """Incidents logged as a date label plus an HTML value."""

    def setUp(self) -> None:
        self.incidents = parser.extract_behaviour_rows([pages.BEHAVIOUR_PROPERTY_ROWS])

    def test_both_rows_are_read(self) -> None:
        self.assertEqual(len(self.incidents), 2)

    def test_the_date_comes_from_the_label(self) -> None:
        self.assertEqual(self.incidents[0].occurred, date(2026, 9, 21))

    def test_the_html_is_stripped(self) -> None:
        self.assertNotIn("<", self.incidents[0].comment or "")
        self.assertIn("Excellent work", self.incidents[0].comment or "")

    def test_points_and_sign_are_read(self) -> None:
        positive = next(i for i in self.incidents if "Excellent" in (i.comment or ""))
        negative = next(i for i in self.incidents if "Late" in (i.comment or ""))
        self.assertEqual(positive.points, 2.0)
        self.assertTrue(positive.is_positive)
        self.assertEqual(negative.points, -1.0)
        self.assertFalse(negative.is_positive)

    def test_rows_without_a_date_label_are_ignored(self) -> None:
        tree = {"props": {"fieldLabel": "Form group", "value": "9X1"}}
        self.assertEqual(parser.extract_behaviour_rows([tree]), [])


class TestCalendarReferences(unittest.TestCase):
    """The calendar feed needs the object its component draws."""

    def test_the_reference_is_read(self) -> None:
        self.assertEqual(
            parser.extract_calendar_references([pages.CALENDAR_COMPONENT_PAGE]),
            [("1879", "43")],
        )

    def test_pages_without_a_calendar_yield_nothing(self) -> None:
        self.assertEqual(
            parser.extract_calendar_references([pages.PROFILE_PAGE]), []
        )


class TestFormPayloads(unittest.TestCase):
    """An action URL answers with a form, which is not the child's data."""

    def test_a_slideover_is_recognised(self) -> None:
        self.assertTrue(parser.is_form_payload(pages.LOG_ABSENCE_SLIDEOVER))

    def test_a_page_is_not(self) -> None:
        self.assertFalse(parser.is_form_payload(pages.CALENDAR_COMPONENT_PAGE))
        self.assertFalse(parser.is_form_payload(pages.ASSIGNMENTS_CONTENT))

    def test_an_action_caption_is_not_followed(self) -> None:
        self.assertEqual(
            parser.extract_content_urls(pages.PAGE_WITH_ACTION_BUTTON_ONLY), []
        )

    def test_a_genuine_caption_still_is(self) -> None:
        self.assertEqual(
            parser.extract_content_urls(pages.SHELL_PAGE_WITH_CONTENT_URL),
            ["/guardians/student-ui/assignments-content/student-id/1879"],
        )


class TestDashboardSectionRows(unittest.TestCase):
    """The dashboard is where work due and balances are actually listed."""

    def setUp(self) -> None:
        self.rows = parser.extract_section_rows([pages.DASHBOARD_WITH_SECTIONS])

    def test_each_row_knows_its_section(self) -> None:
        sections = {row.section for row in self.rows}
        self.assertEqual(
            sections, {"Notices", "Assignments that are due", "Accounts"}
        )

    def test_rows_are_not_attributed_to_an_outer_section_as_well(self) -> None:
        # One notice, four under assignments, one account: each counted once,
        # not also against the enclosing layout column.
        self.assertEqual(len(self.rows), 6)

    def test_assignments_are_parsed_from_their_row_text(self) -> None:
        items = parser.extract_assignments_from_sections(self.rows)
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first.title, "Flash Cards")
        self.assertEqual(first.subject, "9Ma3")
        self.assertEqual(first.due, datetime(2026, 9, 20))
        self.assertTrue(first.is_submitted)

    def test_the_status_comes_from_the_description(self) -> None:
        items = parser.extract_assignments_from_sections(self.rows)
        waiting = next(item for item in items if item.title == "Cell biology")
        self.assertEqual(waiting.status, "Waiting for student to submit")
        self.assertFalse(waiting.is_submitted)

    def test_a_row_that_is_not_an_assignment_is_skipped(self) -> None:
        titles = [
            item.title for item in parser.extract_assignments_from_sections(self.rows)
        ]
        self.assertNotIn("View all assignments", titles)

    def test_rows_outside_an_assignments_section_are_ignored(self) -> None:
        # The Notices row also has no "(Due ...)", but the section gate is first.
        items = parser.extract_assignments_from_sections(self.rows)
        self.assertFalse(any("hearing" in item.title for item in items))

    def test_the_balance_comes_from_the_description(self) -> None:
        accounts = parser.extract_accounts_from_sections(self.rows)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].name, "Meals")
        self.assertEqual(accounts[0].balance, 4.15)

    def test_a_row_without_a_balance_is_not_an_account(self) -> None:
        rows = [parser.SectionRow(section="Activities", text="X: Trips")]
        self.assertEqual(parser.extract_accounts_from_sections(rows), [])


class TestKpiEndpoint(unittest.TestCase):
    """The per-child KPI list: a caption plus a number inside HTML."""

    def setUp(self) -> None:
        self.kpis = parser.extract_kpis([pages.STUDENT_KPIS])

    def test_all_four_are_read(self) -> None:
        self.assertEqual(len(self.kpis), 4)

    def test_attendance_is_found_by_its_caption(self) -> None:
        self.assertEqual(parser.attendance_from_kpis(self.kpis), 100.0)

    def test_behaviour_totals_are_found_by_their_captions(self) -> None:
        positive, negative = parser.behaviour_from_kpis(self.kpis)
        self.assertEqual(positive, 35.0)
        self.assertEqual(negative, 0.0)

    def test_nothing_is_invented_when_there_are_no_kpis(self) -> None:
        self.assertIsNone(parser.attendance_from_kpis([]))
        self.assertEqual(parser.behaviour_from_kpis([]), (None, None))


class TestGuardianCalendarFeed(unittest.TestCase):
    """Events keyed start_datetime/end_datetime, wrapped in field objects."""

    def test_lessons_are_read_with_their_times_and_rooms(self) -> None:
        lessons = parser.extract_lessons_from_calendar(pages.GUARDIAN_CALENDAR)
        self.assertEqual(len(lessons), 2)
        self.assertEqual(lessons[0].summary, "Biology")
        self.assertEqual(lessons[0].start, datetime(2026, 9, 22, 9, 0))
        self.assertEqual(lessons[0].end, datetime(2026, 9, 22, 10, 0))
        self.assertEqual(lessons[0].location, "S4")


class TestPointsAreNotInvented(unittest.TestCase):
    """Only a number the text calls a point is a point."""

    def test_a_bare_number_is_not_points(self) -> None:
        tree = {
            "props": {
                "fieldLabel": "21 Sep 2026",
                "value": "Room C1.5 with Mr Hale, period 3",
            }
        }
        incidents = parser.extract_behaviour_rows([tree])
        self.assertEqual(len(incidents), 1)
        self.assertIsNone(incidents[0].points)

    def test_a_stated_point_value_is_read(self) -> None:
        tree = {
            "props": {
                "fieldLabel": "21 Sep 2026",
                "value": "Excellent work &nbsp; 2 points &nbsp; Biology",
            }
        }
        self.assertEqual(parser.extract_behaviour_rows([tree])[0].points, 2.0)


class TestWrappedNavigationLinks(unittest.TestCase):
    """Navigation fields arrive as {"value": ...}, not as bare strings."""

    def test_wrapped_urls_are_read(self) -> None:
        links = {link.text: link.url for link in parser.find_links(
            pages.SHELL_PAGE_WITH_CONTENT_URL
        )}
        self.assertEqual(
            links["Behaviour"],
            "/guardians/behaviour-ui/student-behaviour/student-id/1879",
        )
        self.assertEqual(
            links["Attendance"],
            "/guardians/student-ui/recent-attendance/student-id/1879",
        )

    def test_those_navigation_routes_classify(self) -> None:
        found = parser.classify_pages(
            [pages.SHELL_PAGE_WITH_CONTENT_URL], const.DOMAIN_KEYWORDS
        )
        self.assertIn("Behaviour", found[const.DATA_BEHAVIOUR])
        self.assertIn("Attendance", found[const.DATA_ATTENDANCE])


class TestBehaviour(unittest.TestCase):
    """Behaviour points and incidents."""

    def test_reads_totals_and_incidents(self) -> None:
        positive, negative, incidents = parser.extract_behaviour([pages.BEHAVIOUR_PAGE])
        self.assertEqual(positive, 128.0)
        self.assertEqual(negative, 14.0)
        self.assertEqual(len(incidents), 2)

    def test_newest_incident_first(self) -> None:
        _, _, incidents = parser.extract_behaviour([pages.BEHAVIOUR_PAGE])
        self.assertEqual(incidents[0].occurred, datetime(2026, 9, 21))
        self.assertEqual(incidents[0].subject, "Biology")
        self.assertTrue(incidents[0].is_positive)
        self.assertFalse(incidents[1].is_positive)

    def test_sums_incidents_when_no_totals_are_published(self) -> None:
        grid_only = {"items": [pages.BEHAVIOUR_PAGE["items"][1]]}
        positive, negative, incidents = parser.extract_behaviour([grid_only])
        self.assertEqual(len(incidents), 2)
        self.assertEqual(positive, 2.0)
        self.assertEqual(negative, 1.0)


class TestAttendance(unittest.TestCase):
    """Attendance summary."""

    def test_reads_percentage_and_counts(self) -> None:
        summary = parser.extract_attendance([pages.ATTENDANCE_PAGE])
        self.assertEqual(summary.percentage, 96.4)
        self.assertEqual(summary.present_sessions, 241)
        self.assertEqual(summary.authorised_absences, 6)
        self.assertEqual(summary.unauthorised_absences, 2)
        self.assertEqual(summary.late_sessions, 3)

    def test_authorised_is_not_confused_with_unauthorised(self) -> None:
        tree = {
            "items": [
                {"label": "Unauthorised absences", "value": "9"},
                {"label": "Authorised absences", "value": "1"},
            ]
        }
        summary = parser.extract_attendance([tree])
        self.assertEqual(summary.unauthorised_absences, 9)
        self.assertEqual(summary.authorised_absences, 1)

    def test_missing_data_leaves_fields_empty(self) -> None:
        summary = parser.extract_attendance([{"items": []}])
        self.assertIsNone(summary.percentage)


class TestTimetable(unittest.TestCase):
    """Lessons from both the grid and the calendar endpoint."""

    def test_reads_lessons_from_a_grid(self) -> None:
        lessons = parser.extract_lessons_from_tables(
            [pages.TIMETABLE_PAGE], on_day=date(2026, 9, 22)
        )
        self.assertEqual(len(lessons), 2)
        first = lessons[0]
        self.assertEqual(first.summary, "Biology")
        self.assertEqual(first.start, datetime(2026, 9, 22, 9, 0))
        self.assertEqual(first.end, datetime(2026, 9, 22, 10, 0))
        self.assertEqual(first.location, "S4")
        self.assertEqual(first.teacher, "Mrs J Okafor")

    def test_reads_events_from_the_calendar_endpoint(self) -> None:
        lessons = parser.extract_lessons_from_calendar(pages.CALENDAR_ENDPOINT)
        summaries = [lesson.summary for lesson in lessons]
        self.assertEqual(summaries, ["Biology", "Mathematics", "INSET day"])
        self.assertEqual(lessons[0].end, datetime(2026, 9, 22, 10, 0))
        self.assertEqual(lessons[2].all_day_on, date(2026, 9, 28))
        self.assertIsNone(lessons[2].start)

    def test_deduplicates_repeated_events(self) -> None:
        doubled = {"items": pages.CALENDAR_ENDPOINT["items"] * 2}
        self.assertEqual(len(parser.extract_lessons_from_calendar(doubled)), 3)


class TestOtherDomains(unittest.TestCase):
    """Progress, meal accounts and notices."""

    def test_grades(self) -> None:
        grades = parser.extract_grades([pages.PROGRESS_PAGE])
        self.assertEqual(len(grades), 2)
        biology = next(item for item in grades if item.subject == "Biology")
        self.assertEqual(biology.value, "7")
        self.assertEqual(biology.target, "8")
        self.assertEqual(biology.assessment, "Autumn 1")

    def test_accounts_including_a_negative_balance(self) -> None:
        accounts = {
            account.name: account.balance
            for account in parser.extract_accounts([pages.MEALS_PAGE])
        }
        self.assertEqual(accounts["Meals"], -3.20)
        self.assertEqual(accounts["Trips"], 15.00)

    def test_notices_are_newest_first_and_html_free(self) -> None:
        notices = parser.extract_notices([pages.NOTICES_ENDPOINT])
        self.assertEqual(len(notices), 2)
        self.assertEqual(notices[0].title, "Year 9 parents' evening")
        self.assertNotIn("<", notices[0].body or "")

    def test_profile_fields(self) -> None:
        fields = parser.extract_profile_fields([pages.PROFILE_PAGE])
        self.assertEqual(fields["year group"], "Year 9")
        self.assertEqual(fields["form group"], "9BQ")


class TestStudentDiscovery(unittest.TestCase):
    """Finding the children on the dashboard."""

    def test_finds_both_children_with_their_names(self) -> None:
        refs = parser.extract_student_refs([pages.GUARDIAN_DASHBOARD])
        self.assertEqual(
            [(ref.student_id, ref.name) for ref in refs],
            [("40219", "Amelia Example"), ("40855", "Oliver Example")],
        )

    def test_ignores_generic_link_captions(self) -> None:
        refs = parser.extract_student_refs([pages.GUARDIAN_DASHBOARD])
        self.assertNotIn("View Student Profile", [ref.name for ref in refs])

    def test_lesson_and_notice_links_are_not_children(self) -> None:
        """Regression: "Current/Next/Previous lesson" became three children.

        Those links carry a bare ``/id/<n>``, which is not a student id, and
        their captions look superficially like two-word names.
        """
        refs = parser.extract_student_refs([pages.DASHBOARD_WITH_LESSON_LINKS])
        self.assertEqual(
            [(ref.student_id, ref.name) for ref in refs], [("40219", "Amelia Example")]
        )

    def test_bare_id_urls_carry_no_student_id(self) -> None:
        for url in (
            "/guardians/calendar-entry/view-event/id/8814023",
            "/guardians/news-story/view/id/55012",
            "/guardians/payments/invoice/id/771",
        ):
            with self.subTest(url=url):
                self.assertIsNone(parser.student_id_in_url(url))

    def test_explicit_student_ids_are_recognised(self) -> None:
        for url, expected in (
            ("/guardians/student-profile/index/student-id/40219", "40219"),
            ("/guardians/attendance/index/student_id/40219", "40219"),
            ("/guardians/students/40219/overview", "40219"),
            ("/students/view/40219", "40219"),
        ):
            with self.subTest(url=url):
                self.assertEqual(parser.student_id_in_url(url), expected)

    def test_portal_vocabulary_is_never_a_name(self) -> None:
        for caption in (
            "Next lesson",
            "Current lesson",
            "Previous lesson",
            "View Student Profile",
            "My assignments",
            "Meal balance",
            "Report cards",
            "This week",
        ):
            with self.subTest(caption=caption):
                self.assertFalse(parser._looks_like_name(caption))

    def test_real_names_still_pass(self) -> None:
        for caption in (
            "Amelia Example",
            "Oliver Example",
            "Siân O'Brien",
            "Jean-Luc Picard",
            "Mary Jane Watson-Parker",
        ):
            with self.subTest(caption=caption):
                self.assertTrue(parser._looks_like_name(caption))

    def test_a_child_is_found_even_without_a_usable_caption(self) -> None:
        tree = {
            "items": [
                {
                    "text": "View Student Profile",
                    "url": "/guardians/student-profile/index/student-id/40219",
                }
            ]
        }
        refs = parser.extract_student_refs([tree])
        self.assertEqual([(r.student_id, r.name) for r in refs], [("40219", "Student 40219")])

    def test_returns_nothing_when_there_are_no_student_links(self) -> None:
        self.assertEqual(parser.extract_student_refs([{"items": []}]), [])


class TestPageClassification(unittest.TestCase):
    """Sorting discovered links into data domains."""

    def setUp(self) -> None:
        self.pages = parser.classify_pages(
            [pages.GUARDIAN_DASHBOARD], const.DOMAIN_KEYWORDS
        )

    def test_files_each_link_under_its_domain(self) -> None:
        self.assertIn("Attendance", self.pages[const.DATA_ATTENDANCE])
        self.assertIn("Behaviour", self.pages[const.DATA_BEHAVIOUR])
        self.assertIn("Assignments", self.pages[const.DATA_ASSIGNMENTS])
        self.assertIn("Calendar", self.pages[const.DATA_TIMETABLE])

    def test_does_not_file_a_name_that_contains_a_keyword(self) -> None:
        # "Oliver Example" contains "exam"; it is a child, not an exams page.
        for entries in self.pages.values():
            self.assertNotIn("Oliver Example", entries)
            self.assertNotIn("Amelia Example", entries)

    def test_whole_word_matching(self) -> None:
        self.assertTrue(parser.caption_mentions("Exams", "exam"))
        self.assertTrue(parser.caption_mentions("My exam timetable", "exam"))
        self.assertTrue(parser.caption_mentions("Examinations", "examination"))
        self.assertFalse(parser.caption_mentions("Examinations", "exam"))
        self.assertFalse(parser.caption_mentions("Example page", "exam"))
        self.assertFalse(parser.caption_mentions("Oliver Example", "exam"))

    def test_exam_pages_are_still_classified(self) -> None:
        # "exam" no longer matches "Examinations", but "examination" does, so the
        # domain still resolves for the wording Arbor actually uses.
        classified = parser.classify_pages(
            [{"items": [{"text": "Examinations", "url": "/guardians/exams/index"}]}],
            const.DOMAIN_KEYWORDS,
        )
        self.assertIn("Examinations", classified[const.DATA_EXAMINATIONS])

    def test_plurals_still_match(self) -> None:
        self.assertTrue(parser.caption_mentions("Accounts", "account"))
        self.assertTrue(parser.caption_mentions("Report cards", "report card"))
        self.assertTrue(parser.caption_mentions("Assignments", "assignment"))
        self.assertTrue(parser.caption_mentions("Notices", "notice"))

    def test_does_not_match_inside_a_longer_word(self) -> None:
        self.assertFalse(parser.caption_mentions("Marksheet", "mark"))
        self.assertFalse(parser.caption_mentions("Reporter", "report"))

    def test_skips_external_links(self) -> None:
        for entries in self.pages.values():
            for url in entries.values():
                self.assertTrue(url.startswith("/"), url)


class TestPageFilteringForSiblings(unittest.TestCase):
    """A discovered URL must not be read as a sibling's data."""

    def setUp(self) -> None:
        self.pages = parser.classify_pages(
            [pages.GUARDIAN_DASHBOARD], const.DOMAIN_KEYWORDS
        )

    def test_keeps_only_the_matching_child(self) -> None:
        # The dashboard's nav links all carry the first child's id.
        for_first = parser.filter_pages_for_student(
            self.pages, "40219", keep_unscoped=False
        )
        self.assertIn("Attendance", for_first[const.DATA_ATTENDANCE])

        for_sibling = parser.filter_pages_for_student(
            self.pages, "40855", keep_unscoped=False
        )
        self.assertEqual(for_sibling, {})

    def test_unscoped_urls_are_kept_only_when_asked(self) -> None:
        unscoped = {const.DATA_ATTENDANCE: {"Attendance": "/guardians/attendance/index"}}
        self.assertEqual(
            parser.filter_pages_for_student(unscoped, "40219", keep_unscoped=True),
            unscoped,
        )
        self.assertEqual(
            parser.filter_pages_for_student(unscoped, "40219", keep_unscoped=False), {}
        )

    def test_unscoped_urls_survive_alongside_a_mismatch(self) -> None:
        mixed = {
            const.DATA_ATTENDANCE: {
                "Attendance": "/guardians/attendance/index",
                "Sibling attendance": "/guardians/attendance/index/student-id/999",
            }
        }
        result = parser.filter_pages_for_student(mixed, "40219", keep_unscoped=True)
        self.assertEqual(
            result, {const.DATA_ATTENDANCE: {"Attendance": "/guardians/attendance/index"}}
        )

class TestLabelledHtmlFields(unittest.TestCase):
    """Arbor packs several named fields into one row's HTML value."""

    def test_reads_each_bold_label_as_a_field(self) -> None:
        fields = parser.parse_labelled_html(
            "<div><b>Behaviour:</b> Motivation</div><div><b>Recorded by:</b> Mr Fuller</div>"
        )
        self.assertEqual(fields, {"Behaviour": "Motivation", "Recorded by": "Mr Fuller"})

    def test_an_empty_field_is_left_out(self) -> None:
        # Arbor emits "<b>Narrative:</b> " when the teacher wrote no comment, and
        # flattening the row to text runs that straight into the next label.
        fields = parser.parse_labelled_html(
            "<div><b>Narrative:</b> </div><div><b>Recorded by:</b> Mr Burton</div>"
        )
        self.assertEqual(fields, {"Recorded by": "Mr Burton"})

    def test_plain_text_has_no_fields(self) -> None:
        self.assertEqual(parser.parse_labelled_html("35 positive incidents"), {})
        self.assertEqual(parser.parse_labelled_html(None), {})

    def test_a_value_may_itself_contain_a_colon(self) -> None:
        fields = parser.parse_labelled_html("<div><b>Event:</b> Maths KS4: 9Ma3</div>")
        self.assertEqual(fields["Event"], "Maths KS4: 9Ma3")


class TestSectionRowHeadings(unittest.TestCase):
    """A row's meaning depends on both headings above it."""

    def test_subsection_does_not_overwrite_its_section(self) -> None:
        rows = parser.extract_section_rows([pages.BEHAVIOUR_INCIDENT_BREAKDOWN])
        breakdown = [row for row in rows if row.subsection.endswith("Breakdown")]
        self.assertTrue(breakdown)
        # "mis-subsection" contains the word "section"; letting it win loses the
        # polarity, which only the outer heading states.
        self.assertTrue(any(row.section == "Positive Incidents" for row in breakdown))
        self.assertTrue(any(row.section == "Negative Incidents" for row in breakdown))

    def test_mentions_checks_both_headings(self) -> None:
        row = parser.SectionRow(section="Positive Incidents", text="x", subsection="Breakdown")
        self.assertTrue(row.mentions("positive"))
        self.assertTrue(row.mentions("breakdown"))
        self.assertFalse(row.mentions("negative"))

    def test_identical_rows_in_one_page_are_all_kept(self) -> None:
        rows = parser.extract_section_rows([pages.BEHAVIOUR_INCIDENT_BREAKDOWN])
        respect = [row for row in rows if "Respect" in row.text]
        # Two Respect incidents, same day, same teacher, same lesson: two
        # incidents. Deduplicating them undercounted a term's total by four.
        self.assertEqual(len(respect), 2)

    def test_the_same_page_read_twice_is_not_counted_twice(self) -> None:
        once = parser.extract_section_rows([pages.BEHAVIOUR_INCIDENT_BREAKDOWN])
        twice = parser.extract_section_rows(
            [pages.BEHAVIOUR_INCIDENT_BREAKDOWN, pages.BEHAVIOUR_INCIDENT_BREAKDOWN]
        )
        self.assertEqual(len(once), len(twice))


class TestBehaviourIncidentDetail(unittest.TestCase):
    """Each incident's date, type, subject, teacher and narrative."""

    def setUp(self) -> None:
        self.rows = parser.extract_section_rows([pages.BEHAVIOUR_INCIDENT_BREAKDOWN])
        self.incidents = parser.extract_behaviour_incidents(self.rows)

    def test_reads_every_incident(self) -> None:
        self.assertEqual(len(self.incidents), 5)

    def test_newest_first(self) -> None:
        self.assertEqual(self.incidents[0].occurred, date(2026, 9, 21))

    def test_carries_the_fields_behind_the_number(self) -> None:
        latest = self.incidents[0]
        self.assertEqual(latest.kind, "Motivation")
        self.assertEqual(latest.subject, "Maths KS4")
        self.assertEqual(latest.class_code, "9Ma3")
        self.assertEqual(latest.event, "Maths KS4: 9Ma3")
        self.assertEqual(latest.staff, "Mr Fuller")
        self.assertEqual(latest.comment, "Good work completed in lesson")
        self.assertEqual(latest.polarity, "positive")
        self.assertTrue(latest.is_positive)

    def test_polarity_comes_from_the_section_not_the_wording(self) -> None:
        negative = [item for item in self.incidents if item.polarity == "negative"]
        self.assertEqual(len(negative), 1)
        # "Disruption" is in no keyword list; the heading above it is the source.
        self.assertEqual(negative[0].kind, "Disruption")
        self.assertTrue(negative[0].is_negative)
        self.assertFalse(negative[0].is_positive)

    def test_a_negative_incidents_points_are_signed(self) -> None:
        negative = next(item for item in self.incidents if item.polarity == "negative")
        self.assertEqual(negative.points, -2)

    def test_points_are_not_invented_when_none_are_published(self) -> None:
        # Wrotham publishes behaviour types and no points at all. Reading the
        # first number in each row gave a total of 203 that meant nothing.
        self.assertIsNone(self.incidents[0].points)

    def test_a_non_lesson_event_yields_no_subject(self) -> None:
        assembly = next(item for item in self.incidents if item.kind == "Communication")
        self.assertEqual(assembly.event, "Open Evening Tour Guides")
        self.assertIsNone(assembly.class_code)

    def test_an_empty_narrative_is_absent_not_blank(self) -> None:
        respect = next(item for item in self.incidents if item.kind == "Respect")
        self.assertIsNone(respect.comment)

    def test_summary_rows_are_not_read_as_incidents(self) -> None:
        self.assertFalse(any("incidents" in (item.kind or "") for item in self.incidents))

    def test_totals_are_kept_per_polarity_and_period(self) -> None:
        totals = parser.extract_behaviour_totals(self.rows)
        self.assertEqual(
            totals["positive"], {"Lifetime": 129.0, "2026/2027": 35.0, "Autumn": 35.0}
        )
        self.assertEqual(totals["negative"]["Lifetime"], 4.0)
        self.assertEqual(totals["neutral"]["Lifetime"], 2.0)

    def test_the_headline_total_prefers_the_academic_year(self) -> None:
        totals = parser.extract_behaviour_totals(self.rows)
        self.assertEqual(parser.headline_behaviour_total(totals["positive"]), 35.0)

    def test_the_headline_falls_back_past_lifetime(self) -> None:
        self.assertEqual(
            parser.headline_behaviour_total({"Lifetime": 129.0, "Autumn": 35.0}), 35.0
        )
        # Lifetime alone is better than nothing, but only as a last resort.
        self.assertEqual(parser.headline_behaviour_total({"Lifetime": 129.0}), 129.0)
        self.assertIsNone(parser.headline_behaviour_total({}))

    def test_a_breakdown_row_is_not_a_total(self) -> None:
        totals = parser.extract_behaviour_totals(self.rows)
        # Every recorded period, and no incident dates among them.
        for periods in totals.values():
            self.assertEqual(set(periods), {"Lifetime", "2026/2027", "Autumn"})


class TestAssignmentDetail(unittest.TestCase):
    """The assignment's own page names the subject the list only codes."""

    def setUp(self) -> None:
        self.details = parser.extract_assignment_details(
            [pages.ASSIGNMENT_DETAIL_PAGE, pages.ASSIGNMENT_DETAIL_COURSE_IN_DUE]
        )

    def test_reads_both_pages(self) -> None:
        self.assertEqual(set(self.details), {"term 1 - task 1", "cell biology"})

    def test_carries_every_labelled_field(self) -> None:
        detail = self.details["term 1 - task 1"]
        self.assertEqual(detail.course, "English Language KS4: 9En4")
        self.assertEqual(detail.marking, "No mark")
        self.assertEqual(detail.status, "Waiting for student to submit")
        self.assertEqual(detail.submission_type, "Physical/Other")
        self.assertIn("Perspective", detail.instructions or "")

    def test_a_page_that_is_not_an_assignment_is_ignored(self) -> None:
        self.assertEqual(parser.extract_assignment_details([pages.PROFILE_PAGE]), {})
        self.assertEqual(parser.extract_assignment_details([pages.BEHAVIOUR_PAGE]), {})

    def test_enrichment_replaces_the_class_code_with_the_subject(self) -> None:
        rows = parser.extract_section_rows([pages.DASHBOARD_WITH_SECTIONS])
        assignments = parser.enrich_assignments(
            parser.extract_assignments_from_sections(rows), self.details
        )
        task = next(item for item in assignments if item.title == "Term 1 - Task 1")
        self.assertEqual(task.subject, "English Language KS4")
        self.assertEqual(task.class_code, "9En4")
        self.assertEqual(task.course, "English Language KS4: 9En4")
        self.assertEqual(task.marking, "No mark")
        self.assertEqual(task.submission_type, "Physical/Other")
        self.assertIn("Metaphor", task.instructions or "")

    def test_the_marking_scheme_is_not_reported_as_a_grade(self) -> None:
        rows = parser.extract_section_rows([pages.DASHBOARD_WITH_SECTIONS])
        assignments = parser.enrich_assignments(
            parser.extract_assignments_from_sections(rows), self.details
        )
        # "No mark" and "Number" say how the work will be marked, not how it was.
        self.assertTrue(all(item.grade is None for item in assignments))

    def test_an_assignment_with_no_detail_page_keeps_its_class_code(self) -> None:
        rows = parser.extract_section_rows([pages.DASHBOARD_WITH_SECTIONS])
        assignments = parser.enrich_assignments(
            parser.extract_assignments_from_sections(rows), self.details
        )
        flash = next(item for item in assignments if item.title == "Flash Cards")
        self.assertEqual(flash.subject, "9Ma3")
        self.assertIsNone(flash.course)

    def test_a_date_is_found_inside_a_crowded_field(self) -> None:
        self.assertEqual(
            parser.find_date("Science KS4: 9Sc3, 25 Sep 2026"), date(2026, 9, 25)
        )
        self.assertEqual(parser.find_date("24 Sep 2026"), date(2026, 9, 24))
        self.assertIsNone(parser.find_date("Science KS4: 9Sc3"))

    def test_splits_a_course_into_subject_and_class(self) -> None:
        self.assertEqual(
            parser.split_course("English Language KS4: 9En4"), ("English Language KS4", "9En4")
        )
        self.assertEqual(parser.split_course("Open Evening"), ("Open Evening", None))


class TestKpiComparisons(unittest.TestCase):
    """A KPI tile states the figures it compares its headline against."""

    def setUp(self) -> None:
        self.kpis = parser.extract_kpis([pages.STUDENT_KPIS_WITH_COMPARISONS])

    def test_a_bar_chart_comparison_is_read_from_the_bar(self) -> None:
        attendance = next(k for k in self.kpis if "Attendance" in k.title)
        self.assertEqual(attendance.comparisons, {"Year": "100%", "Last 4 weeks": "96%"})

    def test_a_captioned_comparison_is_split_on_the_colon(self) -> None:
        positive = next(k for k in self.kpis if k.title.startswith("Positive"))
        self.assertEqual(
            positive.comparisons, {"This year": "35 incidents", "Last term": "32 incidents"}
        )

    def test_attendance_periods_are_percentages(self) -> None:
        self.assertEqual(
            parser.attendance_periods_from_kpis(self.kpis),
            {"Year": 100.0, "Last 4 weeks": 96.0},
        )

    def test_behaviour_periods_are_counts_per_polarity(self) -> None:
        periods = parser.behaviour_periods_from_kpis(self.kpis)
        # "Last term" is on the tile and nowhere else: the behaviour page states
        # this term, this year and the child's lifetime, but not the one before.
        self.assertEqual(periods["positive"]["Last term"], 32.0)
        self.assertEqual(periods["negative"]["Last term"], 1.0)

    def test_a_tile_without_comparisons_is_not_a_failure(self) -> None:
        plain = parser.extract_kpis([pages.STUDENT_KPIS])
        self.assertTrue(plain)
        self.assertTrue(all(kpi.comparisons == {} for kpi in plain))
        # The headline still reads, which is what the sensor uses.
        self.assertEqual(parser.attendance_from_kpis(plain), 100.0)


class TestAttendanceByDate(unittest.TestCase):
    """Every registration session, with the mark the school recorded."""

    def setUp(self) -> None:
        self.rows = parser.extract_section_rows([pages.ATTENDANCE_BY_DATE_PAGE])
        self.marks = parser.extract_attendance_marks(self.rows)

    def test_a_present_mark_survives_having_no_text(self) -> None:
        """Arbor draws a present mark as a tick, which flattens to nothing.

        Requiring a row to have text discarded 24 of a term's 28 sessions.
        """
        present = [mark for mark in self.marks if mark.status == "present"]
        self.assertEqual(len(present), 3)
        self.assertIsNone(present[0].code)

    def test_reads_every_session(self) -> None:
        self.assertEqual(len(self.marks), 10)

    def test_newest_first(self) -> None:
        self.assertEqual(self.marks[0].on, date(2026, 9, 22))
        self.assertEqual(self.marks[0].session, "PM")

    def test_splits_the_date_from_the_register(self) -> None:
        morning = next(
            m for m in self.marks if m.on == date(2026, 9, 21) and m.session == "AM"
        )
        self.assertEqual(morning.mark, "Present AM")
        self.assertEqual(morning.week, "20 Sep 2026 - 26 Sep 2026")

    def test_keeps_the_code_the_school_printed(self) -> None:
        unavoidable = next(m for m in self.marks if m.code == "Y7")
        self.assertEqual(unavoidable.mark, "Any Other Unavoidable Cause")
        self.assertEqual(unavoidable.status, "not_counted")
        # A Y code is neither an attendance nor an absence.
        self.assertFalse(unavoidable.is_absence)

    def test_a_dash_is_an_absent_mark_not_a_code(self) -> None:
        unmarked = next(m for m in self.marks if m.status == "unmarked")
        self.assertIsNone(unmarked.code)
        self.assertEqual(unmarked.mark, "No Mark")
        self.assertFalse(unmarked.is_absence)

    def test_classifies_the_marks_a_school_actually_uses(self) -> None:
        self.assertEqual(
            parser.summarise_attendance_marks(self.marks),
            {"unmarked": 2, "present": 3, "late": 1, "authorised": 1,
             "unauthorised": 1, "not_counted": 2},
        )

    def test_late_counts_as_present_but_is_still_counted_as_late(self) -> None:
        summary = parser.attendance_from_marks(self.marks)
        self.assertEqual(summary.late_sessions, 1)
        self.assertEqual(summary.present_sessions, 4)

    def test_the_percentage_excludes_sessions_the_school_does_not_count(self) -> None:
        """A "Y" code is out of the denominator, not counted as an absence.

        This is how 28 listed sessions produce a 24-session total at Wrotham.
        """
        summary = parser.attendance_from_marks(self.marks)
        # 4 present or late out of 6 countable; the 2 Y codes and 2 unmarked
        # sessions are in neither number.
        self.assertEqual(summary.percentage, 66.67)
        self.assertEqual(summary.authorised_absences, 1)
        self.assertEqual(summary.unauthorised_absences, 1)

    def test_an_unknown_mark_is_kept_rather_than_guessed_at(self) -> None:
        rows = [parser.SectionRow(section="w", text="", label="01 Sep 2026 AM",
                                  description="Study Leave")]
        mark = parser.extract_attendance_marks(rows)[0]
        self.assertEqual(mark.mark, "Study Leave")
        self.assertEqual(mark.status, "other")
        self.assertFalse(mark.is_absence)

    def test_a_row_that_is_not_a_session_is_ignored(self) -> None:
        rows = parser.extract_section_rows([pages.DASHBOARD_WITH_SECTIONS])
        self.assertEqual(parser.extract_attendance_marks(rows), [])


class TestDownloadLinksAreNotFollowed(unittest.TestCase):
    """The attendance page links a PDF certificate; it must be left alone."""

    def test_a_download_url_is_not_offered_as_content(self) -> None:
        page = {
            "type": "page",
            "content": [
                {
                    "xtype": "mis-button-load-page",
                    "props": {
                        "pageUrl": "/guardians/student/download-attendance-certificate"
                        "/student-id/1879/academic-year-id/16"
                    },
                },
                {
                    "xtype": "mis-button-load-page",
                    "props": {"pageUrl": "/guardians/student-ui/overview/id/1879"},
                },
            ],
        }
        self.assertEqual(
            parser.extract_content_urls(page), ["/guardians/student-ui/overview/id/1879"]
        )


class TestCurrencyIsNotReadFromAPath(unittest.TestCase):
    """Arbor's account filters are links, and a link is not a balance."""

    URL = "/guardians/customer-account-ui/top-ups-dashboard/student-id/1879/term-id/40"

    def test_a_path_is_not_a_monetary_amount(self) -> None:
        # Reported the child's own id as a balance of £1879.
        self.assertIsNone(parser.parse_currency(self.URL))
        self.assertIsNone(parser.parse_currency("https://school.example/accounts/12"))

    def test_a_bare_number_is_still_a_balance(self) -> None:
        # Some schools print one without a symbol.
        self.assertEqual(parser.parse_currency("4.15"), 4.15)
        self.assertEqual(parser.parse_currency("£4.15"), 4.15)
        self.assertEqual(parser.parse_currency("-£1.50"), -1.50)

    def test_a_filter_link_is_not_an_account(self) -> None:
        page = {
            "items": [
                {"xtype": "mis-section", "props": {"title": "Account"}},
                {"fieldLabel": "Meals", "value": self.URL},
            ]
        }
        self.assertEqual(parser.extract_accounts([page]), [])

    def test_looks_like_path_leaves_ordinary_text_alone(self) -> None:
        self.assertFalse(parser.looks_like_path("Balance: £4.15"))
        self.assertFalse(parser.looks_like_path("Maths KS4: 9Ma3"))
        self.assertTrue(parser.looks_like_path("/guardians/home-ui/dashboard"))


class TestRouteClassification(unittest.TestCase):
    """A caption can say nothing; the route behind it does."""

    def test_a_bare_caption_is_classified_by_its_path(self) -> None:
        tree = {"items": [{"text": "By Date",
                           "url": "/guardians/student-ui/attendance-by-date/student-id/40219"}]}
        found = parser.classify_pages([tree], const.DOMAIN_KEYWORDS)
        self.assertEqual(
            found[const.DATA_ATTENDANCE],
            {"By Date": "/guardians/student-ui/attendance-by-date/student-id/40219"},
        )

    def test_the_caption_still_wins(self) -> None:
        # The caption names behaviour; the path names attendance. Arbor's own
        # caption is the better description of what the page shows.
        tree = {"items": [{"text": "Behaviour",
                           "url": "/guardians/student-ui/attendance-by-date/student-id/40219"}]}
        found = parser.classify_pages([tree], const.DOMAIN_KEYWORDS)
        self.assertIn(const.DATA_BEHAVIOUR, found)
        self.assertNotIn(const.DATA_ATTENDANCE, found)

    def test_a_path_naming_nothing_is_still_skipped(self) -> None:
        tree = {"items": [{"text": "Overview", "url": "/guardians/student-ui/overview/id/40219"}]}
        self.assertEqual(parser.classify_pages([tree], const.DOMAIN_KEYWORDS), {})


if __name__ == "__main__":
    unittest.main()
