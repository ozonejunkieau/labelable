"""Abstract base class for printer implementations."""

import base64
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import aiohttp

from labelable.models.printer import PrinterConfig

logger = logging.getLogger(__name__)

# Cache duration for online status (seconds)
STATUS_CACHE_TTL = 30.0


def read_serial_reply(port: Any, size: int, timeout: float, quiet_gap: float = 0.1) -> bytes:
    """Read a reply from a serial port: up to ``size`` bytes, stopping once it goes quiet.

    ``port.read(size)`` waits for all ``size`` bytes or the port's timeout, and
    a status reply is far shorter than ``size``, so every healthcheck took the
    full 5 s port timeout (and ignored the caller's timeout). This returns
    ``quiet_gap`` after the last byte arrives, or at ``timeout`` if nothing does.
    Blocking: run it in an executor.
    """
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    last_byte_at: float | None = None
    while len(buffer) < size:
        waiting = port.in_waiting
        if waiting:
            buffer += port.read(min(waiting, size - len(buffer)))
            last_byte_at = time.monotonic()
            continue
        now = time.monotonic()
        if last_byte_at is not None and now - last_byte_at >= quiet_gap:
            break
        if now >= deadline:
            break
        time.sleep(0.01)
    return bytes(buffer)


class BasePrinter(ABC):
    """Abstract base class for all printer implementations."""

    def __init__(self, config: PrinterConfig) -> None:
        self.config = config
        self.name = config.name
        self._connected = False
        self._printing = False  # Set during print jobs to suppress healthchecks
        self._cached_online: bool | None = None
        self._cache_time: float = 0.0
        self._last_checked: datetime | None = None  # Absolute time of last status check
        self._model_info: str | None = None  # Cached model/version info
        # HA connection state (set by subclass _connect_ha if applicable)
        self._ha_session: aiohttp.ClientSession | None = None
        self._ha_device_id: str | None = None
        self._ha_url: str | None = None

    @property
    def is_connected(self) -> bool:
        """Check if the printer is currently connected."""
        return self._connected

    def get_cached_online_status(self) -> bool | None:
        """Get cached online status without blocking.

        Returns:
            True/False if we have a recent cached status (within TTL),
            None if no cached status available.
        """
        if self._cached_online is None:
            return None
        if time.monotonic() - self._cache_time > self.status_cache_ttl:
            return None
        return self._cached_online

    @property
    def status_cache_ttl(self) -> float:
        """How long a status check stays valid.

        The worker refreshes the cache every healthcheck interval (60 s by
        default). A fixed 30 s TTL made status read "unknown" for half of
        every interval, so the TTL stretches to cover one missed check.
        """
        return max(STATUS_CACHE_TTL, self.config.healthcheck.interval * 2)

    async def online_status(self) -> bool:
        """Online status for request handlers: the cache when fresh, else a check.

        Request handlers used to call is_online() every time. On a TCP printer
        that is a healthcheck on the socket the worker also uses, so two at
        once collided, and an offline printer blocked every page for seconds.
        """
        cached = self.get_cached_online_status()
        if cached is not None:
            return cached
        return await self.is_online()

    def _update_cache(self, online: bool) -> None:
        """Update the cached online status."""
        self._cached_online = online
        self._cache_time = time.monotonic()
        self._last_checked = datetime.now()

    def invalidate_cache(self) -> None:
        """Invalidate the cached online status, forcing a fresh check."""
        self._cached_online = None
        self._cache_time = 0.0

    @property
    def last_checked(self) -> datetime | None:
        """Get the absolute time of the last status check."""
        return self._last_checked

    @property
    def model_info(self) -> str | None:
        """Get cached model/version info."""
        return self._model_info

    @property
    def dpi(self) -> int | None:
        """Resolution the printer itself reports, or None if unknown.

        Only transports that can ask the printer override this (the
        zebra_bridge device reports it from ~HI).
        """
        return None

    @property
    def output_format(self) -> str:
        """Template engine output format this transport expects.

        Defaults to the printer type ("zpl", "epl2", "ptouch"). Transports
        that build the printer command stream themselves override this
        (e.g. "ptouch_raw" for the ptouch_bridge network device).
        """
        return self.config.type.value

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the printer.

        Raises:
            ConnectionError: If connection fails.
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the printer."""
        pass

    @abstractmethod
    async def is_online(self) -> bool:
        """Check if the printer is online and ready.

        Returns:
            True if printer is online and ready to print.
        """
        pass

    @abstractmethod
    async def get_media_size(self) -> tuple[float, float] | None:
        """Query the printer for current media/label dimensions.

        Returns:
            Tuple of (width_mm, height_mm) if available, None if not supported.
        """
        pass

    @abstractmethod
    async def print_raw(self, data: bytes) -> None:
        """Send raw data to the printer.

        Args:
            data: Raw printer command data (ZPL, EPL, bitmap, etc.)

        Raises:
            ConnectionError: If not connected or connection lost.
            PrinterError: If printing fails.
        """
        pass

    async def print_with_quantity(self, data: bytes, quantity: int) -> None:
        """Send data to printer with quantity handling.

        Default implementation loops `quantity` times calling print_raw.
        Subclasses can override to detect native quantity commands in their
        protocol (e.g., ^PQ for ZPL) and skip looping when present.

        Args:
            data: Raw printer command data.
            quantity: Number of copies to print.
        """
        for _ in range(quantity):
            await self.print_raw(data)

    async def _close_after_error(self) -> None:
        """Disconnect after a failed exchange, never raising."""
        try:
            await self.disconnect()
        except Exception as e:
            logger.debug(f"Printer {self.name}: error while closing - {e}")
        self._connected = False

    async def _send_via_ha(self, data: bytes) -> None:
        """Send raw bytes to the printer via the HA zebra_printer integration.

        Service data is JSON, so the job travels as base64 (``data_b64``) and
        reaches the printer byte for byte. Sending it as text corrupted every
        byte above 0x7F, which breaks EPL2 GW bitmaps and non-ASCII text.

        An integration older than ``data_b64`` rejects the call with 400. A
        pure-ASCII job is safe to resend as text, so that still works; a binary
        job is refused rather than printed corrupted.
        """
        if not self._ha_session:
            raise ConnectionError("HA session not initialized")

        url = f"{self._ha_url}/api/services/zebra_printer/print_raw"
        payload = {"device_id": self._ha_device_id, "data_b64": base64.b64encode(data).decode("ascii")}
        async with self._ha_session.post(url, json=payload) as resp:
            if resp.status == 200:
                return
            status, text = resp.status, await resp.text()

        if status == 400 and data.isascii():
            logger.info(f"Printer {self.name}: HA integration lacks data_b64, resending as text")
            async with self._ha_session.post(
                url, json={"device_id": self._ha_device_id, "data": data.decode("ascii")}
            ) as resp:
                if resp.status == 200:
                    return
                status, text = resp.status, await resp.text()
        elif status == 400:
            raise ConnectionError(
                f"HA service call failed: {status} - {text}. This job contains binary data, which needs "
                "the zebra_printer integration updated to a version supporting data_b64"
            )

        raise ConnectionError(f"HA service call failed: {status} - {text}")

    async def _is_online_ha(self) -> bool:
        """Check printer status via HA API.

        Used by subclasses when connected via Home Assistant integration.
        Queries the ready binary sensor, falls back to language sensor existence.
        """
        if not self._ha_session or not self._ha_device_id:
            self._update_cache(False)
            return False

        try:
            # Query the ready sensor for this device
            entity_id = f"binary_sensor.{self._ha_device_id}_ready"
            async with self._ha_session.get(f"{self._ha_url}/api/states/{entity_id}") as resp:
                if resp.status != 200:
                    # Sensor not found, try just checking if the device exists via language sensor
                    entity_id = f"sensor.{self._ha_device_id}_language"
                    async with self._ha_session.get(f"{self._ha_url}/api/states/{entity_id}") as resp2:
                        if resp2.status == 200:
                            # Device exists, assume online
                            self._update_cache(True)
                            return True
                    logger.warning(f"Printer {self.name}: HA entity not found")
                    self._update_cache(False)
                    return False

                state = await resp.json()
                online = state.get("state") == "on"
                self._update_cache(online)

                # Get model info from HA if not already set
                if online and self._model_info is None:
                    try:
                        model_entity = f"sensor.{self._ha_device_id}_model"
                        async with self._ha_session.get(f"{self._ha_url}/api/states/{model_entity}") as model_resp:
                            if model_resp.status == 200:
                                model_state = await model_resp.json()
                                self._model_info = model_state.get("state")
                    except Exception:
                        pass

                return online
        except Exception as e:
            logger.warning(f"Printer {self.name}: HA status check failed - {e}")
            self._update_cache(False)
            return False

    async def __aenter__(self) -> "BasePrinter":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()


class PrinterError(Exception):
    """Exception raised for printer-related errors."""

    pass
