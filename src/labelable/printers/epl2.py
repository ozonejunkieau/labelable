"""Zebra EPL2 printer implementation."""

import asyncio
import logging

import serial

from labelable.models.printer import PrinterConfig, SerialConnection, TCPConnection
from labelable.printers.base import BasePrinter, PrinterError

logger = logging.getLogger(__name__)


class EPL2Printer(BasePrinter):
    """Zebra EPL2 printer implementation supporting TCP and serial connections."""

    def __init__(self, config: PrinterConfig) -> None:
        super().__init__(config)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._serial: serial.Serial | None = None

    async def connect(self) -> None:
        """Establish connection to the EPL2 printer."""
        if self._connected:
            return

        conn = self.config.connection
        if isinstance(conn, TCPConnection):
            await self._connect_tcp(conn)
        elif isinstance(conn, SerialConnection):
            await self._connect_serial(conn)
        else:
            raise PrinterError(f"Unsupported connection type for EPL2: {type(conn)}")

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

        self._connected = False

    async def is_online(self) -> bool:
        """Check if the EPL2 printer is online.

        Sends a healthcheck command and checks for response.
        Default command is UQ (print information query).
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
            command = self.config.healthcheck.command or "UQ"
            await self._send(command.encode() + b"\n")
            response = await self._recv(timeout=2.0)
            online = len(response) > 0
            self._update_cache(online)

            if not online:
                logger.warning(f"Printer {self.name}: no response to healthcheck")

            # Parse model info from UQ response (first line: "UKQ1935HLU      V4.42")
            if online and response and self._model_info is None:
                try:
                    first_line = response.decode("utf-8", errors="ignore").split("\n")[0]
                    # Format: "UKQ1935HLU      V4.42" -> "UKQ1935HLU V4.42"
                    parts = first_line.split()
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
        """Query media size from EPL2 printer.

        EPL2 printers don't typically report media size automatically.
        """
        # EPL2 doesn't have a standard media query command
        return None

    async def print_raw(self, data: bytes) -> None:
        """Send raw EPL2 data to the printer."""
        if not self._connected:
            raise ConnectionError("Printer not connected")

        await self._send(data)

    async def _send(self, data: bytes) -> None:
        """Send data to the printer."""
        if self._writer:
            self._writer.write(data)
            await self._writer.drain()
        elif self._serial:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._serial.write, data)
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
