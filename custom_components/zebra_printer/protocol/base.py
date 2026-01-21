"""Base protocol for Zebra printers."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..const import CONNECT_TIMEOUT, DEFAULT_PORT, READ_TIMEOUT


@dataclass
class PrinterStatus:
    """Printer status data."""

    # Connection
    online: bool = False

    # Identity
    model: str | None = None
    firmware: str | None = None

    # Binary states
    head_open: bool | None = None
    paper_out: bool | None = None
    ribbon_out: bool | None = None
    paused: bool | None = None
    buffer_full: bool | None = None

    # Sensors
    labels_printed: int | None = None  # Not available on most models
    head_distance_cm: float | None = None
    print_speed: int | None = None
    darkness: int | None = None
    label_length_mm: float | None = None
    print_width_mm: float | None = None
    print_mode: str | None = None
    print_method: str | None = None  # "direct_thermal" or "thermal_transfer"

    # Error/Warning status from ~HQES
    has_error: bool = False  # True if any error is present
    error_flags: str = "None"  # Comma-separated error descriptions or "None"
    warning_flags: str = "None"  # Comma-separated warning descriptions or "None"

    # Capabilities (from ~HI response)
    thermal_transfer_capable: bool = False  # True if printer supports thermal transfer

    # Raw data for debugging
    raw_status: dict[str, Any] = field(default_factory=dict)


class PrinterProtocol(ABC):
    """Abstract base class for printer protocols."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        """Initialize protocol."""
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> bool:
        """Connect to printer."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECT_TIMEOUT,
            )
            return True
        except (TimeoutError, OSError):
            return False

    async def disconnect(self) -> None:
        """Disconnect from printer."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None

    async def send_command(self, command: str) -> str:
        """Send command and read response."""
        if not self._writer or not self._reader:
            return ""

        try:
            # Send command
            self._writer.write(f"{command}\r\n".encode())
            await self._writer.drain()

            # Read response with timeout
            response = await asyncio.wait_for(
                self._reader.read(4096),
                timeout=READ_TIMEOUT,
            )
            return response.decode("latin-1", errors="replace")
        except (TimeoutError, OSError):
            return ""

    async def send_raw(self, data: bytes) -> bool:
        """Send raw data to printer."""
        if not self._writer:
            return False

        try:
            self._writer.write(data)
            await self._writer.drain()
            return True
        except OSError:
            return False

    @abstractmethod
    async def get_status(self) -> PrinterStatus:
        """Get printer status."""

    @abstractmethod
    async def probe(self) -> bool:
        """Probe if this protocol is supported."""

    @abstractmethod
    def get_calibrate_command(self) -> str:
        """Get calibration command."""

    @abstractmethod
    def get_feed_command(self, count: int = 1) -> str:
        """Get feed command."""
