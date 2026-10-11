"""Zebra ZPL printer behind an ESP32-P4 network bridge.

The bridge (zebra-bridge firmware) hosts a GK/GX-series printer over USB and
exposes a small HTTP API on the LAN:

    GET  /status   unauthenticated, returns printer identity, faults, counters
    POST /print    bearer token, raw ZPL body (Content-Type: application/zpl)

ZPL goes to the printer verbatim. The device decides completion itself by
polling ``~HS`` until the printer's buffer drains, and cancels a failed job
with ``~JA``, so a fault never leaves half a job waiting to resume.

The device holds one job at a time and has no queue - queueing is our job.
"""

import logging
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
        await self._wait_for_completion(before_printed, before_failed)

    async def print_with_quantity(self, data: bytes, quantity: int) -> None:
        """Send ZPL with quantity handling.

        ^PQ in the data means the printer handles quantity natively. Otherwise
        the copies are joined into a single job - the device accepts several
        formats per job, and one job means one completion wait rather than
        one per copy. Too large to join, it falls back to one job per copy.
        """
        if b"^PQ" in data or quantity <= 1:
            await self.print_raw(data)
        elif len(data) * quantity <= JOIN_LIMIT_BYTES:
            await self.print_raw(data * quantity)
        else:
            for _ in range(quantity):
                await self.print_raw(data)
