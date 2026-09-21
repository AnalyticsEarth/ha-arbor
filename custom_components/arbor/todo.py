"""To-do list entity exposing each child's Arbor assignments."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ArborConfigEntry
from .coordinator import ArborCoordinator
from .entity import ArborStudentEntity
from .models import Assignment


async def async_setup_entry(
    hass: HomeAssistant, entry: ArborConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one assignments list per child."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_students() -> None:
        if coordinator.data is None:
            return
        new = [
            ArborAssignmentsTodoList(coordinator, student_id)
            for student_id in coordinator.data.students
            if student_id not in known
        ]
        known.update(coordinator.data.students)
        if new:
            async_add_entities(new)

    _add_new_students()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_students))


class ArborAssignmentsTodoList(ArborStudentEntity, TodoListEntity):
    """A child's homework as a read-only to-do list.

    Arbor is the system of record for submissions, so nothing here writes back:
    ticking an item off in Home Assistant would not hand the work in.
    """

    _attr_translation_key = "assignments"

    def __init__(self, coordinator: ArborCoordinator, student_id: str) -> None:
        """Set up the to-do list entity."""
        super().__init__(coordinator, student_id, "assignments")

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """The child's assignments, newest deadline first."""
        student = self.student
        if student is None:
            return None
        return [_to_todo_item(item) for item in student.assignments]


def _to_todo_item(assignment: Assignment) -> TodoItem:
    """Convert an assignment into a to-do item."""
    # Arbor does not expose a stable assignment id to guardians, so derive one
    # from the fields that identify the piece of work.
    fingerprint = "|".join(
        [
            assignment.title,
            assignment.subject or "",
            assignment.due.isoformat() if assignment.due else "",
        ]
    )
    uid = hashlib.sha1(fingerprint.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]

    due: date | datetime | None = assignment.due
    if isinstance(due, datetime):
        due = due.date()

    description_parts = [
        part
        for part in (
            f"Subject: {assignment.subject}" if assignment.subject else None,
            f"Set by: {assignment.teacher}" if assignment.teacher else None,
            f"Status: {assignment.status}" if assignment.status else None,
            f"Grade: {assignment.grade}" if assignment.grade else None,
        )
        if part
    ]

    return TodoItem(
        uid=uid,
        summary=assignment.title,
        status=TodoItemStatus.COMPLETED
        if assignment.is_submitted
        else TodoItemStatus.NEEDS_ACTION,
        due=due,
        description="\n".join(description_parts) or None,
    )
