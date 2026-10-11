"""Brother P-Touch printer behind an ESP32-P4 network bridge.

The bridge (ptouch-bridge firmware) hosts a PT-P710BT over USB and exposes a
small HTTP API on the LAN:

    GET  /status   unauthenticated, returns session/media state
    POST /print    bearer token, accepts pre-rasterised rows as base64

It does **not** speak PTCBP. It wants uncompressed 16-byte raster rows and
builds the printer command stream itself, which is why this transport renders
templates as ``ptouch_raw`` rather than ``ptouch``.

The device holds one job at a time and has no queue - queueing is our job.
"""

import base64
import logging
from typing import Any

from labelable.models.printer import PrinterConfig, PTouchBridgeConnection
from labelable.printers.base import PrinterError
from labelable.printers.http_bridge import RETRY_ATTEMPTS, HTTPBridgePrinter
from labelable.templates.converters.ptouch import BYTES_PER_LINE

logger = logging.getLogger(__name__)

__all__ = ["RETRY_ATTEMPTS", "TOKEN_ENV_VAR", "PTouchNetPrinter"]

# Environment fallback for the POST /print bearer token
TOKEN_ENV_VAR = "LABELABLE_PTOUCH_BRIDGE_TOKEN"

# Media type values from the bridge spec
MEDIA_TYPE_NAMES: dict[int, str] = {
    0x00: "None",
    0x01: "Laminated",
    0x03: "Non-laminated",
    0x11: "Heat-shrink 2:1",
    0x17: "Heat-shrink 3:1",
    0xFF: "Incompatible",
}

# tape_color_id is a raw byte; only these two are documented
TAPE_COLOUR_NAMES: dict[int, str] = {
    0x01: "White",
    0x08: "Black",
}


class PTouchNetPrinter(HTTPBridgePrinter):
    """P-Touch printer reached over HTTP via the ESP32-P4 ptouch-bridge."""

    TOKEN_ENV_VAR = TOKEN_ENV_VAR
    DEVICE_LABEL = "P-Touch bridge"

    def __init__(self, config: PrinterConfig) -> None:
        conn = config.connection
        if not isinstance(conn, PTouchBridgeConnection):
            raise PrinterError(f"PTouchNetPrinter requires PTouchBridgeConnection, got {type(conn).__name__}")
        super().__init__(config, conn)
        self._conn = conn
        self._tape_colour: str | None = None
        self._media_kind: str | None = None
        self._declared_width: int | None = None

    @property
    def output_format(self) -> str:
        """This device builds the command stream, so it wants bare rows."""
        return "ptouch_raw"

    @property
    def tape_colour(self) -> str | None:
        """Loaded tape colour, if the device has read the printer."""
        return self._tape_colour

    @property
    def media_kind(self) -> str | None:
        """Loaded media type name, if the device has read the printer."""
        return self._media_kind

    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update cached descriptive fields from a status payload.

        The device omits ``media_type``/``tape_color_id`` entirely (not null)
        when it has never successfully polled the printer, and reports
        ``media_width_mm`` as null in the same situation.
        """
        errors = status.get("errors")
        self._errors = [str(e) for e in errors] if isinstance(errors, list) else []

        media_type = status.get("media_type")
        if isinstance(media_type, int):
            self._media_kind = MEDIA_TYPE_NAMES.get(media_type, f"Unknown ({media_type})")

        tape_colour = status.get("tape_color_id")
        if isinstance(tape_colour, int):
            self._tape_colour = TAPE_COLOUR_NAMES.get(tape_colour, f"Unknown ({tape_colour})")

        width = status.get("media_width_mm")
        if isinstance(width, int | float):
            kind = self._media_kind or "Unknown"
            self._model_info = f"P-Touch bridge ({int(width)}mm {kind})"
        elif self._model_info is None:
            self._model_info = "P-Touch bridge (no media read)"

    def _is_ready(self, status: dict[str, Any]) -> bool:
        """Ready iff USB is up, no job is running, and there are no errors.

        ``state == "error"`` only describes the previous job, so it counts as
        ready (see the http_bridge module docstring).
        """
        return bool(status.get("usb_connected")) and status.get("state") != "printing" and not self._errors

    def _rejection(self, status_code: int, reason: str) -> PrinterError:
        if status_code == 422:
            return PrinterError(
                f"Media width mismatch: bridge rejected {self._declared_width}mm against the loaded tape ({reason})"
            )
        return super()._rejection(status_code, reason)

    def _status_width(self) -> int | None:
        """Media width from the last status, or None if never read."""
        if not self._last_status:
            return None
        width = self._last_status.get("media_width_mm")
        if isinstance(width, int | float):
            return int(width)
        return None

    def _status_media_type(self) -> int:
        """Media type from the last status, falling back to config."""
        if self._last_status:
            media_type = self._last_status.get("media_type")
            if isinstance(media_type, int):
                return media_type
        return self._conn.media_type

    async def get_media_size(self) -> tuple[float, float] | None:
        """Return (width_mm, 0) - P-Touch tape is continuous."""
        width = self._status_width()
        if width is not None and width > 0:
            return (float(width), 0.0)
        return None

    async def check_media_width(self, expected_width_mm: int) -> None:
        """Verify the loaded tape width matches the expected width.

        Args:
            expected_width_mm: Expected tape width in mm.

        Raises:
            PrinterError: If media width doesn't match or status query fails.
        """
        status = await self._fetch_status()
        if status is None:
            raise PrinterError("Failed to read printer status")

        if self._errors:
            raise PrinterError(f"Printer has errors: {', '.join(self._errors)}")

        width = self._status_width()
        if width is None:
            raise PrinterError("Failed to read printer status (no media width reported)")

        if width != expected_width_mm:
            raise PrinterError(
                f"Media width mismatch: printer has {width}mm tape loaded, but template requires {expected_width_mm}mm"
            )

    async def print_raw(self, data: bytes) -> None:
        """Submit raster rows to the bridge and wait for the job to finish.

        Args:
            data: Concatenated uncompressed 16-byte raster rows.

        Raises:
            PrinterError: On a rejected, failed or timed-out job.
        """
        if len(data) == 0:
            raise PrinterError("Refusing to print an empty raster job")
        if len(data) % BYTES_PER_LINE != 0:
            raise PrinterError(
                f"Raster payload is not 16-byte aligned: {len(data)} bytes ({len(data) % BYTES_PER_LINE} left over)"
            )

        token = self._require_token()
        row_count = len(data) // BYTES_PER_LINE

        # Refresh status so the declared width/type match what is actually
        # loaded - a stale value would earn a self-inflicted 422.
        status = await self._fetch_status()
        before_printed, before_failed = self._job_counters()

        width = self._status_width()
        if width is None:
            raise PrinterError("Bridge has not read the loaded tape; cannot declare media_width_mm")

        payload = {
            "media_width_mm": width,
            "media_type": self._status_media_type(),
            "raster_row_count": row_count,
            "raster_rows_b64": base64.b64encode(data).decode("ascii"),
            "force": self._conn.force,
        }

        if status is None:
            logger.warning(f"Printer {self.name}: status unavailable before print, using last known media")

        self._declared_width = width
        await self._submit(token, json=payload)
        await self._wait_for_completion(before_printed, before_failed)
