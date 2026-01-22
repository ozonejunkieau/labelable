"""Services for Zebra Printer integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import ZebraPrinterCoordinator

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_PRINT_RAW = "print_raw"
SERVICE_CALIBRATE = "calibrate"
SERVICE_FEED = "feed"

# Service field names
ATTR_DATA = "data"
ATTR_COUNT = "count"
ATTR_DEVICE_ID = "device_id"

# Service schemas
SERVICE_PRINT_RAW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_DATA): cv.string,
    }
)

SERVICE_CALIBRATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)

SERVICE_FEED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_COUNT, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
    }
)


def get_coordinator_for_device(hass: HomeAssistant, device_id: str) -> ZebraPrinterCoordinator:
    """Get coordinator for a device ID."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    if device is None:
        raise HomeAssistantError(f"Device {device_id} not found")

    # Find the config entry for this device
    for entry_id in device.config_entries:
        if entry_id in hass.data.get(DOMAIN, {}):
            return hass.data[DOMAIN][entry_id]

    raise HomeAssistantError(f"No Zebra printer found for device {device_id}")


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for Zebra Printer integration."""

    async def handle_print_raw(call: ServiceCall) -> None:
        """Handle print_raw service call."""
        device_id = call.data[ATTR_DEVICE_ID]
        data = call.data[ATTR_DATA]

        coordinator = get_coordinator_for_device(hass, device_id)

        success = await coordinator.async_send_raw(data)
        if not success:
            raise HomeAssistantError(f"Failed to send data to printer {coordinator.host}")

    async def handle_calibrate(call: ServiceCall) -> None:
        """Handle calibrate service call."""
        device_id = call.data[ATTR_DEVICE_ID]

        coordinator = get_coordinator_for_device(hass, device_id)

        success = await coordinator.async_calibrate()
        if not success:
            raise HomeAssistantError(f"Failed to calibrate printer {coordinator.host}")

    async def handle_feed(call: ServiceCall) -> None:
        """Handle feed service call."""
        device_id = call.data[ATTR_DEVICE_ID]
        count = call.data.get(ATTR_COUNT, 1)

        coordinator = get_coordinator_for_device(hass, device_id)

        success = await coordinator.async_feed(count)
        if not success:
            raise HomeAssistantError(f"Failed to feed labels on printer {coordinator.host}")

    hass.services.async_register(
        DOMAIN,
        SERVICE_PRINT_RAW,
        handle_print_raw,
        schema=SERVICE_PRINT_RAW_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE,
        handle_calibrate,
        schema=SERVICE_CALIBRATE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_FEED,
        handle_feed,
        schema=SERVICE_FEED_SCHEMA,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Zebra Printer services."""
    # Only unload if this is the last config entry
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_PRINT_RAW)
        hass.services.async_remove(DOMAIN, SERVICE_CALIBRATE)
        hass.services.async_remove(DOMAIN, SERVICE_FEED)
