"""Turn Arbor's page component trees into the models in ``models.py``.

Arbor renders each portal page as a nested JSON description of UI components.
The exact keys differ between page types, Arbor releases and school
configurations, so nothing here assumes a fixed path into the tree. Instead the
tree is flattened into three primitives -- tables, labelled metrics and links --
and each domain is then recognised by the words the school's own portal uses.

Every extractor returns whatever it could understand and ignores the rest, so a
page shape we have not seen degrades to missing data rather than an exception.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime, time
from typing import Any

from .models import (
    AccountBalance,
    Assignment,
    AttendanceSummary,
    BehaviourIncident,
    Grade,
    Lesson,
    Notice,
)

_LOGGER = logging.getLogger(__name__)

# Keys that hold a human-readable caption on an Arbor component.
_LABEL_KEYS = (
    "title",
    "text",
    "label",
    "fieldLabel",
    "header",
    "heading",
    "name",
    "caption",
    "displayName",
)

# Keys that hold a component's value.
_VALUE_KEYS = ("value", "displayValue", "html", "content", "subtitle", "description")

# Keys under which a component nests its children.
_CHILD_KEYS = (
    "items",
    "children",
    "rows",
    "data",
    "records",
    "panels",
    "sections",
    "tabs",
    "columns",
    "fields",
    "body",
    "content",
    "widgets",
    "tiles",
    "cards",
    "entries",
    "events",
    "store",
    "config",
    "props",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_DEPTH = 40


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Table:
    """A tabular component: ordered column captions plus row mappings."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    title: str | None = None

    def column_matching(self, *keywords: str) -> str | None:
        """First column whose caption contains any of ``keywords``."""
        for keyword in keywords:
            for column in self.columns:
                if keyword in column.casefold():
                    return column
        return None


@dataclass(slots=True)
class Metric:
    """A labelled single value, e.g. a dashboard tile or a profile field."""

    label: str
    value: str
    context: str | None = None


@dataclass(slots=True)
class Link:
    """A navigable link found in a page tree."""

    text: str
    url: str


