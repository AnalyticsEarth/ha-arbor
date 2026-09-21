"""Binary sensors for the Arbor integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ArborConfigEntry
from .coordinator import ArborCoordinator
from .entity import ArborStudentEntity
from .models import StudentData


@dataclass(frozen=True, kw_only=True)
class ArborBinarySensorDescription(BinarySensorEntityDescription):
    """Describes one Arbor binary sensor."""

    value_fn: Callable[[StudentData], bool | None]
    attributes_fn: Callable[[StudentData], dict[str, Any]] | None = None


def _has_school_today(student: StudentData) -> bool | None:
    """Whether the child has anything timetabled today."""
    if not student.lessons:
        return None
    today = date.today()
    return any(
        (lesson.start.date() if lesson.start else lesson.all_day_on) == today
        for lesson in student.lessons
    )


def _in_lesson(student: StudentData) -> bool | None:
    """Whether a timetabled lesson is running right now."""
    timed = [lesson for lesson in student.lessons if lesson.start and lesson.end]
    if not timed:
        return None
    now = datetime.now()
    return any(lesson.start <= now <= lesson.end for lesson in timed)


BINARY_SENSORS: tuple[ArborBinarySensorDescription, ...] = (
    ArborBinarySensorDescription(
        key="school_today",
        translation_key="school_today",
        icon="mdi:school-outline",
        value_fn=_has_school_today,
    ),
    ArborBinarySensorDescription(
        key="in_lesson",
        translation_key="in_lesson",
        icon="mdi:human-male-board",
        value_fn=_in_lesson,
    ),
    ArborBinarySensorDescription(
        key="has_overdue_assignments",
        translation_key="has_overdue_assignments",
        icon="mdi:alert-circle-outline",
        value_fn=lambda student: bool(student.overdue_assignments),
        attributes_fn=lambda student: {
            "count": len(student.overdue_assignments),
            "titles": [item.title for item in student.overdue_assignments[:25]],
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ArborConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Arbor binary sensors."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_students() -> None:
        if coordinator.data is None:
            return
        new = [
            ArborBinarySensor(coordinator, student_id, description)
            for student_id in coordinator.data.students
            if student_id not in known
            for description in BINARY_SENSORS
        ]
        known.update(coordinator.data.students)
        if new:
            async_add_entities(new)

    _add_new_students()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_students))


class ArborBinarySensor(ArborStudentEntity, BinarySensorEntity):
    """A yes/no fact about one child."""

    entity_description: ArborBinarySensorDescription

    _unrecorded_attributes = frozenset({"titles"})

    def __init__(
        self,
        coordinator: ArborCoordinator,
        student_id: str,
        description: ArborBinarySensorDescription,
    ) -> None:
        """Set up the binary sensor from its description."""
        super().__init__(coordinator, student_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state, or None when the portal gave us nothing to judge on."""
        student = self.student
        if student is None:
            return None
        return self.entity_description.value_fn(student)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Supporting detail behind the state."""
        student = self.student
        if student is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(student)
