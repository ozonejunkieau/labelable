"""Shared HTTP plumbing for the ESP32-P4 labelable-usb bridge.

One board drives several USB printers and exposes, per printer (selected with
``?printer=<id>``):

    GET  /status   unauthenticated, returns session state and job counters
    POST /print    bearer token, 202 on admission
    GET  /config   bearer token, stored settings (ZPL and EPL2 only)

and the same job semantics: one job at a time per printer, no queue on the
device, and 202 means admitted rather than printed. The older single-printer
firmwares (ptouch-bridge, zebra-bridge) speak the same API without the
``printer`` parameter, which they ignore. Completion is observed by polling
/status until the session leaves ``printing`` and ``jobs_printed`` or
``jobs_failed`` moves.

``state`` is ``"error"`` after a failed job and stays that way until the
next job starts, so it describes the *last job*, not the printer. Readiness
must not require ``"idle"``, or one failure wedges the queue: the printer
reads as offline, jobs are re-queued instead of submitted, and nothing ever
starts the job that would clear the state.

Subclasses supply the payload, the readiness rule and how status fields map
onto the printer's descriptive properties.
"""

import asyncio
import logging
import os
import time
from abc import abstractmethod
from typing import Any, ClassVar
from urllib.parse import quote

import aiohttp

from labelable.models.printer import PrinterConfig, UsbBridgeConnection
from labelable.printers.base import BasePrinter, PrinterError

logger = logging.getLogger(__name__)

# HTTP request timeout for /status and /print
REQUEST_TIMEOUT = 10.0

# Retry policy for retryable statuses. The devices have no queue, so a busy
# device just needs waiting out.
RETRY_ATTEMPTS = 5
RETRY_DELAY = 2.0

# Completion polling after a 202 admission
COMPLETION_POLL_INTERVAL = 1.0

# POST /print gets no total timeout: a 1 MB ZPL job over a slow link can take
# longer than REQUEST_TIMEOUT just to upload. Connecting and waiting for the
# reply after the upload are still bounded.
POST_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=REQUEST_TIMEOUT, sock_read=REQUEST_TIMEOUT)

# Environment fallback for the bearer token
TOKEN_ENV_VAR = "LABELABLE_USB_BRIDGE_TOKEN"

# 4xx/5xx reasons from POST /print that get specific handling
REASON_NOT_CONNECTED = "printer not connected"
REASON_NOT_READY = "printer not ready"
REASON_UNKNOWN_PRINTER = "unknown printer"
REASON_PRINTER_REQUIRED = "printer parameter required"

# GET /config holds the printer's USB link for up to ~4 s, and the settings
# it reports rarely change, so it is read far less often than /status.
CONFIG_REFRESH_INTERVAL = 600.0


