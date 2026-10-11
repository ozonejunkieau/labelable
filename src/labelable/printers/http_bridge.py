"""Shared HTTP plumbing for the ESP32-P4 USB bridge devices.

Both bridge firmwares (ptouch-bridge and zebra-bridge) expose the same shape
of API on the LAN:

    GET  /status   unauthenticated, returns session state and job counters
    POST /print    bearer token, 202 on admission

and the same job semantics: one job at a time, no queue on the device, and
202 means admitted rather than printed. Completion is observed by polling
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
from abc import abstractmethod
from typing import Any, ClassVar

import aiohttp

from labelable.models.printer import PrinterConfig, PTouchBridgeConnection, ZebraBridgeConnection
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

BridgeConnection = PTouchBridgeConnection | ZebraBridgeConnection


class HTTPBridgePrinter(BasePrinter):
    """Base for printers reached over HTTP via an ESP32-P4 bridge."""

    # Environment fallback for the POST /print bearer token
    TOKEN_ENV_VAR: ClassVar[str]
    # Human name used in log and error messages, e.g. "P-Touch bridge"
    DEVICE_LABEL: ClassVar[str]

    def __init__(self, config: PrinterConfig, conn: BridgeConnection) -> None:
        super().__init__(config)
        self._bridge_conn = conn
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

    @property
    def completion_timeout(self) -> float:
        """Seconds to wait for an admitted job to finish."""
        return 30.0

    def _is_retryable(self, status_code: int, reason: str) -> bool:
        """Whether a non-202 POST /print response is worth retrying."""
        return status_code in (409, 503)

    def _rejection(self, status_code: int, reason: str) -> PrinterError:
        """Error for a non-retryable POST /print response."""
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

    @property
    def _token(self) -> str | None:
        return self._bridge_conn.token or os.environ.get(self.TOKEN_ENV_VAR)

    @property
    def errors(self) -> list[str]:
        """Active hardware errors reported by the device."""
        return self._errors

    def _require_token(self) -> str:
        token = self._token
        if not token:
            raise PrinterError(
                f"No bearer token configured for printer '{self.name}' (set connection.token or {self.TOKEN_ENV_VAR})"
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
            async with session.get(f"{self.base_url}/status") as resp:
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

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def is_online(self) -> bool:
        """Refresh status and report whether the device would accept a job."""
        status = await self._fetch_status()
        if status is None:
            self._connected = False
            self._update_cache(False)
            return False

        self._connected = True
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

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            async with self._session.post(
                f"{self.base_url}/print",
                headers=headers,
                **request_kwargs,
            ) as resp:
                status_code = resp.status
                reason = await self._error_reason(resp)

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

            if status.get("state") == "printing":
                continue

            if self._errors:
                raise PrinterError(f"{self.DEVICE_LABEL} reported errors: {', '.join(self._errors)}")

            if printed > before_printed:
                return

        raise PrinterError(f"{self.DEVICE_LABEL} did not complete the job within {timeout:.0f}s")
