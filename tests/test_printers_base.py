"""Tests for base printer functionality."""

import time
from datetime import datetime

import pytest

from labelable.models.printer import HealthcheckConfig, PrinterConfig, TCPConnection
from labelable.printers.base import STATUS_CACHE_TTL, BasePrinter


class MockBasePrinter(BasePrinter):
    """Concrete implementation of BasePrinter for testing."""

    def __init__(self, config: PrinterConfig, online: bool = True):
        super().__init__(config)
        self._online = online
        self._print_calls: list[bytes] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_online(self) -> bool:
        self._update_cache(self._online)
        return self._online

    async def get_media_size(self) -> tuple[float, float] | None:
        return (50.0, 25.0)

    async def print_raw(self, data: bytes) -> None:
        self._print_calls.append(data)


@pytest.fixture
def printer_config():
    """Create a test printer configuration."""
    return PrinterConfig(
        name="test-printer",
        type="zpl",
        connection=TCPConnection(host="127.0.0.1", port=9100),
        healthcheck=HealthcheckConfig(interval=60, command="~HS"),
    )


@pytest.fixture
def printer(printer_config):
    """Create a test printer instance."""
    return MockBasePrinter(printer_config)


class TestBasePrinter:
    """Tests for BasePrinter class."""

    def test_init(self, printer, printer_config):
        """Test printer initialization."""
        assert printer.name == "test-printer"
        assert printer.config == printer_config
        assert printer._connected is False
        assert printer._cached_online is None

    def test_is_connected(self, printer):
        """Test is_connected property."""
        assert printer.is_connected is False

    @pytest.mark.asyncio
    async def test_connect(self, printer):
        """Test connect method."""
        await printer.connect()
        assert printer.is_connected is True

    @pytest.mark.asyncio
    async def test_disconnect(self, printer):
        """Test disconnect method."""
        await printer.connect()
        await printer.disconnect()
        assert printer.is_connected is False

    @pytest.mark.asyncio
    async def test_is_online_updates_cache(self, printer):
        """Test that is_online updates the cache."""
        assert printer._cached_online is None

        result = await printer.is_online()

        assert result is True
        assert printer._cached_online is True
        assert printer._last_checked is not None

    def test_get_cached_online_status_no_cache(self, printer):
        """Test get_cached_online_status with no cache."""
        result = printer.get_cached_online_status()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_online_status_valid(self, printer):
        """Test get_cached_online_status with valid cache."""
        await printer.is_online()

        result = printer.get_cached_online_status()
        assert result is True

    def test_get_cached_online_status_expired(self, printer):
        """Test get_cached_online_status with expired cache."""
        # Manually set cache with old timestamp
        printer._cached_online = True
        printer._cache_time = time.monotonic() - STATUS_CACHE_TTL - 1

        result = printer.get_cached_online_status()
        assert result is None

    def test_invalidate_cache(self, printer):
        """Test cache invalidation."""
        printer._cached_online = True
        printer._cache_time = time.monotonic()

        printer.invalidate_cache()

        assert printer._cached_online is None
        assert printer._cache_time == 0.0

    @pytest.mark.asyncio
    async def test_print_raw(self, printer):
        """Test print_raw method."""
        await printer.connect()
        await printer.print_raw(b"test data")

        assert printer._print_calls == [b"test data"]

    @pytest.mark.asyncio
    async def test_print_with_quantity_single(self, printer):
        """Test print_with_quantity with quantity=1."""
        await printer.connect()
        await printer.print_with_quantity(b"test data", 1)

        assert len(printer._print_calls) == 1

    @pytest.mark.asyncio
    async def test_print_with_quantity_multiple(self, printer):
        """Test print_with_quantity with multiple copies."""
        await printer.connect()
        await printer.print_with_quantity(b"test data", 3)

        # Default implementation loops
        assert len(printer._print_calls) == 3

    @pytest.mark.asyncio
    async def test_get_media_size(self, printer):
        """Test get_media_size method."""
        result = await printer.get_media_size()
        assert result == (50.0, 25.0)

    def test_last_checked_initially_none(self, printer):
        """Test last_checked is initially None."""
        assert printer._last_checked is None

    @pytest.mark.asyncio
    async def test_last_checked_updated_on_status_check(self, printer):
        """Test last_checked is updated when status is checked."""
        before = datetime.now()
        await printer.is_online()
        after = datetime.now()

        assert printer._last_checked is not None
        assert before <= printer._last_checked <= after


class TestPrinterOffline:
    """Tests for offline printer behavior."""

    @pytest.fixture
    def offline_printer(self, printer_config):
        """Create an offline printer."""
        return MockBasePrinter(printer_config, online=False)

    @pytest.mark.asyncio
    async def test_is_online_returns_false(self, offline_printer):
        """Test is_online returns False for offline printer."""
        result = await offline_printer.is_online()
        assert result is False

    @pytest.mark.asyncio
    async def test_cached_status_is_false(self, offline_printer):
        """Test cached status is False after check."""
        await offline_printer.is_online()
        assert offline_printer.get_cached_online_status() is False
