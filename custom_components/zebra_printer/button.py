"""Button platform for Zebra Printer."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory

from .const import DOMAIN
from .coordinator import ZebraPrinterCoordinator
from .entity import ZebraPrinterEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Button keys
BUTTON_CALIBRATE = "calibrate"
BUTTON_FEED = "feed"


@dataclass(frozen=True, kw_only=True)
class ZebraButtonEntityDescription(ButtonEntityDescription):
    """Describes a Zebra button entity."""

    press_fn: Callable[[ZebraPrinterCoordinator], Coroutine[Any, Any, bool]]


BUTTONS: tuple[ZebraButtonEntityDescription, ...] = (
    ZebraButtonEntityDescription(
        key=BUTTON_CALIBRATE,
        translation_key=BUTTON_CALIBRATE,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_calibrate(),
    ),
    ZebraButtonEntityDescription(
        key=BUTTON_FEED,
        translation_key=BUTTON_FEED,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_feed(1),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zebra Printer buttons."""
    coordinator: ZebraPrinterCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [ZebraButton(coordinator, description) for description in BUTTONS]
    async_add_entities(entities)


class ZebraButton(ZebraPrinterEntity, ButtonEntity):
    """Button for Zebra Printer."""

    entity_description: ZebraButtonEntityDescription

    def __init__(
        self,
        coordinator: ZebraPrinterCoordinator,
        description: ZebraButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.coordinator)
