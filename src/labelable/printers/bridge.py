"""Bridge P-Touch printer that holds jobs for a remote daemon to poll.

The daemon runs on the machine with the USB printer attached and periodically
polls the Labelable server for pending print jobs. No inbound ports are needed
on the daemon side - all communication is initiated by the daemon.
"""

import asyncio
import logging
import time

from labelable.models.printer import BridgeConnection, PrinterConfig
from labelable.printers.base import BasePrinter, PrinterError

logger = logging.getLogger(__name__)

# How long print_raw() waits for the daemon to complete a job
JOB_TIMEOUT = 60.0

# If no status report within this many seconds, consider daemon offline
DAEMON_STALE_TIMEOUT = 90.0


class BridgePTouchPrinter(BasePrinter):
    """P-Touch printer accessed via a polling bridge daemon.

    When print_raw() is called (by the queue worker), it stores the data
    and blocks until the daemon picks it up and reports the result.

    The daemon polls via the bridge API endpoints:
      GET  /api/v1/bridge/{name}/job     - fetch pending job data
      POST /api/v1/bridge/{name}/result  - report job completion
      POST /api/v1/bridge/{name}/status  - report printer status
    """

    def __init__(self, config: PrinterConfig) -> None:
        super().__init__(config)
        conn = config.connection
        if not isinstance(conn, BridgeConnection):
            raise PrinterError(f"BridgePTouchPrinter requires BridgeConnection, got {type(conn).__name__}")
        # Pending job data for the daemon to pick up
        self._pending_data: bytes | None = None
        # Signalled when the daemon reports job result
        self._result_event = asyncio.Event()
        self._result_ok: bool = False
        self._result_error: str | None = None
        # Daemon-reported status
        self._daemon_online: bool = False
        self._last_status_time: float = 0.0
        self._media_kind: str | None = None
        self._tape_colour: str | None = None
        self._text_colour: str | None = None
        self._low_battery: bool | None = None
        self._errors: list[str] = []

    async def connect(self) -> None:
        # No real connection - the daemon handles USB
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_online(self) -> bool:
        # Daemon is considered online if it reported status recently
        if self._last_status_time > 0 and (time.monotonic() - self._last_status_time) > DAEMON_STALE_TIMEOUT:
            self._daemon_online = False
        online = self._daemon_online
        self._update_cache(online)
        return online

    async def get_media_size(self) -> tuple[float, float] | None:
        conn = self.config.connection
        if isinstance(conn, BridgeConnection) and conn.tape_width_mm:
            return (float(conn.tape_width_mm), 0.0)
        return None

    async def print_raw(self, data: bytes) -> None:
        """Store data for daemon pickup and wait for result."""
        self._pending_data = data
        self._result_event.clear()
        self._result_ok = False
        self._result_error = None

        try:
            await asyncio.wait_for(self._result_event.wait(), timeout=JOB_TIMEOUT)
        except TimeoutError:
            self._pending_data = None
            raise PrinterError("Bridge daemon did not complete job within timeout") from None

        if not self._result_ok:
            raise PrinterError(f"Bridge print failed: {self._result_error}")

    def take_pending_job(self) -> bytes | None:
        """Take the pending job data (called by the API when daemon polls).

        Returns the raw bytes if a job is pending, None otherwise.
        Clears the pending data so the same job isn't returned twice.
        """
        data = self._pending_data
        self._pending_data = None
        return data

    def report_result(self, ok: bool, error: str | None = None) -> None:
        """Report job completion (called by the API when daemon reports result)."""
        self._result_ok = ok
        self._result_error = error
        self._result_event.set()

    @property
    def media_kind(self) -> str | None:
        return self._media_kind

    @property
    def tape_colour(self) -> str | None:
        return self._tape_colour

    @property
    def text_colour(self) -> str | None:
        return self._text_colour

    @property
    def low_battery(self) -> bool | None:
        return self._low_battery

    @property
    def errors(self) -> list[str]:
        return self._errors

    def report_status(
        self,
        online: bool,
        model_info: str | None = None,
        tape_width_mm: int | None = None,
        media_kind: str | None = None,
        tape_colour: str | None = None,
        text_colour: str | None = None,
        low_battery: bool | None = None,
        errors: list[str] | None = None,
    ) -> None:
        """Update daemon-reported status (called by the API on status reports)."""
        self._daemon_online = online
        self._last_status_time = time.monotonic()
        self._update_cache(online)
        if model_info:
            self._model_info = model_info
        # Update tape width if reported
        if tape_width_mm is not None:
            conn = self.config.connection
            if isinstance(conn, BridgeConnection):
                conn.tape_width_mm = tape_width_mm
        self._media_kind = media_kind
        self._tape_colour = tape_colour
        self._text_colour = text_colour
        self._low_battery = low_battery
        self._errors = errors or []
