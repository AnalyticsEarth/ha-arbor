"""Tests for the derived properties on the Arbor data models."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import models  # noqa: E402


def _student(**kwargs) -> models.StudentData:
    return models.StudentData(student_id="1", name="Test Child", **kwargs)


class TestAssignmentStatus(unittest.TestCase):
    """The submitted/overdue rules the sensors depend on."""

    def test_submitted_wording(self) -> None:
        for status, expected in (
            ("Submitted", True),
            ("Handed in", True),
            ("Marked", True),
            ("Complete", True),
            ("Not submitted", False),
            ("Unsubmitted", False),
            ("Awaiting submission", False),
            ("", False),
            (None, False),
        ):
            with self.subTest(status=status):
                item = models.Assignment(title="Essay", status=status)
                self.assertEqual(item.is_submitted, expected)

    def test_overdue_requires_a_past_deadline(self) -> None:
        yesterday = date.today() - timedelta(days=1)
        tomorrow = date.today() + timedelta(days=1)
        self.assertTrue(
            models.Assignment(title="x", due=yesterday, status="Not submitted").is_overdue
        )
        self.assertFalse(
            models.Assignment(title="x", due=tomorrow, status="Not submitted").is_overdue
        )

    def test_submitted_work_is_never_overdue(self) -> None:
        yesterday = date.today() - timedelta(days=1)
        self.assertFalse(
            models.Assignment(title="x", due=yesterday, status="Submitted").is_overdue
        )

    def test_no_deadline_is_never_overdue(self) -> None:
        self.assertFalse(models.Assignment(title="x", status="Not submitted").is_overdue)


class TestBehaviourIncident(unittest.TestCase):
    """Positive/negative classification."""

    def test_points_decide_when_present(self) -> None:
        self.assertTrue(models.BehaviourIncident(occurred=None, points=2).is_positive)
        self.assertFalse(models.BehaviourIncident(occurred=None, points=-1).is_positive)

    def test_falls_back_to_the_wording(self) -> None:
        self.assertTrue(
            models.BehaviourIncident(occurred=None, kind="Positive - good work").is_positive
        )
        self.assertTrue(
            models.BehaviourIncident(occurred=None, kind="Achievement point").is_positive
        )
        self.assertFalse(
            models.BehaviourIncident(occurred=None, kind="Negative - late").is_positive
        )
        self.assertFalse(models.BehaviourIncident(occurred=None).is_positive)


class TestStudentData(unittest.TestCase):
    """Aggregates the entities read."""

    def test_net_points(self) -> None:
        self.assertEqual(
            _student(
                behaviour_points_positive=10, behaviour_points_negative=3
            ).behaviour_points_net,
            7,
        )

    def test_net_points_is_none_when_nothing_is_known(self) -> None:
        self.assertIsNone(_student().behaviour_points_net)

    def test_net_points_tolerates_one_missing_side(self) -> None:
        self.assertEqual(_student(behaviour_points_positive=10).behaviour_points_net, 10)

    def test_outstanding_and_overdue_split(self) -> None:
        yesterday = date.today() - timedelta(days=1)
        student = _student(
            assignments=[
                models.Assignment(title="done", status="Submitted"),
                models.Assignment(title="todo", status="Not submitted"),
                models.Assignment(title="late", status="Not submitted", due=yesterday),
            ]
        )
        self.assertEqual(
            [item.title for item in student.outstanding_assignments], ["todo", "late"]
        )
        self.assertEqual([item.title for item in student.overdue_assignments], ["late"])

    def test_next_lesson_skips_finished_ones(self) -> None:
        now = datetime.now()
        student = _student(
            lessons=[
                models.Lesson(
                    summary="Past", start=now - timedelta(hours=2), end=now - timedelta(hours=1)
                ),
                models.Lesson(
                    summary="Next", start=now + timedelta(hours=1), end=now + timedelta(hours=2)
                ),
                models.Lesson(
                    summary="Later", start=now + timedelta(hours=3), end=now + timedelta(hours=4)
                ),
            ]
        )
        self.assertEqual(student.next_lesson.summary, "Next")

    def test_a_lesson_in_progress_is_the_next_lesson(self) -> None:
        now = datetime.now()
        student = _student(
            lessons=[
                models.Lesson(
                    summary="Now", start=now - timedelta(minutes=10), end=now + timedelta(minutes=50)
                )
            ]
        )
        self.assertEqual(student.next_lesson.summary, "Now")

    def test_next_lesson_is_none_without_timed_lessons(self) -> None:
        student = _student(lessons=[models.Lesson(summary="INSET", all_day_on=date.today())])
        self.assertIsNone(student.next_lesson)

    def test_primary_account_prefers_the_meal_account(self) -> None:
        student = _student(
            accounts=[
                models.AccountBalance(name="Trips", balance=5.0),
                models.AccountBalance(name="Meals", balance=-1.5),
            ]
        )
        self.assertEqual(student.primary_account.name, "Meals")

    def test_primary_account_falls_back_to_the_first(self) -> None:
        student = _student(accounts=[models.AccountBalance(name="Trips", balance=5.0)])
        self.assertEqual(student.primary_account.name, "Trips")
        self.assertIsNone(_student().primary_account)


class TestLessonSorting(unittest.TestCase):
    """All-day events and timed lessons have to sort together."""

    def test_mixed_lessons_sort(self) -> None:
        lessons = [
            models.Lesson(summary="All day", all_day_on=date(2026, 9, 28)),
            models.Lesson(summary="Timed", start=datetime(2026, 9, 22, 9, 0)),
            models.Lesson(summary="No time at all"),
        ]
        ordered = sorted(lessons, key=lambda lesson: lesson.sort_key)
        self.assertEqual(
            [lesson.summary for lesson in ordered], ["No time at all", "Timed", "All day"]
        )


class TestArborSchool(unittest.TestCase):
    """The label shown in the config-flow school picker."""

    def test_prefers_the_short_name_and_adds_the_location(self) -> None:
        school = models.ArborSchool(
            name="Princes Risborough School",
            base_url="https://princesrisborough.uk.arbor.sc",
            short_name="Princes Risborough",
            location="HP27 0DR",
        )
        self.assertEqual(school.label, "Princes Risborough (HP27 0DR)")

    def test_falls_back_to_the_full_name(self) -> None:
        school = models.ArborSchool(name="A School", base_url="https://a.uk.arbor.sc")
        self.assertEqual(school.label, "A School")


if __name__ == "__main__":
    unittest.main()
