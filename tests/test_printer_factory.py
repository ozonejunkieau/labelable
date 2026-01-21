"""Tests for printer factory and base printer functionality."""

import time

import pytest

from labelable.models.printer import (
    HAConnection,
    PrinterConfig,
    PrinterType,
    SerialConnection,
    TCPConnection,
)
from labelable.printers import (
    EPL2Printer,
    PTouchPrinter,
    ZPLPrinter,
    create_printer,
)
from labelable.printers.base import STATUS_CACHE_TTL


class TestCreatePrinter:
    """Tests for the create_printer factory function."""

    def test_create_zpl_printer(self):
        """Test creating a ZPL printer."""
        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100", port=9100),
        )
        printer = create_printer(config)
        assert isinstance(printer, ZPLPrinter)
        assert printer.name == "test-zpl"
        assert printer.config == config

    def test_create_epl2_printer(self):
        """Test creating an EPL2 printer."""
        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=TCPConnection(host="192.168.1.101"),
        )
        printer = create_printer(config)
        assert isinstance(printer, EPL2Printer)
        assert printer.name == "test-epl2"

    def test_create_ptouch_printer(self):
        """Test creating a P-Touch printer."""
        config = PrinterConfig(
            name="test-ptouch",
            type=PrinterType.PTOUCH,
            connection=SerialConnection(device="/dev/ttyUSB0"),
        )
        printer = create_printer(config)
        assert isinstance(printer, PTouchPrinter)
        assert printer.name == "test-ptouch"

    def test_create_printer_with_ha_connection(self):
        """Test creating a printer with HA connection."""
        config = PrinterConfig(
            name="ha-printer",
            type=PrinterType.ZPL,
            connection=HAConnection(device_id="my_device"),
        )
        printer = create_printer(config)
        assert isinstance(printer, ZPLPrinter)
        assert isinstance(printer.config.connection, HAConnection)

    def test_create_printer_unknown_type(self):
        """Test that unknown printer type raises ValueError."""
        # Create a config with an invalid type by bypassing validation
        config = PrinterConfig(
            name="test",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100"),
        )
        # Manually set invalid type
        config.type = "invalid_type"  # type: ignore

        with pytest.raises(ValueError, match="Unknown printer type"):
            create_printer(config)


class TestBasePrinterCache:
    """Tests for BasePrinter caching functionality."""

    def test_initial_cache_state(self):
        """Test initial cache state is empty."""
        config = PrinterConfig(
            name="test",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100"),
        )
        printer = ZPLPrinter(config)

        assert printer.get_cached_online_status() is None
        assert printer.last_checked is None
        assert printer.model_info is None
        assert not printer.is_connected

    def test_cache_update(self):
        """Test updating the cache."""
        config = PrinterConfig(
            name="test",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100"),
        )
        printer = ZPLPrinter(config)

        printer._update_cache(True)

        assert printer.get_cached_online_status() is True
        assert printer.last_checked is not None

    def test_cache_expiry(self):
        """Test that cache expires after TTL."""
        config = PrinterConfig(
            name="test",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100"),
        )
        printer = ZPLPrinter(config)

        printer._update_cache(True)
        assert printer.get_cached_online_status() is True

        # Simulate time passing beyond TTL by backdating the cache time
        printer._cache_time = time.monotonic() - STATUS_CACHE_TTL - 1
        assert printer.get_cached_online_status() is None

    def test_cache_offline_status(self):
        """Test caching offline status."""
        config = PrinterConfig(
            name="test",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100"),
        )
        printer = ZPLPrinter(config)

        printer._update_cache(False)
        assert printer.get_cached_online_status() is False

    def test_model_info_property(self):
        """Test model info property."""
        config = PrinterConfig(
            name="test",
            type=PrinterType.ZPL,
            connection=TCPConnection(host="192.168.1.100"),
        )
        printer = ZPLPrinter(config)

        assert printer.model_info is None

        printer._model_info = "ZD420 V84.20.21Z"
        assert printer.model_info == "ZD420 V84.20.21Z"


class TestPrinterQuantityHandling:
    """Tests for printer quantity handling."""

    def test_zpl_quantity_with_pq_command(self):
        """Test ZPL detects ^PQ command in data."""
        # Data with ^PQ command should be detected by ZPL printer
        data_with_pq = b"^XA^FDTest^FS^PQ3^XZ"
        data_without_pq = b"^XA^FDTest^FS^XZ"

        assert b"^PQ" in data_with_pq
        assert b"^PQ" not in data_without_pq

    def test_epl2_quantity_detection(self):
        """Test EPL2 quantity detection in P command."""
        import re

        # EPL2 uses P command with quantity
        data_p1 = b"N\nA50,50,0,1,1,1,N,\"Test\"\nP1\n"
        data_p5 = b"N\nA50,50,0,1,1,1,N,\"Test\"\nP5\n"

        match_p1 = re.search(rb"P(\d+)", data_p1)
        match_p5 = re.search(rb"P(\d+)", data_p5)

        assert match_p1 and int(match_p1.group(1)) == 1
        assert match_p5 and int(match_p5.group(1)) == 5
