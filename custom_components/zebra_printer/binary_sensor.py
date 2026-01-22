"""Binary sensor platform for Zebra Printer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .const import (
    BINARY_SENSOR_BUFFER_FULL,
    BINARY_SENSOR_HAS_ERROR,
    BINARY_SENSOR_HEAD_OPEN,
    BINARY_SENSOR_PAPER_OUT,
    BINARY_SENSOR_PAUSED,
    BINARY_SENSOR_READY,
    BINARY_SENSOR_RIBBON_OUT,
    DOMAIN,
    PROTOCOL_ZPL,
)
from .coordinator import ZebraPrinterCoordinator
from .entity import ZebraPrinterEntity
from .protocol import PrinterStatus

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


@dataclass(frozen=True, kw_only=True)
class ZebraBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Zebra binary sensor entity."""

    value_fn: Callable[[PrinterStatus], bool | None]
    zpl_only: bool = False


BINARY_SENSORS: tuple[ZebraBinarySensorEntityDescription, ...] = (
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_READY,
        translation_key=BINARY_SENSOR_READY,
        # No device_class - will show state from translations
        value_fn=lambda data: data.online and not data.has_error,
    ),
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_HEAD_OPEN,
        translation_key=BINARY_SENSOR_HEAD_OPEN,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.head_open,
    ),
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_PAPER_OUT,
        translation_key=BINARY_SENSOR_PAPER_OUT,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.paper_out,
    ),
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_RIBBON_OUT,
        translation_key=BINARY_SENSOR_RIBBON_OUT,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.ribbon_out,
        zpl_only=True,
    ),
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_PAUSED,
        translation_key=BINARY_SENSOR_PAUSED,
        # No device_class - will show On/Off, translations provide "Paused"/"Not Paused"
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.paused,
    ),
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_BUFFER_FULL,
        translation_key=BINARY_SENSOR_BUFFER_FULL,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.buffer_full,
        zpl_only=True,
    ),
    ZebraBinarySensorEntityDescription(
        key=BINARY_SENSOR_HAS_ERROR,
        translation_key=BINARY_SENSOR_HAS_ERROR,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.has_error,
        zpl_only=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zebra Printer binary sensors."""
    coordinator: ZebraPrinterCoordinator = hass.data[DOMAIN][entry.entry_id]
    is_zpl = coordinator.protocol_type == PROTOCOL_ZPL

    entities = []
    for description in BINARY_SENSORS:
        # Skip ZPL-only sensors for EPL2 printers
        if description.zpl_only and not is_zpl:
            continue
        entities.append(ZebraBinarySensor(coordinator, description))

    async_add_entities(entities)


class ZebraBinarySensor(ZebraPrinterEntity, BinarySensorEntity):
    """Binary sensor for Zebra Printer."""

    entity_description: ZebraBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: ZebraPrinterCoordinator,
        description: ZebraBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Ready sensor is unavailable when we can't reach the printer
        if self.entity_description.key == BINARY_SENSOR_READY:
            return self.coordinator.data is not None and self.coordinator.data.online
        # Other sensors are only available when printer is online
        return self.coordinator.data is not None and self.coordinator.data.online
