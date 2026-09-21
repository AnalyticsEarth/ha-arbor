"""Sensors for the Arbor integration."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ArborConfigEntry
from .coordinator import ArborCoordinator
from .entity import ArborStudentEntity
from .models import Assignment, BehaviourIncident, Lesson, StudentData

_LOGGER = logging.getLogger(__name__)

# How many list items to publish as attributes. Home Assistant stores attributes
# in the state machine and recorder, so an unbounded list is a real cost. A term
# of behaviour incidents runs to a few dozen, so the cap has to clear that to be
# of any use for a whole school year.
MAX_LIST_ATTRIBUTES = 60

# Assignment instructions run to whole paragraphs. The full text goes on the
# to-do item, where a parent actually reads it; an attribute carries an excerpt
# so a dashboard card can show what the work is without holding kilobytes of
# prose in the state machine.
MAX_TEXT_ATTRIBUTE = 500


def _iso(value: datetime | date | None) -> str | None:
    """ISO-format a date or datetime for an attribute."""
    return value.isoformat() if value is not None else None


def _clip(text: str | None) -> str | None:
    """Shorten a long free-text field for an attribute."""
    if text is None or len(text) <= MAX_TEXT_ATTRIBUTE:
        return text
    return f"{text[: MAX_TEXT_ATTRIBUTE - 1]}…"


def _assignment_attrs(items: list[Assignment]) -> list[dict[str, Any]]:
    return [
        {
            "title": item.title,
            "subject": item.subject,
            "class": item.class_code,
            "course": item.course,
            "due": _iso(item.due),
            "status": item.status,
            "marking": item.marking,
            "submission_type": item.submission_type,
            "instructions": _clip(item.instructions),
            "grade": item.grade,
            "teacher": item.teacher,
            "overdue": item.is_overdue,
            "url": item.url,
        }
        for item in items[:MAX_LIST_ATTRIBUTES]
    ]


def _incident_attrs(items: list[BehaviourIncident]) -> list[dict[str, Any]]:
    return [
        {
            "date": _iso(item.occurred),
            "type": item.kind,
            "points": item.points,
            "subject": item.subject,
            "class": item.class_code,
            "event": item.event,
            "staff": item.staff,
            "comment": item.comment,
            "polarity": item.polarity,
            "positive": item.is_positive,
        }
        for item in items[:MAX_LIST_ATTRIBUTES]
    ]


def _count_by(items: list[BehaviourIncident], attribute: str) -> dict[str, int]:
    """Incidents grouped by one of their fields, most frequent first.

    This is the question a parent actually asks -- which subjects the points came
    from -- and it is not answerable from a running total.
    """
    counts = Counter(getattr(item, attribute) or "(not stated)" for item in items)
    return dict(counts.most_common())


def _behaviour_attrs(student: StudentData) -> dict[str, Any]:
    """The behaviour record, broken down the ways it gets asked about."""
    incidents = student.behaviour_incidents
    return {
        "positive_points": student.behaviour_points_positive,
        "negative_points": student.behaviour_points_negative,
        "incident_count": len(incidents),
        "positive_incidents": sum(1 for item in incidents if item.is_positive),
        "negative_incidents": sum(1 for item in incidents if item.is_negative),
        "by_subject": _count_by(incidents, "subject"),
        "by_type": _count_by(incidents, "kind"),
        "by_staff": _count_by(incidents, "staff"),
        # Arbor states each total for the child's lifetime, the academic year and
        # the current term, which is how 129 and 35 are both true.
        "totals_by_period": student.behaviour_totals,
        "recent_incidents": _incident_attrs(incidents),
    }


def _lesson_attrs(items: list[Lesson]) -> list[dict[str, Any]]:
    return [
        {
            "summary": item.summary,
            "start": _iso(item.start),
            "end": _iso(item.end),
            "date": _iso(item.all_day_on),
            "location": item.location,
            "teacher": item.teacher,
        }
        for item in items[:MAX_LIST_ATTRIBUTES]
    ]


@dataclass(frozen=True, kw_only=True)
class ArborSensorDescription(SensorEntityDescription):
    """Describes one Arbor sensor."""

    value_fn: Callable[[StudentData], Any]
    attributes_fn: Callable[[StudentData], dict[str, Any]] | None = None
    unit_fn: Callable[[StudentData], str | None] | None = None


def _attendance_attrs(student: StudentData) -> dict[str, Any]:
    summary = student.attendance
    return {
        "present_sessions": summary.present_sessions,
        "authorised_absences": summary.authorised_absences,
        "unauthorised_absences": summary.unauthorised_absences,
        "late_sessions": summary.late_sessions,
        "period": summary.period,
    }


def _next_lesson_value(student: StudentData) -> str | None:
    lesson = student.next_lesson
    return lesson.summary if lesson else None


def _next_lesson_attrs(student: StudentData) -> dict[str, Any]:
    lesson = student.next_lesson
    if lesson is None:
        return {"lessons_today": 0}
    today = date.today()
    return {
        "start": _iso(lesson.start),
        "end": _iso(lesson.end),
        "location": lesson.location,
        "teacher": lesson.teacher,
        "lessons_today": sum(
            1
            for item in student.lessons
            if (item.start.date() if item.start else item.all_day_on) == today
        ),
        "upcoming": _lesson_attrs(
            [item for item in student.lessons if item.start and item.start >= datetime.now()]
        ),
    }


def _balance_value(student: StudentData) -> float | None:
    account = student.primary_account
    return account.balance if account else None


def _balance_attrs(student: StudentData) -> dict[str, Any]:
    return {
        "accounts": [
            {"name": account.name, "balance": account.balance, "currency": account.currency}
            for account in student.accounts[:MAX_LIST_ATTRIBUTES]
        ]
    }


# A monetary sensor must always report a unit, even before the first balance
# has been scraped. Arbor tenants are UK schools, so sterling is the default.
_CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def _balance_unit(student: StudentData) -> str:
    account = student.primary_account
    currency = account.currency if account else "GBP"
    return _CURRENCY_SYMBOLS.get(currency, currency)


def _summary_attrs(student: StudentData) -> dict[str, Any]:
    """Everything known about a child, in one place.

    This backs the per-child summary entity, for dashboard cards and templates
    that want the whole picture without referencing a dozen entity ids. The
    individual sensors remain the place to get history and numeric triggers.
    """
    lesson = student.next_lesson
    today = date.today()
    account = student.primary_account
    return {
        "student_id": student.student_id,
        "year_group": student.year_group,
        "form_group": student.form_group,
        "attendance_percentage": student.attendance.percentage,
        "attendance": _attendance_attrs(student),
        "behaviour_points": student.behaviour_points_net,
        "positive_points": student.behaviour_points_positive,
        "negative_points": student.behaviour_points_negative,
        "behaviour_incidents": _incident_attrs(student.behaviour_incidents),
        "behaviour_by_subject": _count_by(student.behaviour_incidents, "subject"),
        "behaviour_by_type": _count_by(student.behaviour_incidents, "kind"),
        "behaviour_totals_by_period": student.behaviour_totals,
        "assignments_total": len(student.assignments),
        "assignments_outstanding": len(student.outstanding_assignments),
        "assignments_overdue": len(student.overdue_assignments),
        "assignments": _assignment_attrs(student.assignments),
        "next_lesson": lesson.summary if lesson else None,
        "next_lesson_start": _iso(lesson.start) if lesson else None,
        "next_lesson_location": lesson.location if lesson else None,
        "next_lesson_teacher": lesson.teacher if lesson else None,
        "lessons_today": sum(
            1
            for item in student.lessons
            if (item.start.date() if item.start else item.all_day_on) == today
        ),
        "lessons": _lesson_attrs(student.lessons),
        "meal_balance": account.balance if account else None,
        "accounts": [
            {"name": item.name, "balance": item.balance, "currency": item.currency}
            for item in student.accounts[:MAX_LIST_ATTRIBUTES]
        ],
        "grades": [
            {
                "subject": item.subject,
                "grade": item.value,
                "target": item.target,
                "assessment": item.assessment,
            }
            for item in student.grades[:MAX_LIST_ATTRIBUTES]
        ],
        "notices_count": len(student.notices),
        "notices": [
            {"title": item.title, "published": _iso(item.published)}
            for item in student.notices[:MAX_LIST_ATTRIBUTES]
        ],
        "missing_data": sorted(student.empty_domains),
    }


SENSORS: tuple[ArborSensorDescription, ...] = (
    # `name=None` makes this the device's primary entity, so it is called after
    # the child rather than carrying a suffix: sensor.amelia_example.
    ArborSensorDescription(
        key="summary",
        name=None,
        icon="mdi:account-school-outline",
        value_fn=lambda student: student.name,
        attributes_fn=_summary_attrs,
    ),
    ArborSensorDescription(
        key="attendance",
        translation_key="attendance",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:calendar-check",
        value_fn=lambda student: student.attendance.percentage,
        attributes_fn=_attendance_attrs,
    ),
    ArborSensorDescription(
        key="behaviour_points",
        translation_key="behaviour_points",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:scale-balance",
        value_fn=lambda student: student.behaviour_points_net,
        attributes_fn=_behaviour_attrs,
    ),
    ArborSensorDescription(
        key="positive_points",
        translation_key="positive_points",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thumb-up-outline",
        value_fn=lambda student: student.behaviour_points_positive,
    ),
    ArborSensorDescription(
        key="negative_points",
        translation_key="negative_points",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thumb-down-outline",
        value_fn=lambda student: student.behaviour_points_negative,
    ),
    ArborSensorDescription(
        key="assignments_outstanding",
        translation_key="assignments_outstanding",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:notebook-edit-outline",
        value_fn=lambda student: len(student.outstanding_assignments),
        attributes_fn=lambda student: {
            "total": len(student.assignments),
            "overdue": len(student.overdue_assignments),
            "assignments": _assignment_attrs(student.outstanding_assignments),
        },
    ),
    ArborSensorDescription(
        key="assignments_overdue",
        translation_key="assignments_overdue",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:alert-outline",
        value_fn=lambda student: len(student.overdue_assignments),
        attributes_fn=lambda student: {
            "assignments": _assignment_attrs(student.overdue_assignments)
        },
    ),
    ArborSensorDescription(
        key="next_lesson",
        translation_key="next_lesson",
        icon="mdi:timetable",
        value_fn=_next_lesson_value,
        attributes_fn=_next_lesson_attrs,
    ),
    ArborSensorDescription(
        key="meal_balance",
        translation_key="meal_balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:food-apple-outline",
        value_fn=_balance_value,
        attributes_fn=_balance_attrs,
        unit_fn=_balance_unit,
    ),
    ArborSensorDescription(
        key="notices",
        translation_key="notices",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:bulletin-board",
        value_fn=lambda student: len(student.notices),
        attributes_fn=lambda student: {
            "notices": [
                {
                    "title": notice.title,
                    "published": _iso(notice.published),
                    "body": notice.body,
                }
                for notice in student.notices[:MAX_LIST_ATTRIBUTES]
            ]
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ArborConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Arbor sensors, adding entities for children found later too."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_students() -> None:
        if coordinator.data is None:
            return
        new = [
            ArborSensor(coordinator, student_id, description)
            for student_id in coordinator.data.students
            if student_id not in known
            for description in SENSORS
        ]
        known.update(coordinator.data.students)
        if new:
            async_add_entities(new)

    _add_new_students()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_students))


class ArborSensor(ArborStudentEntity, SensorEntity):
    """A single reported value about one child."""

    entity_description: ArborSensorDescription

    # List-valued attributes change on most refreshes and would bloat the
    # recorder database without being useful as history.
    _unrecorded_attributes = frozenset(
        {
            "accounts",
            "assignments",
            "attendance",
            "behaviour_by_subject",
            "behaviour_by_type",
            "behaviour_incidents",
            "behaviour_totals_by_period",
            "by_staff",
            "by_subject",
            "by_type",
            "grades",
            "lessons",
            "notices",
            "recent_incidents",
            "totals_by_period",
            "upcoming",
        }
    )

    def __init__(
        self,
        coordinator: ArborCoordinator,
        student_id: str,
        description: ArborSensorDescription,
    ) -> None:
        """Set up the sensor from its description."""
        super().__init__(coordinator, student_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """The sensor's current value."""
        student = self.student
        if student is None:
            return None
        return self.entity_description.value_fn(student)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Unit, resolved at runtime for the currency sensors."""
        if self.entity_description.unit_fn is not None and (student := self.student):
            return self.entity_description.unit_fn(student)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Supporting detail behind the value."""
        student = self.student
        if student is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(student)