def strip_html(value: str) -> str:
    """Flatten an HTML fragment to single-spaced plain text."""
    text = value.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def text_of(value: Any) -> str | None:
    """Best-effort plain-text rendering of any node value."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        cleaned = strip_html(value)
        return cleaned or None
    if isinstance(value, dict):
        for key in (*_VALUE_KEYS, *_LABEL_KEYS):
            if key in value:
                rendered = text_of(value[key])
                if rendered:
                    return rendered
        return None
    if isinstance(value, (list, tuple)):
        parts = [rendered for item in value if (rendered := text_of(item))]
        return ", ".join(parts) or None
    return None


def walk(node: Any, _depth: int = 0) -> Iterator[dict[str, Any]]:
    """Yield every mapping in a tree, outermost first."""
    if _depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value, _depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from walk(item, _depth + 1)


def node_label(node: dict[str, Any]) -> str | None:
    """The caption of a component, if it has one."""
    for key in _LABEL_KEYS:
        if key in node:
            rendered = text_of(node[key])
            if rendered:
                return rendered
    return None


def _column_caption(column: Any, index: int) -> str:
    """Caption for a table column definition."""
    if isinstance(column, str):
        return strip_html(column) or f"column_{index}"
    if isinstance(column, dict):
        for key in ("text", "header", "title", "label", "name", "dataIndex", "field", "key"):
            if key in column:
                rendered = text_of(column[key])
                if rendered:
                    return rendered
    return f"column_{index}"


def _column_keys(columns: list[Any]) -> list[str | None]:
    """The data key each column reads from, when the definition names one."""
    keys: list[str | None] = []
    for column in columns:
        if isinstance(column, dict):
            for key in ("dataIndex", "field", "key", "name", "id"):
                candidate = column.get(key)
                if isinstance(candidate, str) and candidate:
                    keys.append(candidate)
                    break
            else:
                keys.append(None)
        else:
            keys.append(None)
    return keys


def _row_values(row: Any, captions: list[str], keys: list[str | None]) -> dict[str, str] | None:
    """Map one raw row onto ``{caption: text}``."""
    if isinstance(row, (list, tuple)):
        cells = [text_of(cell) for cell in row]
        mapped = {
            captions[index]: cell
            for index, cell in enumerate(cells)
            if index < len(captions) and cell
        }
        return mapped or None

    if not isinstance(row, dict):
        return None

    source = row
    for nested in ("data", "fields", "values", "cells", "record"):
        inner = row.get(nested)
        if isinstance(inner, dict):
            source = {**source, **inner}

    mapped: dict[str, str] = {}
    for index, caption in enumerate(captions):
        key = keys[index] if index < len(keys) else None
        raw: Any = None
        if key and key in source:
            raw = source[key]
        elif caption in source:
            raw = source[caption]
        else:
            # Fall back to a case-insensitive caption match.
            folded = caption.casefold()
            for source_key, source_value in source.items():
                if isinstance(source_key, str) and source_key.casefold() == folded:
                    raw = source_value
                    break
        rendered = text_of(raw)
        if rendered:
            mapped[caption] = rendered

    if mapped:
        return mapped

    # Some rows carry no column definitions at all; keep their scalar fields so
    # keyword matching still has something to work with.
    loose = {
        str(key): rendered
        for key, value in source.items()
        if isinstance(key, str) and (rendered := text_of(value)) and not isinstance(value, (list, dict))
    }
    return loose or None


def _iter_row_sources(node: dict[str, Any]) -> Iterator[Any]:
    """Yield the collections on a node that could hold table rows."""
    for key in ("rows", "data", "records", "items", "entries"):
        value = node.get(key)
        if isinstance(value, list) and value:
            yield value
    store = node.get("store")
    if isinstance(store, dict):
        for key in ("data", "rows", "records", "items"):
            value = store.get(key)
            if isinstance(value, list) and value:
                yield value


def find_tables(tree: Any) -> list[Table]:
    """Every tabular component in a page tree."""
    tables: list[Table] = []
    for node in walk(tree):
        raw_columns = node.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            continue
        captions = [_column_caption(column, index) for index, column in enumerate(raw_columns)]
        keys = _column_keys(raw_columns)
        for rows in _iter_row_sources(node):
            parsed = [values for row in rows if (values := _row_values(row, captions, keys))]
            if parsed:
                tables.append(Table(columns=captions, rows=parsed, title=node_label(node)))
                break
    return tables


def find_metrics(tree: Any) -> list[Metric]:
    """Labelled single values: dashboard tiles, KPI panels, profile fields."""
    metrics: list[Metric] = []
    for node in walk(tree):
        label = None
        for key in ("label", "fieldLabel", "title", "name", "heading", "caption"):
            if key in node:
                label = text_of(node[key])
                if label:
                    break
        if not label:
            continue
        for key in ("value", "displayValue", "text", "subtitle", "content", "html"):
            if key not in node:
                continue
            rendered = text_of(node[key])
            if rendered and rendered != label:
                metrics.append(Metric(label=label, value=rendered))
                break
    return metrics


def find_links(tree: Any) -> list[Link]:
    """Every link-like node, de-duplicated on (text, url)."""
    links: list[Link] = []
    seen: set[tuple[str, str]] = set()
    for node in walk(tree):
        for key in ("url", "href", "pageUrl", "link", "targetUrl", "contentRequestUrl"):
            raw = node.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            text = node_label(node) or ""
            pair = (text, raw)
            if pair in seen:
                continue
            seen.add(pair)
            links.append(Link(text=text, url=raw))
    return links


# ---------------------------------------------------------------------------
# value coercion
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_CURRENCY_RE = re.compile(r"(-?)\s*[£$€]\s*(\d+(?:,\d{3})*(?:\.\d+)?)")
_SIGNED_CURRENCY_RE = re.compile(r"[£$€]\s*(-\d+(?:,\d{3})*(?:\.\d+)?)")
_TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b", re.IGNORECASE)
_TIME_RANGE_RE = re.compile(
    r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\s*(?:-|–|—|to|until)\s*(\d{1,2})[:.](\d{2})\s*(am|pm)?",
    re.IGNORECASE,
)

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d %b %Y",
    "%d %B %Y",
    "%a %d %b %Y",
    "%A %d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
)

# Formats with no year in them; the current year is appended before parsing
# because strptime's own year default is deprecated and changing.
_YEARLESS_DATE_FORMATS = (
    "%d %b",
    "%d %B",
    "%b %d",
    "%B %d",
)


def parse_number(value: Any) -> float | None:
    """First number in a value, as a float."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = text_of(value)
    if not text:
        return None
    match = _NUMBER_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    """First number in a value, rounded to an int."""
    number = parse_number(value)
    return None if number is None else int(round(number))


