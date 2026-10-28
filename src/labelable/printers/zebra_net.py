"""Zebra ZPL printer behind an ESP32-P4 network bridge.

The bridge (labelable-usb firmware, or the older zebra-bridge) hosts a
GK/GX-series printer over USB and exposes a small HTTP API on the LAN:

    GET  /status   unauthenticated, returns printer identity, faults, counters
    POST /print    bearer token, raw ZPL body (Content-Type: application/zpl)
    GET  /config   bearer token, the printer's ^HH configuration label text

ZPL goes to the printer verbatim. The device decides completion itself by
polling ``~HS`` until the printer's buffer drains, and cancels a failed job
with ``~JA``, so a fault never leaves half a job waiting to resume.

The device holds one job at a time and has no queue - queueing is our job.
"""

import logging
import re
from typing import Any

from labelable.models.printer import PrinterConfig, UsbBridgeConnection
from labelable.printers.base import PrinterError
from labelable.printers.http_bridge import HTTPBridgePrinter

logger = logging.getLogger(__name__)

# Wait for a job to finish. Must exceed the firmware's own
# CONFIG_LU_ZPL_JOB_TIMEOUT_S (300 s by default), or a long ^PQ batch is
# reported failed here while it is still printing.
DEFAULT_COMPLETION_TIMEOUT_S = 330.0

# The firmware's default CONFIG_LU_JOB_BUFFER_MAX_BYTES. Copies are joined
# into one job only while they fit, so a build with a smaller limit still
# works - it just loops instead.
JOIN_LIMIT_BYTES = 1024 * 1024

# Configuration label lines shown as printer details, in display order:
# (label on the ^HH output, display name, value kind)
CONFIG_DETAILS: list[tuple[str, str, str]] = [
    ("PRINT WIDTH", "Print width", "dots"),
    ("LABEL LENGTH", "Label length", "dots"),
    ("PRINT MODE", "Print mode", "word"),
    ("MEDIA TYPE", "Media type", "word"),
    ("SENSOR TYPE", "Sensor", "word"),
    ("PRINT METHOD", "Print method", "word"),
    ("DARKNESS", "Darkness", "number"),
    ("PRINT SPEED", "Print speed", "text"),
    ("TEAR OFF", "Tear-off / cut offset", "offset"),
    ("LEFT POSITION", "Left position", "offset"),
    ("LABEL TOP", "Label top", "offset"),
    ("BACKFEED", "Backfeed", "word"),
    ("CUTTER TYPE", "Cutter", "word"),
    ("HEAD USAGE", "Head usage", "length"),
    ("LAST CLEANED", "Last cleaned", "length"),
    ("SERIAL NUMBER", "Serial number", "text"),
]


def parse_config_label(raw: str) -> dict[str, str]:
    """Split ^HH output into {label: value}.

    Each line is ``value<2+ spaces>LABEL``; a value can itself contain a
    double space ("39.0IN   989MM"), so the label is the last column and
    everything before it is the value. Lines without two columns are skipped.
    """
    result: dict[str, str] = {}
    for line in raw.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 2:
            result[parts[-1]] = " ".join(parts[:-1])
    return result


