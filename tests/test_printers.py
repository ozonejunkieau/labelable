"""Tests for printer implementations."""

import pytest

from labelable.models.printer import PrinterConfig, PrinterType, TCPConnection
from labelable.printers.base import BasePrinter
from labelable.printers.epl2 import EPL2Printer
from labelable.printers.zpl import ZPLPrinter


class MockPrinter(BasePrinter):
    """Mock printer for testing base class behavior."""

    def __init__(self, config: PrinterConfig):
        super().__init__(config)
        self.print_calls: list[bytes] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_online(self) -> bool:
        return self._connected

    async def get_media_size(self) -> tuple[float, float] | None:
        return None

    async def print_raw(self, data: bytes) -> None:
        self.print_calls.append(data)


class MockZPLPrinter(ZPLPrinter):
    """Mock ZPL printer that tracks print calls without network."""

    def __init__(self, config: PrinterConfig):
        super().__init__(config)
        self.print_calls: list[bytes] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def print_raw(self, data: bytes) -> None:
        self.print_calls.append(data)


class MockEPL2Printer(EPL2Printer):
    """Mock EPL2 printer that tracks print calls without network."""

    def __init__(self, config: PrinterConfig):
        super().__init__(config)
        self.print_calls: list[bytes] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def print_raw(self, data: bytes) -> None:
        self.print_calls.append(data)


@pytest.fixture
def tcp_config() -> PrinterConfig:
    """Create a TCP printer config for testing."""
    return PrinterConfig(
        name="test-printer",
        type=PrinterType.ZPL,
        connection=TCPConnection(host="127.0.0.1", port=9100),
    )


class TestBasePrinterQuantity:
    """Test base printer quantity handling (default loop behavior)."""

    async def test_print_with_quantity_loops(self, tcp_config: PrinterConfig):
        """Base implementation should loop quantity times."""
        printer = MockPrinter(tcp_config)
        await printer.connect()

        data = b"test data"
        await printer.print_with_quantity(data, 3)

        assert len(printer.print_calls) == 3
        assert all(call == data for call in printer.print_calls)

    async def test_print_with_quantity_one(self, tcp_config: PrinterConfig):
        """Quantity of 1 should print once."""
        printer = MockPrinter(tcp_config)
        await printer.connect()

        data = b"test data"
        await printer.print_with_quantity(data, 1)

        assert len(printer.print_calls) == 1


class TestZPLPrinterQuantity:
    """Test ZPL printer quantity handling with ^PQ detection."""

    async def test_with_pq_command_prints_once(self, tcp_config: PrinterConfig):
        """ZPL with ^PQ command should print once (printer handles quantity)."""
        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockZPLPrinter(config)
        await printer.connect()

        # Template with ^PQ3 (print 3 copies)
        data = b"^XA^FDTest^FS^PQ3^XZ"
        await printer.print_with_quantity(data, 3)

        # Should only print once - ^PQ handles quantity
        assert len(printer.print_calls) == 1
        assert printer.print_calls[0] == data

    async def test_without_pq_command_loops(self, tcp_config: PrinterConfig):
        """ZPL without ^PQ command should loop quantity times."""
        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockZPLPrinter(config)
        await printer.connect()

        # Template without ^PQ
        data = b"^XA^FDTest^FS^XZ"
        await printer.print_with_quantity(data, 3)

        # Should loop 3 times
        assert len(printer.print_calls) == 3

    async def test_pq_with_variable_prints_once(self, tcp_config: PrinterConfig):
        """ZPL with ^PQ{{ quantity }} (rendered) should print once."""
        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockZPLPrinter(config)
        await printer.connect()

        # Rendered template with ^PQ2
        data = b"^XA^FDTest^FS^PQ2^XZ"
        await printer.print_with_quantity(data, 2)

        assert len(printer.print_calls) == 1


class TestEPL2PrinterQuantity:
    """Test EPL2 printer quantity handling with P command detection."""

    async def test_with_quantity_in_p_command_prints_once(self):
        """EPL2 with P2 or higher should print once (printer handles quantity)."""
        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockEPL2Printer(config)
        await printer.connect()

        # Template with P3 (print 3 copies)
        data = b"N\nA50,50,0,1,1,1,N,\"Test\"\nP3\n"
        await printer.print_with_quantity(data, 3)

        # Should only print once - P3 handles quantity
        assert len(printer.print_calls) == 1
        assert printer.print_calls[0] == data

    async def test_with_p1_loops(self):
        """EPL2 with P1 should loop quantity times."""
        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockEPL2Printer(config)
        await printer.connect()

        # Template with P1 (single copy per command)
        data = b"N\nA50,50,0,1,1,1,N,\"Test\"\nP1\n"
        await printer.print_with_quantity(data, 3)

        # Should loop 3 times since P1 means single copy
        assert len(printer.print_calls) == 3

    async def test_without_p_command_loops(self):
        """EPL2 without P command should loop quantity times."""
        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockEPL2Printer(config)
        await printer.connect()

        # Template without P command (unusual but possible)
        data = b"N\nA50,50,0,1,1,1,N,\"Test\"\n"
        await printer.print_with_quantity(data, 2)

        # Should loop 2 times
        assert len(printer.print_calls) == 2

    async def test_with_p10_prints_once(self):
        """EPL2 with P10 (double digit) should print once."""
        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=TCPConnection(host="127.0.0.1", port=9100),
        )
        printer = MockEPL2Printer(config)
        await printer.connect()

        # Template with P10 (print 10 copies)
        data = b"N\nA50,50,0,1,1,1,N,\"Test\"\nP10\n"
        await printer.print_with_quantity(data, 10)

        # Should only print once
        assert len(printer.print_calls) == 1
