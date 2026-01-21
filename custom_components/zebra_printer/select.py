"""Select platform for Zebra Printer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN, PROTOCOL_ZPL, SENSOR_PRINT_METHOD
from .coordinator import ZebraPrinterCoordinator
from .entity import ZebraPrinterEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Print method options
PRINT_METHOD_DIRECT = "direct_thermal"
PRINT_METHOD_TRANSFER = "thermal_transfer"

PRINT_METHOD_OPTIONS = [PRINT_METHOD_DIRECT, PRINT_METHOD_TRANSFER]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zebra Printer select entities."""
    coordinator: ZebraPrinterCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only add print method select for ZPL printers
    if coordinator.protocol_type == PROTOCOL_ZPL:
        async_add_entities([ZebraPrintMethodSelect(coordinator)])


class ZebraPrintMethodSelect(ZebraPrinterEntity, SelectEntity):
    """Select entity for print method (Direct Thermal / Thermal Transfer)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = PRINT_METHOD_OPTIONS
    _attr_translation_key = SENSOR_PRINT_METHOD

    def __init__(self, coordinator: ZebraPrinterCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, SENSOR_PRINT_METHOD)

    @property
    def current_option(self) -> str | None:
        """Return the current print method."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.print_method

    async def async_select_option(self, option: str) -> None:
        """Change the print method."""
        await self.coordinator.async_set_print_method(option)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data is not None and self.coordinator.data.online
