"""Shared entity base for the Arbor integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ArborCoordinator
from .models import StudentData


class ArborStudentEntity(CoordinatorEntity[ArborCoordinator]):
    """Base entity for anything reported about one child."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ArborCoordinator, student_id: str, key: str
    ) -> None:
        """Bind the entity to a child and give it a stable unique id."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{student_id}_{key}"

    @property
    def student(self) -> StudentData | None:
        """The child's latest data, or None if they are no longer listed."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.students.get(self._student_id)

    @property
    def available(self) -> bool:
        """Whether the last refresh produced data for this child."""
        return super().available and self.student is not None

    @property
    def device_info(self) -> DeviceInfo:
        """One HA device per child, so their entities group together."""
        student = self.student
        school = self.coordinator.data.school_name if self.coordinator.data else None
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.config_entry.entry_id}_{self._student_id}")},
            name=student.name if student else self._student_id,
            manufacturer=MANUFACTURER,
            model=school or "Arbor Parent Portal",
            configuration_url=self.coordinator.client.base_url,
        )
