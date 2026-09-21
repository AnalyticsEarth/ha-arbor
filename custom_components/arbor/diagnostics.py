"""Diagnostics support for the Arbor integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ArborConfigEntry
from .const import CONF_EMAIL, CONF_PASSWORD

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}

_DIGITS = re.compile(r"\d+")


def _url_shape(url: str | None) -> str | None:
    """A URL with every number masked.

    The shape is what matters when discovery goes wrong -- seeing
    ``/guardians/calendar-entry/view-event/id/<n>`` listed as a child says
    immediately that lessons were mistaken for people -- while the masking keeps
    the child's actual id out of the report.
    """
    if not url:
        return None
    return _DIGITS.sub("<n>", url)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ArborConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Children's names and the raw portal payloads are personal data, so the dump
    reports the shape of what was scraped rather than its contents. Use the
    ``arbor.dump_page`` service when a payload itself needs inspecting.
    """
    coordinator = entry.runtime_data
    data = coordinator.data

    students: list[dict[str, Any]] = []
    if data is not None:
        for student_id, student in data.students.items():
            students.append(
                {
                    "student_id_hash": f"...{student_id[-3:]}",
                    "has_profile_url": student.profile_url is not None,
                    "profile_url_shape": _url_shape(student.profile_url),
                    "name_looks_like_a_person": not student.name.startswith("Student "),
                    "attendance_percentage_found": student.attendance.percentage is not None,
                    "behaviour_points_found": student.behaviour_points_net is not None,
                    "counts": {
                        "assignments": len(student.assignments),
                        "behaviour_incidents": len(student.behaviour_incidents),
                        "lessons": len(student.lessons),
                        "grades": len(student.grades),
                        "accounts": len(student.accounts),
                        "notices": len(student.notices),
                    },
                    "pages_scraped": sorted(student.raw),
                    "empty_domains": sorted(student.empty_domains),
                    "sourced_domains": sorted(student.sourced_domains),
                    "unsourced_domains": sorted(student.unsourced_domains),
                }
            )

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "base_url": coordinator.client.base_url,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds() if coordinator.update_interval else None
        ),
        "last_update_success": coordinator.last_update_success,
        "refused_endpoints": sorted(coordinator.scraper.unavailable),
        "student_count": len(data.students) if data else 0,
        "discovered_pages": (
            {
                domain: [_url_shape(url) for url in entries.values()]
                for domain, entries in data.discovered_pages.items()
            }
            if data
            else {}
        ),
        "warnings": data.warnings if data else [],
        "students": students,
    }
