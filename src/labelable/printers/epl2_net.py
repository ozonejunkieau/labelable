"""Zebra EPL2 printer behind an ESP32-P4 network bridge.

The bridge (labelable-usb firmware) hosts an LP2844-class printer over USB and
exposes a small HTTP API on the LAN:

    GET  /status   unauthenticated, returns printer identity, faults, counters
    POST /print    bearer token, raw EPL2 body (Content-Type: application/epl)
    GET  /config   bearer token, the printer's ``UQ`` settings

EPL2 goes to the printer verbatim. EPL2 has no ``~HS`` equivalent, so the
device decides completion by sending ``UQ`` after the job and waiting for the
reply: commands run in order, so an answer means the job was consumed. A
printer stopped on a fault (paper out, head open) stops answering, which
shows up as ``not_responding`` in ``errors``.

The device holds one job at a time and has no queue - queueing is our job.
"""

import logging
from typing import Any

from labelable.models.printer import PrinterConfig, UsbBridgeConnection
from labelable.printers.base import PrinterError
from labelable.printers.http_bridge import HTTPBridgePrinter
from labelable.printers.zebra_net import JOIN_LIMIT_BYTES
from labelable.templates.converters.epl2 import epl2_native_quantity

logger = logging.getLogger(__name__)

# Wait for a job to finish. Must exceed the firmware's own
# CONFIG_LU_EPL_JOB_TIMEOUT_S (120 s by default).
DEFAULT_COMPLETION_TIMEOUT_S = 150.0

# Settings from GET /config ``parsed`` (or, before it is read, /status
# ``media``), in display order: (key, display name, value kind)
CONFIG_DETAILS: list[tuple[str, str, str]] = [
    ("label_width_dots", "Label width", "dots"),
    ("label_length_dots", "Label length", "dots"),
    ("gap_dots", "Gap", "dots"),
    ("darkness", "Darkness", "text"),
    ("speed", "Print speed", "text"),
    ("print_direction", "Print direction", "word"),
    ("direct_thermal", "Print method", "thermal"),
    ("serial", "Serial number", "text"),
]