class ZebraNetPrinter(HTTPBridgePrinter):
    """Zebra ZPL printer reached over HTTP via the ESP32-P4 labelable-usb bridge."""

    DEVICE_LABEL = "Zebra bridge"
    PRINTER_LABEL = "Zebra printer"
    NOT_READY_HINT = "not answering ~HS - is it in ZPL mode?"
    KIND = "zpl"
    DEFAULT_COMPLETION_TIMEOUT = DEFAULT_COMPLETION_TIMEOUT_S
    HAS_CONFIG = True

    def __init__(self, config: PrinterConfig) -> None:
        conn = config.connection
        if not isinstance(conn, UsbBridgeConnection):
            raise PrinterError(f"ZebraNetPrinter requires UsbBridgeConnection, got {type(conn).__name__}")
        super().__init__(config, conn)
        self._conn = conn
        self._dpi: int | None = None
        self._config_lines: dict[str, str] = {}

    @property
    def dpi(self) -> int | None:
        """Printer resolution reported by ~HI, or None until it has answered."""
        return self._dpi

    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update cached descriptive fields from a status payload.

        ``printer`` (from ~HI) and ``media`` (from ~HS) are null until the
        printer has answered the query.
        """
        errors = status.get("errors")
        self._errors = [str(e) for e in errors] if isinstance(errors, list) else []

        printer = status.get("printer")
        if isinstance(printer, dict):
            model = printer.get("model") or "Zebra"
            firmware = printer.get("firmware")
            dpi = printer.get("dpi")
            self._dpi = dpi if isinstance(dpi, int) else None
            parts = [str(model)]
            if firmware:
                parts.append(str(firmware))
            if self._dpi:
                parts.append(f"({self._dpi} dpi)")
            self._model_info = " ".join(parts)
        elif self._model_info is None:
            self._model_info = "Zebra bridge (printer not identified)"

    def _is_ready(self, status: dict[str, Any]) -> bool:
        """Ready iff USB is up, no job is running, ~HS answers, and no faults.

        ``media == null`` while connected means the printer is not answering
        ~HS - usually because it is in EPL mode. The device refuses jobs
        then, since it cannot tell when one finishes.

        ``state == "error"`` only describes the previous job, so it counts as
        ready (see the http_bridge module docstring).
        """
        if not status.get("usb_connected"):
            return False
        if status.get("media") is None:
            logger.warning(f"Printer {self.name}: not answering ~HS - is it in ZPL mode?")
            return False
        return status.get("state") != "printing" and not self._errors

    def _failed_job_detail(self) -> str:
        """Prefer the device's own failure reason (e.g. "fault: paper_out")."""
        reason = (self._last_status or {}).get("last_failure")
        if isinstance(reason, str) and reason:
            return reason
        return super()._failed_job_detail()

    @property
    def details(self) -> dict[str, str]:
        """Firmware, stored settings and job counters for display.

        Settings come from the last GET /config; until it has been read, the
        few that /status carries are shown instead.
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

        if self._config_lines:
            for label, name, kind in CONFIG_DETAILS:
                value = self._config_lines.get(label)
                if value:
                    result[name] = self._format_config_value(value, kind)
        else:
            if media.get("print_mode"):
                result["Print mode"] = str(media["print_mode"]).replace("_", " ").capitalize()
            if isinstance(media.get("label_length_dots"), int):
                result["Label length"] = self._dots_text(media["label_length_dots"])

        printed, failed = self._job_counters()
        if self._last_status is not None:
            result["Jobs since bridge start"] = f"{printed} printed, {failed} failed"
        if status.get("last_failure"):
            result["Last failure"] = str(status["last_failure"])
        return result

    def _dots_text(self, dots: int) -> str:
        if self._dpi:
            return f"{dots} dots ({dots * 25.4 / self._dpi:.1f} mm)"
        return f"{dots} dots"

    def _format_config_value(self, value: str, kind: str) -> str:
        if kind in ("dots", "offset"):
            try:
                dots = int(value)
            except ValueError:
                return value
            return self._dots_text(dots) if kind == "dots" else f"{dots:+d} dots"
        if kind == "word":
            return value.capitalize()
        if kind == "number":
            try:
                return f"{float(value):g}"
            except ValueError:
                return value
        if kind == "length":
            # "18,249 IN" -> "18,249 in"
            return value.replace(" IN", " in")
        return value

    def _apply_config(self, body: dict[str, Any]) -> bool:
        raw = body.get("raw")
        if not isinstance(raw, str):
            return False
        self._config_lines = parse_config_label(raw)
        return True

    async def get_media_size(self) -> tuple[float, float] | None:
        """Not available: ~HS reports label length but not width."""
        return None

    async def print_raw(self, data: bytes) -> None:
        """Submit a ZPL job to the bridge and wait for the printer to drain it.

        Args:
            data: ZPL, sent byte for byte.

        Raises:
            PrinterError: On a rejected, failed or timed-out job.
        """
        if len(data) == 0:
            raise PrinterError("Refusing to print an empty ZPL job")

        token = self._require_token()

        # Snapshot the counters immediately before submitting, so completion
        # is judged against this job and not an earlier one.
        if await self._fetch_status() is None:
            logger.warning(f"Printer {self.name}: status unavailable before print")
        before_printed, before_failed = self._job_counters()

        await self._submit(token, data=data, headers={"Content-Type": "application/zpl"})
        try:
            await self._wait_for_completion(before_printed, before_failed)
        finally:
            # A job can change stored settings (^PW, ^MM...), so re-read them
            # at the next healthcheck rather than show stale values
            self._config_read_at = None

    async def print_with_quantity(self, data: bytes, quantity: int) -> None:
        """Send ZPL with quantity handling.

        ^PQ in the data means the printer handles quantity natively. Otherwise
        the copies are joined into a single job - the device accepts several
        formats per job, and one job means one completion wait rather than
        one per copy. Too large to join, it falls back to one job per copy.
        """
        if quantity < 1:
            return  # Same as the other transports: zero copies prints nothing
        if b"^PQ" in data or quantity == 1:
            await self.print_raw(data)
        elif len(data) * quantity <= JOIN_LIMIT_BYTES:
            await self.print_raw(data * quantity)
        else:
            for _ in range(quantity):
                await self.print_raw(data)