class HTTPBridgePrinter(BasePrinter):
    """Base for printers reached over HTTP via an ESP32-P4 bridge."""

    # Human name used in log and error messages, e.g. "P-Touch bridge"
    DEVICE_LABEL: ClassVar[str]
    # The printer as named in "not ready" errors, e.g. "Zebra printer"
    PRINTER_LABEL: ClassVar[str]
    # What to suggest when the device refuses a job without listing a fault
    NOT_READY_HINT: ClassVar[str] = "no detail"
    # The firmware's printer kind: "ptouch", "zpl" or "epl"
    KIND: ClassVar[str]
    # Seconds to wait for an admitted job when the connection sets none
    DEFAULT_COMPLETION_TIMEOUT: ClassVar[float] = 30.0
    # Whether the device has GET /config for this kind of printer
    HAS_CONFIG: ClassVar[bool] = False

    def __init__(self, config: PrinterConfig, conn: UsbBridgeConnection) -> None:
        super().__init__(config)
        self._bridge_conn = conn
        self._config_read_at: float | None = None
        # Older firmware has no GET /config; stop asking once it says so
        self._config_unsupported = False
        self._session: aiohttp.ClientSession | None = None
        self._last_status: dict[str, Any] | None = None
        self._errors: list[str] = []

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update cached descriptive fields (errors, model info) from a status payload."""

    @abstractmethod
    def _is_ready(self, status: dict[str, Any]) -> bool:
        """Whether the device would accept a job right now."""

    def _apply_config(self, body: dict[str, Any]) -> bool:
        """Store a GET /config body; False if it held nothing usable."""
        return False

    @property
    def completion_timeout(self) -> float:
        """Seconds to wait for an admitted job to finish."""
        configured = self._bridge_conn.completion_timeout_s
        return configured if configured is not None else self.DEFAULT_COMPLETION_TIMEOUT

    def _is_retryable(self, status_code: int, reason: str) -> bool:
        """Whether a non-202 POST /print response is worth retrying.

        A busy printer or one that is briefly unplugged is worth waiting out.
        ``printer not ready`` is a fault that needs a person at the printer.
        """
        if status_code == 409:
            return True
        return status_code == 503 and reason != REASON_NOT_READY

    def _rejection(self, status_code: int, reason: str) -> PrinterError:
        """Error for a non-retryable POST /print response."""
        printer_id = self._bridge_conn.printer
        if status_code == 404 and reason == REASON_UNKNOWN_PRINTER:
            return PrinterError(
                f"{self.DEVICE_LABEL} does not know printer '{printer_id}'; "
                f"set connection.printer to an id listed by GET {self.base_url}/status"
            )
        if status_code == 400 and reason == REASON_PRINTER_REQUIRED:
            return PrinterError(
                f"{self.DEVICE_LABEL} has more than one printer that could take this job; "
                f"set `printer:` in the connection config to one of the ids listed by GET {self.base_url}/status"
            )
        if status_code == 503 and reason == REASON_NOT_READY:
            detail = ", ".join(self._errors) if self._errors else self.NOT_READY_HINT
            return PrinterError(f"{self.PRINTER_LABEL} not ready: {detail}")
        return PrinterError(f"{self.DEVICE_LABEL} rejected job (HTTP {status_code}): {reason}")

    def _failed_job_detail(self) -> str:
        """Why the last job failed, from the most recent status."""
        return ", ".join(self._errors) if self._errors else "no detail"

    # ------------------------------------------------------------------
    # Transport plumbing
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self._bridge_conn.host}:{self._bridge_conn.port}"

    def _url(self, path: str, printer_id: str | None = None) -> str:
        """Endpoint URL, selecting the configured printer (or `printer_id`)."""
        url = f"{self.base_url}{path}"
        printer_id = printer_id or self._bridge_conn.printer
        return f"{url}?printer={quote(printer_id, safe='')}" if printer_id else url

    @property
    def _token(self) -> str | None:
        """Config token, then LABELABLE_USB_BRIDGE_TOKEN, then the old alias's variable."""
        conn = self._bridge_conn
        token = conn.token or os.environ.get(TOKEN_ENV_VAR)
        if not token and conn.legacy_token_env:
            token = os.environ.get(conn.legacy_token_env)
        return token

    @property
    def errors(self) -> list[str]:
        """Active hardware errors reported by the device."""
        return self._errors

    def _require_token(self) -> str:
        token = self._token
        if not token:
            raise PrinterError(
                f"No bearer token configured for printer '{self.name}' (set connection.token or {TOKEN_ENV_VAR})"
            )
        return token

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )
        return self._session

    async def connect(self) -> None:
        """Create the HTTP session and confirm the device answers."""
        self._ensure_session()

        status = await self._fetch_status()
        if status is None:
            await self.disconnect()
            raise ConnectionError(f"{self.DEVICE_LABEL} at {self.base_url} did not respond to GET /status")

        self._connected = True
        logger.info(f"Printer {self.name}: connected to {self.DEVICE_LABEL} at {self.base_url}")

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._connected = False

    async def _fetch_status(self) -> dict[str, Any] | None:
        """GET /status, returning the parsed body or None on failure."""
        session = self._ensure_session()

        try:
            async with session.get(self._url("/status")) as resp:
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

        if isinstance(body.get("printers"), list):
            # No `printer` set, so the board answered with its summary
            body = self._pick_printer(body["printers"])
            if body is None:
                return None

        self._last_status = body
        self._apply_status(body)
        return body

    def _pick_printer(self, printers: list[Any]) -> dict[str, Any] | None:
        """Choose this printer from a board summary when `printer` is unset.

        Works only while exactly one printer of this kind (preferring
        connected ones) is known - the same rule the device applies to
        POST /print without a printer.
        """
        ours = [p for p in printers if isinstance(p, dict) and p.get("kind") == self.KIND]
        connected = [p for p in ours if p.get("usb_connected")]
        candidates = connected or ours
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            logger.warning(f"Printer {self.name}: {self.DEVICE_LABEL} has no '{self.KIND}' printer")
        else:
            ids = ", ".join(str(p.get("id")) for p in candidates)
            logger.warning(
                f"Printer {self.name}: several '{self.KIND}' printers ({ids}); set `printer:` in the connection"
            )
        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def is_online(self) -> bool:
        """Refresh status and report whether the device would accept a job.

        While this transport's own job is running the device reports
        ``printing``, which would cache the printer as offline and make the
        UI report a busy printer as offline. It is online; it is just busy.
        """
        if self._printing:
            return True

        status = await self._fetch_status()
        if status is None:
            self._connected = False
            self._update_cache(False)
            return False

        self._connected = True
        kind = status.get("kind")
        if isinstance(kind, str) and kind != self.KIND:
            logger.warning(
                f"Printer {self.name}: bridge printer is '{kind}', but this printer is configured as '{self.KIND}'"
            )
            online = False
        else:
            online = self._is_ready(status)
        self._update_cache(online)

        if self._errors:
            logger.warning(f"Printer {self.name}: errors - {', '.join(self._errors)}")

        return online

    def _job_counters(self) -> tuple[int, int]:
        """Snapshot (jobs_printed, jobs_failed) from the last status."""
        status = self._last_status or {}
        printed = status.get("jobs_printed")
        failed = status.get("jobs_failed")
        return (
            printed if isinstance(printed, int) else 0,
            failed if isinstance(failed, int) else 0,
        )

    async def refresh_details(self) -> None:
        """Re-read the printer's stored settings via GET /config.

        At most every CONFIG_REFRESH_INTERVAL, and only when the printer is
        connected and idle; a busy or slow reply is simply retried on the next
        healthcheck.
        """
        token = self._token
        if not self.HAS_CONFIG or self._config_unsupported or not token:
            return
        if self._config_read_at is not None and time.monotonic() - self._config_read_at < CONFIG_REFRESH_INTERVAL:
            return
        status = self._last_status
        if not status or not status.get("usb_connected") or status.get("state") == "printing":
            return
        kind = status.get("kind")
        if isinstance(kind, str) and kind != self.KIND:
            # is_online() already reports the mismatch; another kind's
            # settings would only mislabel this printer's details
            return

        # /config insists on a printer, so fall back to the id that /status
        # identified when none is configured
        status_id = status.get("id")
        url = self._url("/config", status_id if isinstance(status_id, str) else None)
        session = self._ensure_session()
        try:
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status in (400, 404):
                    # Older firmware, or a printer kind without settings
                    self._config_unsupported = True
                    logger.info(f"Printer {self.name}: bridge has no GET /config for this printer, settings not shown")
                    return
                if resp.status != 200:
                    logger.debug(f"Printer {self.name}: GET /config returned HTTP {resp.status}")
                    return
                body = await resp.json()
        except Exception as e:
            logger.debug(f"Printer {self.name}: GET /config failed - {e}")
            return

        if not isinstance(body, dict) or not self._apply_config(body):
            logger.warning(f"Printer {self.name}: GET /config returned no usable configuration")
            return
        self._config_read_at = time.monotonic()

    # ------------------------------------------------------------------
    # Printing
    # ------------------------------------------------------------------

    async def _submit(self, token: str, **request_kwargs: Any) -> None:
        """POST /print, retrying only what `_is_retryable` allows.

        ``request_kwargs`` go straight to ``session.post`` (``json=`` or
        ``data=``); an ``Authorization`` header is merged into ``headers``.
        """
        if self._session is None or self._session.closed:
            raise ConnectionError("Printer not connected")

        headers = {**request_kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        last_error = "unknown error"
        before = self._job_counters()

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                async with self._session.post(
                    self._url("/print"),
                    headers=headers,
                    timeout=POST_TIMEOUT,
                    **request_kwargs,
                ) as resp:
                    status_code = resp.status
                    reason = await self._error_reason(resp)
            except (aiohttp.ClientError, TimeoutError) as e:
                # The device may have admitted the job before the connection
                # dropped. Resending it then would print it twice, so ask the
                # device first and only retry if it is not working on it.
                if await self._job_was_admitted(before):
                    logger.warning(f"Printer {self.name}: POST /print failed ({e!r}) but the job was admitted")
                    return
                last_error = f"connection error: {e!r}"
                if attempt < RETRY_ATTEMPTS:
                    logger.info(f"Printer {self.name}: POST /print failed ({e!r}), retrying in {RETRY_DELAY}s")
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                raise PrinterError(f"{self.DEVICE_LABEL} unreachable after {RETRY_ATTEMPTS} attempts: {e!r}") from e

            if status_code == 202:
                return

            if self._is_retryable(status_code, reason):
                last_error = reason
                if attempt < RETRY_ATTEMPTS:
                    logger.info(
                        f"Printer {self.name}: bridge returned {status_code} ({reason}), "
                        f"retrying in {RETRY_DELAY}s (attempt {attempt}/{RETRY_ATTEMPTS})"
                    )
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                raise PrinterError(f"{self.DEVICE_LABEL} unavailable after {RETRY_ATTEMPTS} attempts: {reason}")

            raise self._rejection(status_code, reason)

        raise PrinterError(f"{self.DEVICE_LABEL} unavailable: {last_error}")

    async def _job_was_admitted(self, before: tuple[int, int]) -> bool:
        """After a failed POST, whether the device took the job anyway."""
        status = await self._fetch_status()
        if status is None:
            return False
        return status.get("state") == "printing" or self._job_counters() != before

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

        202 is admission only. print_with_quantity may loop print_raw, so
        returning early would earn a 409 on the second copy.
        """
        timeout = self.completion_timeout
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(COMPLETION_POLL_INTERVAL)
            status = await self._fetch_status()
            if status is None:
                continue

            printed, failed = self._job_counters()

            if failed > before_failed:
                raise PrinterError(f"{self.DEVICE_LABEL} reported a failed job: {self._failed_job_detail()}")

            # Success is checked before errors: a fault that appears after the
            # job finished (paper out on the next label) is not this job's
            # failure, and reporting it as one invites a duplicate reprint.
            if printed > before_printed:
                if self._errors:
                    logger.warning(
                        f"Printer {self.name}: job printed, but the printer now reports {', '.join(self._errors)}"
                    )
                return

            if status.get("state") == "printing":
                continue

            if self._errors:
                raise PrinterError(f"{self.DEVICE_LABEL} reported errors: {', '.join(self._errors)}")

        raise PrinterError(f"{self.DEVICE_LABEL} did not complete the job within {timeout:.0f}s")
