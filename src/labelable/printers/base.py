"""Abstract base class for printer implementations."""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime

import aiohttp

from labelable.models.printer import PrinterConfig

logger = logging.getLogger(__name__)

# Cache duration for online status (seconds)
STATUS_CACHE_TTL = 30.0


class BasePrinter(ABC):
    """Abstract base class for all printer implementations."""

    def __init__(self, config: PrinterConfig) -> None:
        self.config = config
        self.name = config.name
        self._connected = False
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
        if time.monotonic() - self._cache_time > STATUS_CACHE_TTL:
            return None
        return self._cached_online

    def _update_cache(self, online: bool) -> None:
        """Update the cached online status."""
        self._cached_online = online
        self._cache_time = time.monotonic()
        self._last_checked = datetime.now()

    @property
    def last_checked(self) -> datetime | None:
        """Get the absolute time of the last status check."""
        return self._last_checked

    @property
    def model_info(self) -> str | None:
        """Get cached model/version info."""
        return self._model_info

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
            async with self._ha_session.get(
                f"{self._ha_url}/api/states/{entity_id}"
            ) as resp:
                if resp.status != 200:
                    # Sensor not found, try just checking if the device exists via language sensor
                    entity_id = f"sensor.{self._ha_device_id}_language"
                    async with self._ha_session.get(
                        f"{self._ha_url}/api/states/{entity_id}"
                    ) as resp2:
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
                        async with self._ha_session.get(
                            f"{self._ha_url}/api/states/{model_entity}"
                        ) as model_resp:
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