def parse_percentage(value: Any) -> float | None:
    """A percentage, whether written ``94.5%``, ``94.5`` or ``0.945``."""
    text = text_of(value)
    if not text:
        return None
    match = _PERCENT_RE.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    number = parse_number(text)
    if number is None:
        return None
    if 0 < number <= 1 and "." in text:
        return round(number * 100, 2)
    return number


def parse_currency(value: Any) -> float | None:
    """A monetary amount, handling ``-£1.50``, ``£-1.50`` and ``(£1.50)``."""
    text = text_of(value)
    if not text:
        return None
    signed = _SIGNED_CURRENCY_RE.search(text)
    if signed:
        try:
            return float(signed.group(1).replace(",", ""))
        except ValueError:
            return None
    match = _CURRENCY_RE.search(text)
    if match:
        try:
            amount = float(match.group(2).replace(",", ""))
        except ValueError:
            return None
        negative = bool(match.group(1)) or text.strip().startswith("(")
        return -amount if negative else amount
    return parse_number(text)


def _apply_meridiem(hour: int, meridiem: str | None) -> int:
    """Convert a 12-hour hour to 24-hour when am/pm is present."""
    if not meridiem:
        return hour
    meridiem = meridiem.casefold()
    if meridiem == "pm" and hour < 12:
        return hour + 12
    if meridiem == "am" and hour == 12:
        return 0
    return hour


def parse_time_range(value: Any) -> tuple[time, time] | None:
    """A ``09:00 - 10:00`` style range."""
    text = text_of(value)
    if not text:
        return None
    match = _TIME_RANGE_RE.search(text)
    if not match:
        return None
    start_h, start_m, start_mer, end_h, end_m, end_mer = match.groups()
    # "9:00 - 10:00am" means both ends are am.
    start_mer = start_mer or end_mer
    try:
        start = time(_apply_meridiem(int(start_h), start_mer), int(start_m))
        end = time(_apply_meridiem(int(end_h), end_mer), int(end_m))
    except ValueError:
        return None
    return start, end


