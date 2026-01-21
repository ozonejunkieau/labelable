"""Data update coordinator for Zebra Printer."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_PROTOCOL, DEFAULT_PORT, DOMAIN, PROTOCOL_ZPL, SCAN_INTERVAL
from .protocol import EPL2Protocol, PrinterProtocol, PrinterStatus, ZPLProtocol

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class ZebraPrinterCoordinator(DataUpdateCoordinator[PrinterStatus]):
    """Coordinator to manage fetching printer status."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.config_entry = entry
        self._host = entry.data[CONF_HOST]
        self._port = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self._protocol_type = entry.data.get(CONF_PROTOCOL, PROTOCOL_ZPL)
        self._protocol: PrinterProtocol | None = None

    @property
    def host(self) -> str:
        """Return printer host."""
        return self._host

    @property
    def port(self) -> int:
        """Return printer port."""
        return self._port

    @property
    def protocol_type(self) -> str:
        """Return protocol type (zpl or epl2)."""
        return self._protocol_type

    def _get_protocol(self) -> PrinterProtocol:
        """Create protocol instance."""
        if self._protocol_type == PROTOCOL_ZPL:
            return ZPLProtocol(self._host, self._port)
        return EPL2Protocol(self._host, self._port)

    async def _async_update_data(self) -> PrinterStatus:
        """Fetch data from printer."""
        protocol = self._get_protocol()

        try:
            if not await protocol.connect():
                # Return offline status if we can't connect
                return PrinterStatus(online=False)

            status = await protocol.get_status()
            return status

        except (TimeoutError, OSError) as err:
            _LOGGER.debug("Error communicating with printer %s: %s", self._host, err)
            return PrinterStatus(online=False)

        except Exception as err:
            _LOGGER.exception("Unexpected error from printer %s: %s", self._host, err)
            raise UpdateFailed(f"Error communicating with printer: {err}") from err

        finally:
            await protocol.disconnect()

    async def async_send_raw(self, data: str) -> bool:
        """Send raw data to printer."""
        protocol = self._get_protocol()

        try:
            if not await protocol.connect():
                return False

            success = await protocol.send_raw(data.encode())
            return success

        except (TimeoutError, OSError):
            return False

        finally:
            await protocol.disconnect()

    async def async_calibrate(self) -> bool:
        """Run printer calibration."""
        protocol = self._get_protocol()

        try:
            if not await protocol.connect():
                return False

            command = protocol.get_calibrate_command()
            success = await protocol.send_raw(f"{command}\r\n".encode())
            return success

        except (TimeoutError, OSError):
            return False

        finally:
            await protocol.disconnect()

    async def async_feed(self, count: int = 1) -> bool:
        """Feed labels."""
        protocol = self._get_protocol()

        try:
            if not await protocol.connect():
                return False

            command = protocol.get_feed_command(count)
            success = await protocol.send_raw(f"{command}\r\n".encode())
            return success

        except (TimeoutError, OSError):
            return False

        finally:
            await protocol.disconnect()

    async def async_set_print_method(self, method: str) -> bool:
        """Set print method (direct_thermal or thermal_transfer).

        Uses ZPL ^MT command:
        - ^MTT = Thermal Transfer
        - ^MTD = Direct Thermal
        """
        if self._protocol_type != PROTOCOL_ZPL:
            _LOGGER.warning("set_print_method is only supported on ZPL printers")
            return False

        protocol = self._get_protocol()

        try:
            if not await protocol.connect():
                return False

            # ^MT command: T=thermal transfer, D=direct thermal
            method_code = "T" if method == "thermal_transfer" else "D"
            command = f"^XA^MT{method_code}^XZ"
            success = await protocol.send_raw(command.encode())

            # Refresh status after changing
            if success:
                await self.async_request_refresh()

            return success

        except (TimeoutError, OSError):
            return False

        finally:
            await protocol.disconnect()
