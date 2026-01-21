"""Base entity for Zebra Printer."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZebraPrinterCoordinator


class ZebraPrinterEntity(CoordinatorEntity[ZebraPrinterCoordinator]):
    """Base entity for Zebra Printer."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZebraPrinterCoordinator,
        entity_key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entity_key = entity_key

        # Build unique ID from config entry and entity key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{entity_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        data = self.coordinator.data

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name=self.coordinator.config_entry.title,
            manufacturer="Zebra Technologies",
            model=data.model if data else None,
            sw_version=data.firmware if data else None,
            configuration_url=f"http://{self.coordinator.host}",
        )