class EPL2NetPrinter(HTTPBridgePrinter):
    """Zebra EPL2 printer reached over HTTP via the ESP32-P4 labelable-usb bridge."""

    DEVICE_LABEL = "EPL2 bridge"
    PRINTER_LABEL = "EPL2 printer"
    NOT_READY_HINT = "not answering UQ - check paper and head, and that it is in EPL mode"
    KIND = "epl"
    DEFAULT_COMPLETION_TIMEOUT = DEFAULT_COMPLETION_TIMEOUT_S
    HAS_CONFIG = True

    def __init__(self, config: PrinterConfig) -> None:
        conn = config.connection
        if not isinstance(conn, UsbBridgeConnection):
            raise PrinterError(f"EPL2NetPrinter requires UsbBridgeConnection, got {type(conn).__name__}")
        super().__init__(config, conn)
        self._conn = conn
        self._dpi: int | None = None
        self._config_parsed: dict[str, Any] = {}

    @property
    def dpi(self) -> int | None:
        """Printer resolution reported by UQ, or None until it has answered."""
        return self._dpi

    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update cached descriptive fields from a status payload.

        ``printer`` and ``media`` (from UQ) are null until the printer has
        answered.
        """
        errors = status.get("errors")
        self._errors = [str(e) for e in errors] if isinstance(errors, list) else []

        printer = status.get("printer")
        if isinstance(printer, dict):
            firmware = printer.get("firmware")
            dpi = printer.get("dpi")
            self._dpi = dpi if isinstance(dpi, int) else None
            parts = ["Zebra EPL2"]
            if firmware:
                parts.append(str(firmware))
            if self._dpi:
                parts.append(f"({self._dpi} dpi)")
            self._model_info = " ".join(parts)
        elif self._model_info is None:
            self._model_info = "EPL2 bridge (printer not identified)"

    def _is_ready(self, status: dict[str, Any]) -> bool:
        """Ready iff USB is up, no job is running, UQ answers, and no faults.

        ``media == null`` while connected means the printer is not answering
        UQ. ``state == "error"`` only describes the previous job, so it
        counts as ready (see the http_bridge module docstring).
        """
        if not status.get("usb_connected"):
            return False
        if status.get("media") is None:
            logger.warning(f"Printer {self.name}: not answering UQ - is it in EPL mode?")
            return False
        return status.get("state") != "printing" and not self._errors

    def _failed_job_detail(self) -> str:
        """Prefer the device's own failure reason (e.g. "timed out after 120 s")."""
        reason = (self._last_status or {}).get("last_failure")
        if isinstance(reason, str) and reason:
            return reason
        return super()._failed_job_detail()

    def _apply_config(self, body: dict[str, Any]) -> bool:
        parsed = body.get("parsed")
        if not isinstance(parsed, dict):
            return False
        self._config_parsed = parsed
        return True

    @property
    def details(self) -> dict[str, str]:
        """Firmware, stored settings and job counters for display.

        Settings come from the last GET /config; until it has been read, the
        label size that /status carries is shown instead.
        """
        status = self._last_status or {}
        printer_info = status.get("printer")
        printer: dict[str, Any] = printer_info if isinstance(printer_info, dict) else {}
        media_info = status.get("media")
        media: dict[str, Any] = media_info if isinstance(media_info, dict) else {}
        result: dict[str, str] = {}

        if printer.get("firmware"):
            result["Firmware"] = str(printer["firmware"])
        if self._dpi:
            result["Resolution"] = f"{self._dpi} dpi"

        source = self._config_parsed or media
        for key, name, kind in CONFIG_DETAILS:
            value = source.get(key)
            if value is not None and value != "":
                result[name] = self._format_value(value, kind)

        printed, failed = self._job_counters()
        if self._last_status is not None:
            result["Jobs since bridge start"] = f"{printed} printed, {failed} failed"
        if status.get("last_failure"):
            result["Last failure"] = str(status["last_failure"])
        return result

    def _format_value(self, value: Any, kind: str) -> str:
        if kind == "dots" and isinstance(value, int):
            if self._dpi:
                return f"{value} dots ({value * 25.4 / self._dpi:.1f} mm)"
            return f"{value} dots"
        if kind == "thermal":
            return "Direct thermal" if value else "Thermal transfer"
        if kind == "word":
            return str(value).capitalize()
        return str(value)

    async def get_media_size(self) -> tuple[float, float] | None:
        """Not available: UQ reports dots, not a trustworthy physical size."""
        return None

    async def print_raw(self, data: bytes) -> None:
        """Submit an EPL2 job to the bridge and wait for the printer to consume it.

        Args:
            data: EPL2, sent byte for byte.

        Raises:
            PrinterError: On a rejected, failed or timed-out job.
        """
        if len(data) == 0:
            raise PrinterError("Refusing to print an empty EPL2 job")

        token = self._require_token()

        # Snapshot the counters immediately before submitting, so completion
        # is judged against this job and not an earlier one.
        if await self._fetch_status() is None:
            logger.warning(f"Printer {self.name}: status unavailable before print")
        before_printed, before_failed = self._job_counters()

        await self._submit(token, data=data, headers={"Content-Type": "application/epl"})
        try:
            await self._wait_for_completion(before_printed, before_failed)
        finally:
            # A job can change stored settings, so re-read them at the next
            # healthcheck rather than show stale values
            self._config_read_at = None

    async def print_with_quantity(self, data: bytes, quantity: int) -> None:
        """Send EPL2 with quantity handling.

        A ``P<n>`` with n > 1 in the data means the printer handles quantity
        natively. Otherwise, when the job starts with ``N`` (clear the image
        buffer), the copies are joined into a single job: each copy then
        starts from a blank buffer, and one job means one completion wait
        rather than one per copy. A job without a leading ``N`` (possible in a
        hand-written jinja template) would overlay the next copy on the
        previous one, and too large a job does not fit, so both fall back to
        one job per copy.
        """
        if quantity < 1:
            return  # Same as the other transports: zero copies prints nothing
        if quantity == 1 or epl2_native_quantity(data):
            await self.print_raw(data)
        elif data.lstrip().startswith(b"N") and len(data) * quantity <= JOIN_LIMIT_BYTES:
            await self.print_raw(data * quantity)
        else:
            for _ in range(quantity):
                await self.print_raw(data)
