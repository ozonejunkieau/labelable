"""Brother P-Touch printer implementation via USB."""

import asyncio
import logging
from typing import Any

import usb.core
import usb.util

from labelable.models.printer import PrinterConfig, USBConnection
from labelable.printers.base import BasePrinter, PrinterError
from labelable.printers.ptouch_protocol import (
    CMD_INITIALIZE,
    CMD_RESET,
    CMD_STATUS_REQUEST,
    STATUS_RESPONSE_LENGTH,
    StatusResponse,
    parse_status,
)

logger = logging.getLogger(__name__)


class PTouchPrinter(BasePrinter):
    """Brother P-Touch printer implementation over USB.

    Uses PyUSB to communicate with the printer via the Brother raster (PTCBP) protocol.
    """

    def __init__(self, config: PrinterConfig) -> None:
        super().__init__(config)
        self._usb_dev: Any | None = None  # usb.core.Device
        self._usb_ep_out: Any | None = None
        self._usb_ep_in: Any | None = None
        self._last_status: StatusResponse | None = None

    async def connect(self) -> None:
        """Open USB connection and send init sequence."""
        if self._connected:
            return

        conn = self.config.connection
        if not isinstance(conn, USBConnection):
            raise PrinterError(f"P-Touch requires USB connection, got {type(conn).__name__}")

        loop = asyncio.get_event_loop()

        def _open() -> tuple[Any, Any, Any]:
            dev = usb.core.find(idVendor=conn.vendor_id, idProduct=conn.product_id)
            if dev is None:
                raise ConnectionError(f"USB device {conn.vendor_id:04x}:{conn.product_id:04x} not found")
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
            dev.set_configuration()
            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]
            ep_out = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
            )
            ep_in = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN,
            )
            if ep_out is None or ep_in is None:
                raise ConnectionError("USB endpoints not found")
            return dev, ep_out, ep_in

        try:
            self._usb_dev, self._usb_ep_out, self._usb_ep_in = await loop.run_in_executor(None, _open)
        except usb.core.USBError as e:
            raise ConnectionError(f"USB connection failed: {e}") from e

        # Send init sequence: flush buffer → reset
        await self._send(CMD_INITIALIZE)
        await self._send(CMD_RESET)

        self._connected = True
        logger.info(f"Printer {self.name}: connected via USB")

    async def disconnect(self) -> None:
        """Close USB connection."""
        if self._usb_dev is not None:
            try:
                usb.util.dispose_resources(self._usb_dev)
            except Exception:
                pass
            self._usb_dev = None
            self._usb_ep_out = None
            self._usb_ep_in = None

        self._connected = False

    async def is_online(self) -> bool:
        """Send status request and parse response."""
        if not self._connected:
            try:
                await self.connect()
            except (ConnectionError, PrinterError) as e:
                logger.warning(f"Printer {self.name}: connection failed - {e}")
                self._update_cache(False)
                return False

        try:
            await self._send(CMD_STATUS_REQUEST)
            response = await self._recv(size=STATUS_RESPONSE_LENGTH, timeout=3.0)

            if len(response) != STATUS_RESPONSE_LENGTH:
                logger.warning(f"Printer {self.name}: expected {STATUS_RESPONSE_LENGTH} bytes, got {len(response)}")
                self._update_cache(False)
                return False

            status = parse_status(response)
            self._last_status = status

            online = not status.has_errors
            self._update_cache(online)

            if self._model_info is None:
                kind = status.media_kind.name.replace("_", " ").title()
                self._model_info = f"P-Touch ({status.media_width_mm}mm {kind})"

            if status.has_errors:
                logger.warning(f"Printer {self.name}: errors - {', '.join(status.error_descriptions)}")

            return online
        except Exception as e:
            logger.warning(f"Printer {self.name}: status check failed - {e}")
            self._connected = False
            self._update_cache(False)
            return False

    async def get_media_size(self) -> tuple[float, float] | None:
        """Return media width from last status query.

        Returns (width_mm, 0) since P-Touch uses continuous tape.
        """
        if self._last_status and self._last_status.media_width_mm > 0:
            return (float(self._last_status.media_width_mm), 0.0)
        return None

    async def check_media_width(self, expected_width_mm: int) -> None:
        """Verify the loaded tape width matches the expected width.

        Queries printer status and compares media_width_mm.

        Args:
            expected_width_mm: Expected tape width in mm.

        Raises:
            PrinterError: If media width doesn't match or status query fails.
        """
        if not self._connected:
            raise PrinterError("Printer not connected")

        await self._send(CMD_STATUS_REQUEST)
        response = await self._recv(size=STATUS_RESPONSE_LENGTH, timeout=3.0)

        if len(response) != STATUS_RESPONSE_LENGTH:
            raise PrinterError(f"Failed to read printer status (got {len(response)} bytes)")

        status = parse_status(response)
        self._last_status = status

        if status.has_errors:
            raise PrinterError(f"Printer has errors: {', '.join(status.error_descriptions)}")

        if status.media_width_mm != expected_width_mm:
            raise PrinterError(
                f"Media width mismatch: printer has {status.media_width_mm}mm "
                f"tape loaded, but template requires {expected_width_mm}mm"
            )

    async def print_raw(self, data: bytes) -> None:
        """Send raw raster data to the printer."""
        if not self._connected:
            raise ConnectionError("Printer not connected")

        await self._send(data)

    async def _send(self, data: bytes) -> None:
        """Send data via USB bulk endpoint."""
        if self._usb_ep_out is None:
            raise ConnectionError("USB not connected")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._usb_ep_out.write, data)

    async def _recv(self, size: int = 1024, timeout: float = 3.0) -> bytes:
        """Read data from USB bulk endpoint."""
        if self._usb_ep_in is None:
            raise ConnectionError("USB not connected")

        timeout_ms = int(timeout * 1000)

        def _usb_read() -> bytes:
            try:
                data = self._usb_ep_in.read(size, timeout=timeout_ms)
                return bytes(data)
            except usb.core.USBTimeoutError:
                return b""

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _usb_read)
