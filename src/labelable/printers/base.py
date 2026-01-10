"""Abstract base class for printer implementations."""

import time
from abc import ABC, abstractmethod

from labelable.models.printer import PrinterConfig

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
        self._model_info: str | None = None  # Cached model/version info

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
