"""Zebra ZPL printer behind an ESP32-P4 network bridge.

The bridge (zebra-bridge firmware) hosts a GK/GX-series printer over USB and
exposes a small HTTP API on the LAN:

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
import time
from typing import Any

from labelable.models.printer import PrinterConfig, ZebraBridgeConnection
from labelable.printers.base import PrinterError
from labelable.printers.http_bridge import HTTPBridgePrinter

logger = logging.getLogger(__name__)

# Environment fallback for the POST /print bearer token
TOKEN_ENV_VAR = "LABELABLE_ZEBRA_BRIDGE_TOKEN"

# The firmware's default CONFIG_ZB_JOB_BUFFER_MAX_BYTES. Copies are joined
# into one job only while they fit, so a build with a smaller limit still
# works - it just loops instead.
JOIN_LIMIT_BYTES = 1024 * 1024

# 503 reasons from POST /print. Only a missing printer is worth waiting out;
# a fault needs a person at the printer.
REASON_NOT_CONNECTED = "printer not connected"
REASON_NOT_READY = "printer not ready"

# GET /config holds the printer's USB link for up to ~4 s, and the settings
# it reports rarely change, so it is read far less often than /status.
CONFIG_REFRESH_INTERVAL = 600.0

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
    """Zebra ZPL printer reached over HTTP via the ESP32-P4 zebra-bridge."""

    TOKEN_ENV_VAR = TOKEN_ENV_VAR
    DEVICE_LABEL = "Zebra bridge"

    def __init__(self, config: PrinterConfig) -> None:
        conn = config.connection
        if not isinstance(conn, ZebraBridgeConnection):
            raise PrinterError(f"ZebraNetPrinter requires ZebraBridgeConnection, got {type(conn).__name__}")
        super().__init__(config, conn)
        self._conn = conn
        self._dpi: int | None = None
        self._config_lines: dict[str, str] = {}
        self._config_read_at: float | None = None
        # Older firmware has no GET /config; stop asking once it says so
        self._config_unsupported = False

    @property
    def dpi(self) -> int | None:
        """Printer resolution reported by ~HI, or None until it has answered."""
        return self._dpi

    @property
    def completion_timeout(self) -> float:
        return self._conn.completion_timeout_s

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

    def _is_retryable(self, status_code: int, reason: str) -> bool:
        if status_code == 409:
            return True
        return status_code == 503 and reason != REASON_NOT_READY

    def _rejection(self, status_code: int, reason: str) -> PrinterError:
        if status_code == 503 and reason == REASON_NOT_READY:
            if self._errors:
                detail = ", ".join(self._errors)
            else:
                detail = "not answering ~HS - is it in ZPL mode?"
            return PrinterError(f"Zebra printer not ready: {detail}")
        return super()._rejection(status_code, reason)

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

    async def refresh_details(self) -> None:
        """Re-read the printer's stored settings via GET /config.

        At most every CONFIG_REFRESH_INTERVAL, and only when the printer is
        connected and idle; a busy or slow reply is simply retried on the next
        healthcheck.
        """
        token = self._token
        if self._config_unsupported or not token:
            return
        if self._config_read_at is not None and time.monotonic() - self._config_read_at < CONFIG_REFRESH_INTERVAL:
            return
        status = self._last_status
        if not status or not status.get("usb_connected") or status.get("state") == "printing":
            return

        session = self._ensure_session()
        try:
            async with session.get(f"{self.base_url}/config", headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status == 404:
                    self._config_unsupported = True
                    logger.info(f"Printer {self.name}: bridge firmware has no GET /config, settings not shown")
                    return
                if resp.status != 200:
                    logger.debug(f"Printer {self.name}: GET /config returned HTTP {resp.status}")
                    return
                body = await resp.json()
        except Exception as e:
            logger.debug(f"Printer {self.name}: GET /config failed - {e}")
            return

        raw = body.get("raw") if isinstance(body, dict) else None
        if not isinstance(raw, str):
            logger.warning(f"Printer {self.name}: GET /config returned no configuration text")
            return
        self._config_lines = parse_config_label(raw)
        self._config_read_at = time.monotonic()

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
