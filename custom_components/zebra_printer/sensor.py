"""Sensor platform for Zebra Printer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength

from .const import (
    DOMAIN,
    PROTOCOL_ZPL,
    SENSOR_DARKNESS,
    SENSOR_ERRORS,
    SENSOR_FIRMWARE,
    SENSOR_HEAD_DISTANCE,
    SENSOR_LABEL_LENGTH,
    SENSOR_MODEL,
    SENSOR_PRINT_MODE,
    SENSOR_PRINT_SPEED,
    SENSOR_PRINT_WIDTH,
    SENSOR_WARNINGS,
)
from .coordinator import ZebraPrinterCoordinator
from .entity import ZebraPrinterEntity
from .protocol import PrinterStatus

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


@dataclass(frozen=True, kw_only=True)
class ZebraSensorEntityDescription(SensorEntityDescription):
    """Describes a Zebra sensor entity."""

    value_fn: Callable[[PrinterStatus], Any]
    zpl_only: bool = False


SENSORS: tuple[ZebraSensorEntityDescription, ...] = (
    ZebraSensorEntityDescription(
        key=SENSOR_MODEL,
        translation_key=SENSOR_MODEL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.model,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_FIRMWARE,
        translation_key=SENSOR_FIRMWARE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.firmware,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_HEAD_DISTANCE,
        translation_key=SENSOR_HEAD_DISTANCE,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.head_distance_cm,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_PRINT_SPEED,
        translation_key=SENSOR_PRINT_SPEED,
        native_unit_of_measurement="ips",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.print_speed,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_DARKNESS,
        translation_key=SENSOR_DARKNESS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.darkness,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_LABEL_LENGTH,
        translation_key=SENSOR_LABEL_LENGTH,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.label_length_mm,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_PRINT_WIDTH,
        translation_key=SENSOR_PRINT_WIDTH,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.print_width_mm,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_PRINT_MODE,
        translation_key=SENSOR_PRINT_MODE,
        device_class=SensorDeviceClass.ENUM,
        options=[
            "tear_off",
            "peel_off",
            "rewind",
            "cutter",
            "delayed_cut",
            "rfid",
            "applicator",
            "unknown",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.print_mode,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_ERRORS,
        translation_key=SENSOR_ERRORS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.error_flags,
        zpl_only=True,
    ),
    ZebraSensorEntityDescription(
        key=SENSOR_WARNINGS,
        translation_key=SENSOR_WARNINGS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.warning_flags,
        zpl_only=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zebra Printer sensors."""
    coordinator: ZebraPrinterCoordinator = hass.data[DOMAIN][entry.entry_id]
    is_zpl = coordinator.protocol_type == PROTOCOL_ZPL

    entities = []
    for description in SENSORS:
        # Skip ZPL-only sensors for EPL2 printers
        if description.zpl_only and not is_zpl:
            continue
        entities.append(ZebraSensor(coordinator, description))

    async_add_entities(entities)


class ZebraSensor(ZebraPrinterEntity, SensorEntity):
    """Sensor for Zebra Printer."""

    entity_description: ZebraSensorEntityDescription

    def __init__(
        self,
        coordinator: ZebraPrinterCoordinator,
        description: ZebraSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data is not None and self.coordinator.data.online