def parse_clock(value: Any) -> time | None:
    """A single clock time."""
    text = text_of(value)
    if not text:
        return None
    match = _TIME_RE.search(text)
    if not match:
        return None
    hour, minute, meridiem = match.groups()
    try:
        return time(_apply_meridiem(int(hour), meridiem), int(minute))
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    """A calendar date in any of the formats Arbor uses."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = _from_timestamp(value)
        return parsed.date() if parsed else None
    text = text_of(value)
    if not text:
        return None

    # Strip an ordinal suffix and any trailing time before trying formats.
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", " ").strip()
    iso_candidate = cleaned.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        pass

    date_part = _TIME_RE.sub(" ", cleaned).strip()
    date_part = _WS_RE.sub(" ", date_part)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_part, fmt).date()
        except ValueError:
            continue

    this_year = date.today().year
    for fmt in _YEARLESS_DATE_FORMATS:
        try:
            return datetime.strptime(f"{date_part} {this_year}", f"{fmt} %Y").date()
        except ValueError:
            continue
    return None


def _from_timestamp(value: float) -> datetime | None:
    """A unix timestamp in seconds or milliseconds."""
    try:
        seconds = value / 1000 if abs(value) > 1e11 else value
        return datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    """A date, with a time when the value carries one."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_timestamp(value)
    text = text_of(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        pass
    day = parse_date(text)
    if day is None:
        return None
    clock = parse_clock(text)
    return datetime.combine(day, clock or time.min)


# ---------------------------------------------------------------------------
# column matching helpers
# ---------------------------------------------------------------------------


def _pick(row: dict[str, str], table: Table, *keywords: str) -> str | None:
    """Value from the first column whose caption matches a keyword."""
    column = table.column_matching(*keywords)
    if column and (value := row.get(column)):
        return value
    # Fall back to matching the row's own keys, for rows without column defs.
    for keyword in keywords:
        for key, value in row.items():
            if keyword in key.casefold() and value:
                return value
    return None


def _table_mentions(table: Table, *keywords: str) -> bool:
    """Whether a table's title or any column caption mentions a keyword."""
    haystack = " ".join([table.title or "", *table.columns]).casefold()
    return any(keyword in haystack for keyword in keywords)


# ---------------------------------------------------------------------------
# domain extractors
# ---------------------------------------------------------------------------


def extract_assignments(trees: list[Any]) -> list[Assignment]:
    """Homework and coursework from any assignment-shaped table."""
    assignments: list[Assignment] = []
    seen: set[tuple[str, str | None]] = set()

    for tree in trees:
        for table in find_tables(tree):
            if not _table_mentions(
                table, "assignment", "homework", "coursework", "due", "hand in", "submission"
            ):
                continue
            for row in table.rows:
                title = _pick(row, table, "assignment", "title", "name", "task", "homework")
                if not title:
                    continue
                due_raw = _pick(row, table, "due", "deadline", "hand in", "hand-in")
                key = (title.casefold(), due_raw)
                if key in seen:
                    continue
                seen.add(key)
                assignments.append(
                    Assignment(
                        title=title,
                        subject=_pick(row, table, "subject", "course", "class", "lesson"),
                        due=parse_datetime(due_raw) if due_raw else None,
                        status=_pick(row, table, "status", "submitted", "state", "progress"),
                        grade=_pick(row, table, "grade", "mark", "score", "result", "level"),
                        teacher=_pick(row, table, "teacher", "staff", "set by", "author"),
                    )
                )

    assignments.sort(key=lambda item: (item.due is None, item.due or datetime.max))
    return assignments


def extract_attendance(trees: list[Any]) -> AttendanceSummary:
    """Attendance percentage and session counts."""
    summary = AttendanceSummary()

    for tree in trees:
        for metric in find_metrics(tree):
            label = metric.label.casefold()
            if summary.percentage is None and "attendance" in label and "%" in metric.value:
                summary.percentage = parse_percentage(metric.value)
            elif summary.percentage is None and label.strip() in (
                "attendance",
                "attendance this year",
                "overall attendance",
                "yearly attendance",
            ):
                summary.percentage = parse_percentage(metric.value)
            if summary.present_sessions is None and "present" in label:
                summary.present_sessions = parse_int(metric.value)
            if summary.authorised_absences is None and "authorised" in label and "un" not in label:
                summary.authorised_absences = parse_int(metric.value)
            if summary.unauthorised_absences is None and "unauthorised" in label:
                summary.unauthorised_absences = parse_int(metric.value)
            if summary.late_sessions is None and "late" in label:
                summary.late_sessions = parse_int(metric.value)

        for table in find_tables(tree):
            if not _table_mentions(table, "attendance", "absence", "present", "mark"):
                continue
            for row in table.rows:
                if summary.percentage is None:
                    value = _pick(row, table, "attendance", "percentage", "%")
                    if value and "%" in value:
                        summary.percentage = parse_percentage(value)
                if summary.present_sessions is None:
                    summary.present_sessions = parse_int(_pick(row, table, "present"))
                if summary.authorised_absences is None:
                    authorised = _pick(row, table, "authorised absence", "authorised")
                    if authorised and "unauthorised" not in authorised.casefold():
                        summary.authorised_absences = parse_int(authorised)
                if summary.unauthorised_absences is None:
                    summary.unauthorised_absences = parse_int(_pick(row, table, "unauthorised"))
                if summary.late_sessions is None:
                    summary.late_sessions = parse_int(_pick(row, table, "late"))

    return summary


_POSITIVE_WORDS = ("positive", "achievement", "praise", "merit", "reward", "house point", "good")
_NEGATIVE_WORDS = ("negative", "behaviour concern", "concern", "sanction", "detention", "demerit")


def extract_behaviour(trees: list[Any]) -> tuple[float | None, float | None, list[BehaviourIncident]]:
    """Behaviour point totals and the incidents behind them."""
    positive: float | None = None
    negative: float | None = None
    incidents: list[BehaviourIncident] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()

    for tree in trees:
        for metric in find_metrics(tree):
            label = metric.label.casefold()
            if "point" not in label and "behaviour" not in label and "incident" not in label:
                continue
            value = parse_number(metric.value)
            if value is None:
                continue
            if positive is None and any(word in label for word in _POSITIVE_WORDS):
                positive = value
            elif negative is None and any(word in label for word in _NEGATIVE_WORDS):
                negative = abs(value)

        for table in find_tables(tree):
            if not _table_mentions(
                table, "behaviour", "behavior", "incident", "point", "conduct", "achievement"
            ):
                continue
            for row in table.rows:
                kind = _pick(row, table, "type", "behaviour", "incident", "reason", "category")
                occurred_raw = _pick(row, table, "date", "when", "time", "recorded")
                comment = _pick(row, table, "comment", "note", "detail", "description")
                key = (kind, occurred_raw, comment)
                if key in seen:
                    continue
                seen.add(key)
                points = parse_number(_pick(row, table, "point", "score", "value"))
                incident = BehaviourIncident(
                    occurred=parse_datetime(occurred_raw) if occurred_raw else None,
                    kind=kind,
                    points=points,
                    subject=_pick(row, table, "subject", "course", "lesson", "class"),
                    staff=_pick(row, table, "staff", "teacher", "recorded by", "logged by"),
                    comment=comment,
                )
                incidents.append(incident)

    # Fall back to summing the incident list when the portal shows no totals.
    if incidents and (positive is None or negative is None):
        summed_positive = sum(
            abs(item.points or 1) for item in incidents if item.is_positive
        )
        summed_negative = sum(
            abs(item.points or 1) for item in incidents if not item.is_positive
        )
        if positive is None:
            positive = float(summed_positive)
        if negative is None:
            negative = float(summed_negative)

    incidents.sort(
        key=lambda item: (item.occurred is None, item.occurred or datetime.min), reverse=True
    )
    return positive, negative, incidents


def extract_lessons_from_tables(trees: list[Any], on_day: date | None = None) -> list[Lesson]:
    """Timetable rows from any lesson-shaped table."""
    lessons: list[Lesson] = []
    day = on_day or date.today()

    for tree in trees:
        for table in find_tables(tree):
            if not _table_mentions(
                table, "lesson", "period", "timetable", "session", "subject", "class"
            ):
                continue
            for row in table.rows:
                summary = _pick(row, table, "subject", "lesson", "class", "course", "event")
                if not summary:
                    continue
                time_raw = _pick(row, table, "time", "period", "when", "slot")
                row_date = parse_date(_pick(row, table, "date", "day")) or day
                start_dt = end_dt = None
                span = parse_time_range(time_raw) if time_raw else None
                if span:
                    start_dt = datetime.combine(row_date, span[0])
                    end_dt = datetime.combine(row_date, span[1])
                else:
                    start_clock = parse_clock(_pick(row, table, "start", "from") or time_raw or "")
                    end_clock = parse_clock(_pick(row, table, "end", "finish", "to") or "")
                    if start_clock:
                        start_dt = datetime.combine(row_date, start_clock)
                    if end_clock:
                        end_dt = datetime.combine(row_date, end_clock)
                lessons.append(
                    Lesson(
                        summary=summary,
                        start=start_dt,
                        end=end_dt,
                        all_day_on=None if start_dt else row_date,
                        location=_pick(row, table, "room", "location", "venue"),
                        teacher=_pick(row, table, "teacher", "staff", "tutor", "led by"),
                        description=_pick(row, table, "note", "detail", "comment"),
                    )
                )

    lessons.sort(key=lambda lesson: lesson.sort_key)
    return lessons


def extract_lessons_from_calendar(payload: Any) -> list[Lesson]:
    """Events from Arbor's calendar JSON endpoints."""
    lessons: list[Lesson] = []
    for node in walk(payload):
        summary = None
        for key in ("title", "name", "summary", "subject", "eventName", "text"):
            if key in node:
                summary = text_of(node[key])
                if summary:
                    break
        if not summary:
            continue

        start = None
        for key in ("start", "startDate", "startDatetime", "start_date", "from", "startTime"):
            if key in node:
                start = parse_datetime(node[key])
                if start:
                    break
        end = None
        for key in ("end", "endDate", "endDatetime", "end_date", "to", "endTime"):
            if key in node:
                end = parse_datetime(node[key])
                if end:
                    break

        if start is None:
            day = None
            for key in ("date", "day", "eventDate"):
                if key in node:
                    day = parse_date(node[key])
                    if day:
                        break
            if day is None:
                continue
            lessons.append(
                Lesson(
                    summary=summary,
                    all_day_on=day,
                    location=text_of(node.get("location") or node.get("room")),
                    teacher=text_of(node.get("teacher") or node.get("staff")),
                )
            )
            continue

        # Guard against midnight-to-midnight entries being read as zero-length.
        if end is not None and end <= start:
            end = None

        lessons.append(
            Lesson(
                summary=summary,
                start=start,
                end=end,
                location=text_of(node.get("location") or node.get("room") or node.get("venue")),
                teacher=text_of(
                    node.get("teacher") or node.get("staff") or node.get("leadStaff")
                ),
                description=text_of(node.get("description") or node.get("notes")),
            )
        )

    unique: dict[tuple[str, Any], Lesson] = {}
    for lesson in lessons:
        unique.setdefault((lesson.summary, lesson.start or lesson.all_day_on), lesson)
    return sorted(unique.values(), key=lambda lesson: lesson.sort_key)


def extract_grades(trees: list[Any]) -> list[Grade]:
    """Reported marks per subject."""
    grades: list[Grade] = []
    seen: set[tuple[str, str | None]] = set()

    for tree in trees:
        for table in find_tables(tree):
            if not _table_mentions(table, "grade", "mark", "progress", "attainment", "assessment"):
                continue
            for row in table.rows:
                subject = _pick(row, table, "subject", "course", "qualification", "class")
                if not subject:
                    continue
                value = _pick(row, table, "grade", "mark", "result", "attainment", "level", "score")
                key = (subject.casefold(), value)
                if key in seen:
                    continue
                seen.add(key)
                grades.append(
                    Grade(
                        subject=subject,
                        value=value,
                        target=_pick(row, table, "target", "expected", "predicted"),
                        assessment=_pick(row, table, "assessment", "period", "term"),
                    )
                )
    return grades


def extract_accounts(trees: list[Any]) -> list[AccountBalance]:
    """Meal and top-up account balances."""
    accounts: list[AccountBalance] = []
    seen: set[str] = set()

    for tree in trees:
        for metric in find_metrics(tree):
            label = metric.label.casefold()
            if not any(
                word in label for word in ("meal", "lunch", "balance", "account", "dinner", "credit")
            ):
                continue
            balance = parse_currency(metric.value)
            if balance is None:
                continue
            name = metric.label.strip()
            if name.casefold() in seen:
                continue
            seen.add(name.casefold())
            accounts.append(AccountBalance(name=name, balance=balance))

        for table in find_tables(tree):
            if not _table_mentions(table, "account", "balance", "meal", "lunch"):
                continue
            for row in table.rows:
                name = _pick(row, table, "account", "name", "type", "description")
                balance = parse_currency(_pick(row, table, "balance", "amount", "credit", "total"))
                if not name or balance is None or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                accounts.append(AccountBalance(name=name, balance=balance))

    return accounts


def extract_notices(trees: list[Any]) -> list[Notice]:
    """School notices, news items and in-app messages."""
    notices: list[Notice] = []
    seen: set[str] = set()

    for tree in trees:
        for node in walk(tree):
            title = None
            for key in ("title", "subject", "heading", "name"):
                if key in node:
                    title = text_of(node[key])
                    if title:
                        break
            if not title:
                continue
            body = text_of(node.get("body") or node.get("content") or node.get("message"))
            published = None
            for key in ("createdAt", "created", "date", "publishedAt", "published", "sentAt"):
                if key in node:
                    published = parse_datetime(node[key])
                    if published:
                        break
            # Only keep nodes that actually read like a notice.
            if body is None and published is None:
                continue
            if title.casefold() in seen:
                continue
            seen.add(title.casefold())
            url = node.get("url") if isinstance(node.get("url"), str) else None
            notices.append(Notice(title=title, published=published, body=body, url=url))

        for table in find_tables(tree):
            if not _table_mentions(table, "notice", "news", "message", "bulletin"):
                continue
            for row in table.rows:
                title = _pick(row, table, "title", "subject", "notice", "message", "heading")
                if not title or title.casefold() in seen:
                    continue
                seen.add(title.casefold())
                notices.append(
                    Notice(
                        title=title,
                        published=parse_datetime(_pick(row, table, "date", "published", "sent")),
                        body=_pick(row, table, "body", "detail", "content", "summary"),
                    )
                )

    notices.sort(
        key=lambda item: (item.published is None, item.published or datetime.min), reverse=True
    )
    return notices


# ---------------------------------------------------------------------------
# students and navigation
# ---------------------------------------------------------------------------

# Only an explicitly student-scoped id identifies a child. A bare `/id/<n>`
# segment appears on nearly every Arbor URL -- calendar entries, notices,
# payments -- so matching that turned any dashboard link into a "child".
_STUDENT_ID_PATTERNS = (
    re.compile(r"/student[-_]?id/(\d+)", re.IGNORECASE),
    re.compile(r"/students?[-_]?profile/(?:[^/?]+/)*?id/(\d+)", re.IGNORECASE),
    re.compile(r"/students?/(?:view/)?(\d+)(?=/|$|\?)", re.IGNORECASE),
)

_NAME_RE = re.compile(
    r"^[^\W\d_][\w'’\-\.]*(?:\s+[^\W\d_][\w'’\-\.]*){1,4}$", re.UNICODE
)

# Vocabulary that appears in portal navigation but never in a person's name.
# Checked word by word, so "Next lesson" is rejected while "Amelia Example" is
# not -- a substring blocklist cannot tell those apart.
_NON_NAME_WORDS = frozenset(
    """
    view views profile profiles dashboard home homepage log logout login signout
    settings setting menu help support about contact
    attendance absence absences attend present absent late
    behaviour behavior conduct incident incidents point points
    assignment assignments homework coursework task tasks due overdue submitted
    calendar timetable schedule lesson lessons period periods session sessions
    event events class classes subject subjects room rooms
    payment payments invoice invoices shop basket checkout fee fees
    trip trips club clubs activity activities booking bookings
    meal meals lunch dinner catering balance account accounts credit topup
    notice notices news bulletin message messages letter letters communication
    report reports card cards exam exams examination examinations result results
    progress attainment grade grades mark marks target level
    current next previous last today tomorrow yesterday upcoming recent
    week weeks term terms year years day days time times date dates
    school student students child children guardian guardians parent parents
    detail details more all summary overview page pages link click here back
    add new edit update change remove delete cancel confirm save
    my your our the and for with from
    """.split()
)


def _looks_like_name(text: str) -> bool:
    """Whether a link caption reads like a person's name.

    Deliberately strict: a false positive invents a child, which is far more
    confusing than missing a name and falling back to "Student <id>".
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) > 60 or any(char.isdigit() for char in stripped):
        return False
    if not _NAME_RE.match(stripped):
        return False
    words = re.findall(r"[^\W\d_]+", stripped.casefold())
    if not words:
        return False
    return not any(word in _NON_NAME_WORDS for word in words)


def student_id_in_url(url: str) -> str | None:
    """The child id a URL is explicitly scoped to, if any."""
    for pattern in _STUDENT_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


@dataclass(slots=True)
class StudentRef:
    """A child found on the guardian dashboard."""

    student_id: str
    name: str
    url: str | None = None
    #: Where the name came from, for diagnostics: "record", "link" or "fallback".
    name_source: str = "fallback"


def extract_student_refs(trees: list[Any]) -> list[StudentRef]:
    """Find the children this guardian can see.

    Only URLs and records carrying an *explicitly* student-scoped id count. A
    bare ``/id/<n>`` is not enough: it appears on calendar entries, notices and
    payments too, and treating those as children produced entities named after
    lessons rather than after people.

    A record's own name beats a link caption, and any real name beats the
    ``Student <id>`` placeholder.
    """
    by_id: dict[str, StudentRef] = {}
    _RANK = {"fallback": 0, "link": 1, "record": 2}

    def remember(
        student_id: str, name: str | None, url: str | None, source: str
    ) -> None:
        existing = by_id.get(student_id)
        if existing is None:
            by_id[student_id] = StudentRef(
                student_id=student_id,
                name=name or f"Student {student_id}",
                url=url,
                name_source=source if name else "fallback",
            )
            return
        if name and _RANK[source] > _RANK[existing.name_source]:
            existing.name = name
            existing.name_source = source
        if url and not existing.url:
            existing.url = url

    for tree in trees:
        # Explicit student records, if the tree carries any.
        for node in walk(tree):
            raw_id = None
            for key in ("studentId", "student_id", "studentID"):
                if key in node:
                    raw_id = node[key]
                    break
            if raw_id is None:
                continue
            student_id = str(text_of(raw_id) or "").strip()
            if not student_id.isdigit():
                continue
            name = None
            for key in ("studentName", "student_name", "name", "displayName", "title", "text"):
                if key in node:
                    candidate = text_of(node[key])
                    if candidate and _looks_like_name(candidate):
                        name = candidate
                        break
            url = node.get("url") if isinstance(node.get("url"), str) else None
            remember(student_id, name, url, "record" if name else "fallback")

        for link in find_links(tree):
            student_id = student_id_in_url(link.url)
            if student_id is None:
                continue
            name = link.text if _looks_like_name(link.text) else None
            remember(student_id, name, link.url, "link" if name else "fallback")

    return sorted(by_id.values(), key=lambda ref: ref.name)


@lru_cache(maxsize=256)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Whole-word matcher for a caption keyword, tolerating a plural."""
    return re.compile(rf"\b{re.escape(keyword)}(?:s|es)?\b", re.IGNORECASE)


def caption_mentions(caption: str, keyword: str) -> bool:
    """Whether a link caption names a keyword as a word of its own.

    Plain substring matching is too greedy for captions: ``exam`` would match a
    child called "Oliver Example", filing their profile link as an examinations
    page. A plural is still allowed, so ``account`` matches "Accounts".
    """
    return _keyword_pattern(keyword).search(caption) is not None


def classify_pages(
    trees: list[Any], keywords: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, str]]:
    """Group discovered links into data domains by their caption.

    Returns ``{domain: {caption: url}}``. A link is filed under the first domain
    whose keywords its caption names, so the caller can fetch only the pages the
    school actually publishes.
    """
    found: dict[str, dict[str, str]] = {}
    for tree in trees:
        for link in find_links(tree):
            caption = link.text.strip()
            if not caption or not link.url.startswith("/"):
                continue
            # A person's name is never a data page, whatever words it contains.
            if _looks_like_name(caption):
                continue
            for domain, domain_keywords in keywords.items():
                if any(caption_mentions(caption, keyword) for keyword in domain_keywords):
                    found.setdefault(domain, {}).setdefault(caption, link.url)
                    break
    return found


def filter_pages_for_student(
    pages: dict[str, dict[str, str]], student_id: str, *, keep_unscoped: bool
) -> dict[str, dict[str, str]]:
    """Drop discovered URLs that belong to a different child.

    Guardian page URLs carry the child's id, so a link found on a shared page
    such as the dashboard may point at a sibling. URLs with no id at all are
    scoped to whichever child the portal currently has selected, which is only
    unambiguous for a guardian with one child.
    """
    filtered: dict[str, dict[str, str]] = {}
    for domain, entries in pages.items():
        for caption, url in entries.items():
            url_student_id = student_id_in_url(url)
            if url_student_id is not None:
                if url_student_id != student_id:
                    continue
            elif not keep_unscoped:
                continue
            filtered.setdefault(domain, {})[caption] = url
    return filtered


def extract_profile_fields(trees: list[Any]) -> dict[str, str]:
    """Labelled profile values such as year group and form group."""
    fields: dict[str, str] = {}
    for tree in trees:
        for metric in find_metrics(tree):
            key = metric.label.strip().casefold().rstrip(":")
            fields.setdefault(key, metric.value)
    return fields
