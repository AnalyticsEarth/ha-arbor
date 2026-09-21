"""Diagnostics support for the Arbor integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ArborConfigEntry
from .const import CONF_EMAIL, CONF_PASSWORD

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}


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
        "discovered_pages": data.discovered_pages if data else {},
        "warnings": data.warnings if data else [],
        "students": students,
    }
