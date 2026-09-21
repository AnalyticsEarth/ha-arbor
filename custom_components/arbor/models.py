"""Normalised data models for the Arbor integration.

Arbor's parent portal renders every page from a JSON component tree whose exact
shape varies by school configuration and Arbor release. Everything in this
module is the *normalised* form the rest of the integration works with, so the
scraping heuristics in ``parser.py`` are the only code that has to care about
Arbor's wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time


@dataclass(slots=True)
class ArborSchool:
    """A school tenant returned by Arbor's login-time school search."""

    name: str
    base_url: str
    short_name: str | None = None
    location: str | None = None
    application_id: str | None = None

    @property
    def label(self) -> str:
        """Human-readable name for a config-flow picker."""
        name = self.short_name or self.name
        if self.location:
            return f"{name} ({self.location})"
        return name


@dataclass(slots=True)
class Assignment:
    """A piece of homework or coursework."""

    title: str
    #: The subject as the school names it: "English Language KS4". Arbor's list
    #: of work shows only the class code, so this falls back to that.
    subject: str | None = None
    due: datetime | date | None = None
    status: str | None = None
    grade: str | None = None
    teacher: str | None = None
    url: str | None = None
    #: Subject and class together, as the assignment's own page states it:
    #: "English Language KS4: 9En4".
    course: str | None = None
    #: The teaching group the work was set for: "9En4".
    class_code: str | None = None
    #: How the work will be marked ("No mark", "Number") -- not a grade.
    marking: str | None = None
    #: How it is to be handed in ("Submit via Arbor", "Physical/Other").
    submission_type: str | None = None
    #: The teacher's instructions to the student, in full.
    instructions: str | None = None

    @property
    def is_submitted(self) -> bool:
        """Whether Arbor reports the work as handed in."""
        if not self.status:
            return False
        lowered = self.status.casefold()
        if "not submitted" in lowered or "unsubmitted" in lowered:
            return False
        return any(
            token in lowered for token in ("submitted", "handed in", "complete", "marked")
        )

    @property
    def is_overdue(self) -> bool:
        """Whether the due date has passed without a submission."""
        if self.is_submitted or self.due is None:
            return False
        due = self.due.date() if isinstance(self.due, datetime) else self.due
        return due < date.today()


@dataclass(slots=True)
class BehaviourIncident:
    """A single logged positive or negative behaviour event."""

    occurred: datetime | date | None
    #: What the school logged: "Motivation", "Respect", "Weekly 100% attendance".
    kind: str | None = None
    points: float | None = None
    #: The subject the incident was logged in: "Maths KS4".
    subject: str | None = None
    staff: str | None = None
    comment: str | None = None
    #: "positive", "negative" or "neutral", when the school's own page groups
    #: incidents under those headings. More reliable than guessing from wording.
    polarity: str | None = None
    #: What the incident was attached to, in full: "Maths KS4: 9Ma3", or a
    #: one-off like "Open Evening Tour Guides and Department Helpers".
    event: str | None = None
    #: The teaching group, when the event names one: "9Ma3".
    class_code: str | None = None

    @property
    def is_positive(self) -> bool:
        """Whether this reads as a positive event."""
        if self.polarity is not None:
            return self.polarity == "positive"
        if self.points is not None:
            return self.points >= 0
        lowered = (self.kind or "").casefold()
        return any(
            token in lowered
            for token in ("positive", "achievement", "praise", "merit", "reward", "house point")
        )

    @property
    def is_negative(self) -> bool:
        """Whether this reads as a negative event.

        Not simply ``not is_positive``: a school can log a neutral incident, and
        counting those against a child would misreport their record.
        """
        if self.polarity is not None:
            return self.polarity == "negative"
        if self.points is not None:
            return self.points < 0
        lowered = (self.kind or "").casefold()
        return any(
            token in lowered
            for token in ("negative", "concern", "sanction", "detention", "demerit")
        )


@dataclass(slots=True)
class AttendanceSummary:
    """Attendance percentages and session counts for a reporting period."""

    percentage: float | None = None
    present_sessions: int | None = None
    authorised_absences: int | None = None
    unauthorised_absences: int | None = None
    late_sessions: int | None = None
    period: str | None = None


