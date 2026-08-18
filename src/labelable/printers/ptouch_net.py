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

import asyncio
import base64
import logging
import os
from typing import Any

import aiohttp

from labelable.models.printer import PrinterConfig, PTouchBridgeConnection
from labelable.printers.base import BasePrinter, PrinterError
from labelable.templates.converters.ptouch import BYTES_PER_LINE

logger = logging.getLogger(__name__)

# Environment fallback for the POST /print bearer token
TOKEN_ENV_VAR = "LABELABLE_PTOUCH_BRIDGE_TOKEN"

# HTTP request timeout for /status and /print
REQUEST_TIMEOUT = 10.0

# Retry policy for the two retryable statuses (409 busy, 503 not connected).
# The device has no queue, so a busy device just needs waiting out.
RETRY_ATTEMPTS = 5
RETRY_DELAY = 2.0

# Completion polling after a 202 admission
COMPLETION_TIMEOUT = 30.0
COMPLETION_POLL_INTERVAL = 1.0

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


class PTouchNetPrinter(BasePrinter):
    """P-Touch printer reached over HTTP via the ESP32-P4 ptouch-bridge."""

    def __init__(self, config: PrinterConfig) -> None:
        super().__init__(config)
        conn = config.connection
        if not isinstance(conn, PTouchBridgeConnection):
            raise PrinterError(f"PTouchNetPrinter requires PTouchBridgeConnection, got {type(conn).__name__}")
        self._conn = conn
        self._session: aiohttp.ClientSession | None = None
        self._last_status: dict[str, Any] | None = None
        self._errors: list[str] = []
        self._tape_colour: str | None = None
        self._media_kind: str | None = None

    # ------------------------------------------------------------------
    # Transport plumbing
    # ------------------------------------------------------------------

    @property
    def output_format(self) -> str:
        """This device builds the command stream, so it wants bare rows."""
        return "ptouch_raw"

    @property
    def base_url(self) -> str:
        return f"http://{self._conn.host}:{self._conn.port}"

    @property
    def _token(self) -> str | None:
        return self._conn.token or os.environ.get(TOKEN_ENV_VAR)

    @property
    def errors(self) -> list[str]:
        """Active hardware errors reported by the device."""
        return self._errors

    @property
    def tape_colour(self) -> str | None:
        """Loaded tape colour, if the device has read the printer."""
        return self._tape_colour

    @property
    def media_kind(self) -> str | None:
        """Loaded media type name, if the device has read the printer."""
        return self._media_kind

    async def connect(self) -> None:
        """Create the HTTP session and confirm the device answers."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )

        status = await self._fetch_status()
        if status is None:
            await self.disconnect()
            raise ConnectionError(f"P-Touch bridge at {self.base_url} did not respond to GET /status")

        self._connected = True
        logger.info(f"Printer {self.name}: connected to P-Touch bridge at {self.base_url}")

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._connected = False

    async def _fetch_status(self) -> dict[str, Any] | None:
        """GET /status, returning the parsed body or None on failure."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )

        try:
            async with self._session.get(f"{self.base_url}/status") as resp:
                if resp.status != 200:
                    logger.warning(f"Printer {self.name}: GET /status returned HTTP {resp.status}")
                    return None
                body = await resp.json()
        except Exception as e:
            logger.warning(f"Printer {self.name}: GET /status failed - {e}")
            return None

        if not isinstance(body, dict):
            logger.warning(f"Printer {self.name}: GET /status returned unexpected payload type")
            return None

        self._last_status = body
        self._apply_status(body)
        return body

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

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def is_online(self) -> bool:
        """Ready iff USB is up, the session is idle, and there are no errors."""
        status = await self._fetch_status()
        if status is None:
            self._connected = False
            self._update_cache(False)
            return False

        self._connected = True
        online = bool(status.get("usb_connected")) and status.get("state") == "idle" and not self._errors
        self._update_cache(online)

        if self._errors:
            logger.warning(f"Printer {self.name}: errors - {', '.join(self._errors)}")

        return online

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

    # ------------------------------------------------------------------
    # Printing
    # ------------------------------------------------------------------

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

        token = self._token
        if not token:
            raise PrinterError(
                f"No bearer token configured for printer '{self.name}' (set connection.token or {TOKEN_ENV_VAR})"
            )

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

        await self._submit(payload, token)
        await self._wait_for_completion(before_printed, before_failed)

    def _job_counters(self) -> tuple[int, int]:
        """Snapshot (jobs_printed, jobs_failed) from the last status."""
        status = self._last_status or {}
        printed = status.get("jobs_printed")
        failed = status.get("jobs_failed")
        return (
            printed if isinstance(printed, int) else 0,
            failed if isinstance(failed, int) else 0,
        )

    async def _submit(self, payload: dict[str, Any], token: str) -> None:
        """POST /print, retrying only the retryable statuses."""
        if self._session is None or self._session.closed:
            raise ConnectionError("Printer not connected")

        headers = {"Authorization": f"Bearer {token}"}
        last_error = "unknown error"

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            async with self._session.post(
                f"{self.base_url}/print",
                json=payload,
                headers=headers,
            ) as resp:
                status_code = resp.status
                reason = await self._error_reason(resp)

            if status_code == 202:
                return

            if status_code in (409, 503):
                last_error = reason
                if attempt < RETRY_ATTEMPTS:
                    logger.info(
                        f"Printer {self.name}: bridge returned {status_code} ({reason}), "
                        f"retrying in {RETRY_DELAY}s (attempt {attempt}/{RETRY_ATTEMPTS})"
                    )
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                raise PrinterError(f"P-Touch bridge unavailable after {RETRY_ATTEMPTS} attempts: {reason}")

            if status_code == 422:
                raise PrinterError(
                    f"Media width mismatch: bridge rejected {payload['media_width_mm']}mm "
                    f"against the loaded tape ({reason})"
                )

            raise PrinterError(f"P-Touch bridge rejected job (HTTP {status_code}): {reason}")

        raise PrinterError(f"P-Touch bridge unavailable: {last_error}")

    @staticmethod
    async def _error_reason(resp: Any) -> str:
        """Extract the {"error": ...} reason from a response, best effort."""
        try:
            body = await resp.json()
            if isinstance(body, dict):
                reason = body.get("error") or body.get("result")
                if reason:
                    return str(reason)
        except Exception:
            pass
        try:
            text = await resp.text()
        except Exception:
            return "no detail"
        return text.strip() or "no detail"

    async def _wait_for_completion(self, before_printed: int, before_failed: int) -> None:
        """Poll /status until the job leaves `printing` and a counter moves.

        202 is admission only. print_with_quantity loops print_raw, so
        returning early would earn a 409 on the second copy.
        """
        deadline = asyncio.get_event_loop().time() + COMPLETION_TIMEOUT

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(COMPLETION_POLL_INTERVAL)
            status = await self._fetch_status()
            if status is None:
                continue

            printed, failed = self._job_counters()

            if failed > before_failed:
                detail = ", ".join(self._errors) if self._errors else "no detail"
                raise PrinterError(f"P-Touch bridge reported a failed job: {detail}")

            if status.get("state") == "printing":
                continue

            if self._errors:
                raise PrinterError(f"P-Touch bridge reported errors: {', '.join(self._errors)}")

            if printed > before_printed:
                return

        raise PrinterError(f"P-Touch bridge did not complete the job within {COMPLETION_TIMEOUT:.0f}s")
