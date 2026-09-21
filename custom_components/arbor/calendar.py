"""Calendar entity exposing each child's Arbor timetable."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ArborConfigEntry
from .coordinator import ArborCoordinator
from .entity import ArborStudentEntity
from .models import Lesson

# Arbor gives a lesson with no end time no duration at all, which Home Assistant
# rejects; give those a nominal slot instead of dropping them.
DEFAULT_LESSON_DURATION = timedelta(minutes=60)


async def async_setup_entry(
    hass: HomeAssistant, entry: ArborConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one timetable calendar per child."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_students() -> None:
        if coordinator.data is None:
            return
        new = [
            ArborTimetableCalendar(coordinator, student_id)
            for student_id in coordinator.data.students
            if student_id not in known
        ]
        known.update(coordinator.data.students)
        if new:
            async_add_entities(new)

    _add_new_students()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_students))


class ArborTimetableCalendar(ArborStudentEntity, CalendarEntity):
    """A child's lessons and school events as a calendar."""

    _attr_translation_key = "timetable"

    def __init__(self, coordinator: ArborCoordinator, student_id: str) -> None:
        """Set up the calendar entity."""
        super().__init__(coordinator, student_id, "timetable")

    def _to_event(self, lesson: Lesson) -> CalendarEvent | None:
        """Convert a lesson into a Home Assistant calendar event."""
        description_parts = [part for part in (lesson.teacher, lesson.description) if part]
        description = " — ".join(description_parts) or None

        if lesson.start is not None:
            start = _localise(lesson.start)
            end = _localise(lesson.end or lesson.start + DEFAULT_LESSON_DURATION)
            if end <= start:
                end = start + DEFAULT_LESSON_DURATION
            return CalendarEvent(
                start=start,
                end=end,
                summary=lesson.summary,
                location=lesson.location,
                description=description,
            )

        if lesson.all_day_on is not None:
            return CalendarEvent(
                start=lesson.all_day_on,
                end=lesson.all_day_on + timedelta(days=1),
                summary=lesson.summary,
                location=lesson.location,
                description=description,
            )
        return None

    def _events(self) -> list[CalendarEvent]:
        """All known lessons as calendar events."""
        student = self.student
        if student is None:
            return []
        return [event for lesson in student.lessons if (event := self._to_event(lesson))]

    @property
    def event(self) -> CalendarEvent | None:
        """The current or next event."""
        now = dt_util.now()
        events = sorted(self._events(), key=lambda item: _event_start(item))
        for item in events:
            if _event_end(item) >= now:
                return item
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping the requested window."""
        return [
            item
            for item in self._events()
            if _event_start(item) < end_date and _event_end(item) > start_date
        ]


def _localise(value: datetime) -> datetime:
    """Make a datetime timezone-aware in Home Assistant's own timezone.

    The parser produces naive local times, and ``dt_util.as_local`` would read
    those as UTC and shift every lesson by the local offset.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_local(value)


def _event_start(event: CalendarEvent) -> datetime:
    """Start of an event as an aware datetime."""
    if isinstance(event.start, datetime):
        return _localise(event.start)
    return dt_util.start_of_local_day(event.start)


def _event_end(event: CalendarEvent) -> datetime:
    """End of an event as an aware datetime."""
    if isinstance(event.end, datetime):
        return _localise(event.end)
    return dt_util.start_of_local_day(event.end)
