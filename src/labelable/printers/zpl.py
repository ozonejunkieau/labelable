"""Zebra ZPL printer implementation."""

import asyncio
import logging
import os

import aiohttp
import serial

from labelable.models.printer import HAConnection, PrinterConfig, SerialConnection, TCPConnection
from labelable.printers.base import BasePrinter, PrinterError

logger = logging.getLogger(__name__)


class ZPLPrinter(BasePrinter):
    """Zebra ZPL printer implementation supporting TCP, serial, and HA connections."""

    def __init__(self, config: PrinterConfig) -> None:
        super().__init__(config)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._serial: serial.Serial | None = None
        # HA connection state
        self._ha_session: aiohttp.ClientSession | None = None
        self._ha_device_id: str | None = None
        self._ha_url: str | None = None

    async def connect(self) -> None:
        """Establish connection to the ZPL printer."""
        if self._connected:
            return

        conn = self.config.connection
        if isinstance(conn, TCPConnection):
            await self._connect_tcp(conn)
        elif isinstance(conn, SerialConnection):
            await self._connect_serial(conn)
        elif isinstance(conn, HAConnection):
            await self._connect_ha(conn)
        else:
            raise PrinterError(f"Unsupported connection type for ZPL: {type(conn)}")

        self._connected = True

    async def _connect_tcp(self, conn: TCPConnection) -> None:
        """Connect via TCP socket."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(conn.host, conn.port),
                timeout=5.0,
            )
        except TimeoutError as e:
            raise ConnectionError(f"Timeout connecting to {conn.host}:{conn.port}") from e
        except OSError as e:
            raise ConnectionError(f"Failed to connect to {conn.host}:{conn.port}: {e}") from e

    async def _connect_serial(self, conn: SerialConnection) -> None:
        """Connect via serial port."""
        try:
            self._serial = serial.Serial(
                port=conn.device,
                baudrate=conn.baudrate,
                bytesize=conn.bytesize,
                parity=conn.parity,
                stopbits=conn.stopbits,
                timeout=5.0,
            )
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to open serial port {conn.device}: {e}") from e

    async def _connect_ha(self, conn: HAConnection) -> None:
        """Connect via Home Assistant zebra_printer integration."""
        headers = {}
        if conn.ha_token:
            headers["Authorization"] = f"Bearer {conn.ha_token}"
        elif os.environ.get("SUPERVISOR_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['SUPERVISOR_TOKEN']}"

        self._ha_session = aiohttp.ClientSession(headers=headers)
        self._ha_device_id = conn.device_id
        self._ha_url = conn.ha_url.rstrip("/")

    async def _send_via_ha(self, data: bytes) -> None:
        """Send data to printer via Home Assistant service call."""
        if not self._ha_session:
            raise ConnectionError("HA session not initialized")

        async with self._ha_session.post(
            f"{self._ha_url}/api/services/zebra_printer/print_raw",
            json={"device_id": self._ha_device_id, "data": data.decode("latin-1")},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ConnectionError(f"HA service call failed: {resp.status} - {text}")

    async def disconnect(self) -> None:
        """Close connection to the printer."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        if self._serial:
            self._serial.close()
            self._serial = None

        if self._ha_session:
            await self._ha_session.close()
            self._ha_session = None
            self._ha_device_id = None
            self._ha_url = None

        self._connected = False

    async def is_online(self) -> bool:
        """Check if the ZPL printer is online.

        Sends a healthcheck command and checks for response.
        Default command is ~HS (host status query).
        """
        if not self._connected:
            try:
                await self.connect()
            except (ConnectionError, PrinterError) as e:
                logger.warning(f"Printer {self.name}: connection failed - {e}")
                self._update_cache(False)
                return False

        try:
            # Use configured healthcheck command or default
            command = self.config.healthcheck.command or "~HS"
            await self._send(command.encode() + b"\r\n")
            # Try to read response with timeout
            response = await self._recv(timeout=2.0)
            # ZPL printers respond with status info
            online = len(response) > 0
            self._update_cache(online)

            if not online:
                logger.warning(f"Printer {self.name}: no response to healthcheck")

            # Fetch model info once using ~HI (Host Identification)
            if online and self._model_info is None:
                try:
                    await self._send(b"~HI\r\n")
                    hi_response = await self._recv(timeout=2.0)
                    if hi_response:
                        # Response: "\x02GK420d-200dpi,V61.17.16Z,8,2104KB\x03\r\n"
                        text = hi_response.decode("utf-8", errors="ignore")
                        # Strip control chars and parse
                        text = text.strip().strip("\x02\x03")
                        parts = text.split(",")
                        if len(parts) >= 2:
                            self._model_info = f"{parts[0]} {parts[1]}"
                        elif parts:
                            self._model_info = parts[0]
                except Exception:
                    pass

            return online
        except Exception as e:
            logger.warning(f"Printer {self.name}: status check failed - {e}")
            self._connected = False
            self._update_cache(False)
            return False

    async def get_media_size(self) -> tuple[float, float] | None:
        """Query media size from ZPL printer.

        ZPL printers don't typically report media size automatically.
        This would require specific printer model support.
        """
        # ZPL printers don't have a standard media query command
        # Size is typically configured on the printer itself
        return None

    async def print_raw(self, data: bytes) -> None:
        """Send raw ZPL data to the printer."""
        if not self._connected:
            raise ConnectionError("Printer not connected")

        await self._send(data)

    async def print_with_quantity(self, data: bytes, quantity: int) -> None:
        """Send ZPL data with quantity handling.

        If the data contains ^PQ (Print Quantity) command, the printer handles
        quantity natively and we send once. Otherwise, we loop quantity times.
        """
        if b"^PQ" in data:
            # Template uses native ZPL quantity command
            await self.print_raw(data)
        else:
            # Fall back to looping
            for _ in range(quantity):
                await self.print_raw(data)

    async def _send(self, data: bytes) -> None:
        """Send data to the printer."""
        if self._writer:
            self._writer.write(data)
            await self._writer.drain()
        elif self._serial:
            # Run blocking serial write in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._serial.write, data)
        elif self._ha_session:
            await self._send_via_ha(data)
        else:
            raise ConnectionError("No connection available")

    async def _recv(self, size: int = 1024, timeout: float = 5.0) -> bytes:
        """Receive data from the printer."""
        if self._reader:
            try:
                return await asyncio.wait_for(self._reader.read(size), timeout=timeout)
            except TimeoutError:
                return b""
        elif self._serial:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._serial.read, size)
        else:
            raise ConnectionError("No connection available")