@dataclass(slots=True)
class Lesson:
    """A single timetabled event."""

    summary: str
    start: datetime | None = None
    end: datetime | None = None
    all_day_on: date | None = None
    location: str | None = None
    teacher: str | None = None
    description: str | None = None

    @property
    def sort_key(self) -> datetime:
        """Key that orders lessons and all-day events together.

        Always a datetime: mixing dates and datetimes here is not sortable.
        """
        if self.start is not None:
            return self.start
        if self.all_day_on is not None:
            return datetime.combine(self.all_day_on, time.min)
        return datetime.min


@dataclass(slots=True)
class Grade:
    """A reported mark for a subject."""

    subject: str
    value: str | None = None
    target: str | None = None
    assessment: str | None = None


@dataclass(slots=True)
class Notice:
    """A school notice, news item or in-app message."""

    title: str
    published: datetime | date | None = None
    body: str | None = None
    url: str | None = None


@dataclass(slots=True)
class AccountBalance:
    """A meal or top-up account balance."""

    name: str
    balance: float | None = None
    currency: str = "GBP"


@dataclass(slots=True)
class StudentData:
    """Everything the integration knows about one child."""

    student_id: str
    name: str
    profile_url: str | None = None
    year_group: str | None = None
    form_group: str | None = None
    photo_url: str | None = None

    attendance: AttendanceSummary = field(default_factory=AttendanceSummary)
    behaviour_points_positive: float | None = None
    behaviour_points_negative: float | None = None
    behaviour_incidents: list[BehaviourIncident] = field(default_factory=list)
    #: Incident counts by polarity and then period, as the behaviour page states
    #: them: ``{"positive": {"Lifetime": 129, "2026/2027": 35, "Autumn": 35}}``.
    behaviour_totals: dict[str, dict[str, float]] = field(default_factory=dict)
    assignments: list[Assignment] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    grades: list[Grade] = field(default_factory=list)
    accounts: list[AccountBalance] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)

    # Raw per-domain page trees, kept for diagnostics and the dump_page service.
    raw: dict[str, object] = field(default_factory=dict)
    # Domains a page was successfully read for. A domain that is sourced but
    # empty is usually correct -- a child with no homework due really has none --
    # whereas an unsourced domain means no page was found to read.
    sourced_domains: set[str] = field(default_factory=set)
    # Domains that produced no usable data, sourced or not.
    empty_domains: set[str] = field(default_factory=set)

    @property
    def unsourced_domains(self) -> set[str]:
        """Domains with no data *and* no page found to read it from."""
        return self.empty_domains - self.sourced_domains

    @property
    def behaviour_points_net(self) -> float | None:
        """Positive minus negative points, when either is known."""
        if self.behaviour_points_positive is None and self.behaviour_points_negative is None:
            return None
        return (self.behaviour_points_positive or 0) - (self.behaviour_points_negative or 0)

    @property
    def outstanding_assignments(self) -> list[Assignment]:
        """Assignments Arbor has not marked as handed in."""
        return [item for item in self.assignments if not item.is_submitted]

    @property
    def overdue_assignments(self) -> list[Assignment]:
        """Outstanding assignments whose due date has passed."""
        return [item for item in self.assignments if item.is_overdue]

    @property
    def next_lesson(self) -> Lesson | None:
        """The next timetabled lesson that has not finished yet."""
        now = datetime.now()
        upcoming = [
            lesson
            for lesson in self.lessons
            if lesson.start is not None and (lesson.end or lesson.start) >= now
        ]
        if not upcoming:
            return None
        return min(upcoming, key=lambda lesson: lesson.start or datetime.max)

    @property
    def primary_account(self) -> AccountBalance | None:
        """The meal account if there is one, else the first known account."""
        for account in self.accounts:
            if "meal" in account.name.casefold() or "lunch" in account.name.casefold():
                return account
        return self.accounts[0] if self.accounts else None


@dataclass(slots=True)
class ArborData:
    """Top-level payload produced by the coordinator on each refresh."""

    school_name: str | None = None
    guardian_name: str | None = None
    students: dict[str, StudentData] = field(default_factory=dict)
    school_notices: list[Notice] = field(default_factory=list)
    discovered_pages: dict[str, dict[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Guardian-wide payloads, kept for diagnostics rather than for entities.
    raw: dict[str, object] = field(default_factory=dict)
