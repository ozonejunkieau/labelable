"""Tests for BridgePTouchPrinter."""

import asyncio
from unittest.mock import patch

import pytest

from labelable.models.printer import BridgeConnection, PrinterConfig, PrinterType
from labelable.printers.base import PrinterError
from labelable.printers.bridge import DAEMON_STALE_TIMEOUT, BridgePTouchPrinter


def _make_bridge_config(serial: str = "ABC123", tape_width_mm: int | None = 9) -> PrinterConfig:
    return PrinterConfig(
        name="test-bridge",
        type=PrinterType.PTOUCH,
        connection=BridgeConnection(serial_number=serial, tape_width_mm=tape_width_mm),
    )


class TestBridgePTouchPrinter:
    """Tests for the bridge printer with long-polling architecture."""

    def test_create_bridge_printer(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        assert printer.name == "test-bridge"
        assert not printer._daemon_online

    async def test_connect_sets_connected(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        await printer.connect()
        assert printer.is_connected
        await printer.disconnect()
        assert not printer.is_connected

    async def test_is_online_returns_false_by_default(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        assert await printer.is_online() is False

    async def test_is_online_returns_true_after_status_report(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        printer.report_status(online=True, model_info="P-Touch (9mm Tape)")
        assert await printer.is_online() is True
        assert printer.model_info == "P-Touch (9mm Tape)"

    async def test_is_online_returns_false_when_stale(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        # Use a fixed time base so the test works on fresh CI containers
        # where time.monotonic() may be very small
        base_time = 1000.0
        with patch("time.monotonic", return_value=base_time):
            printer.report_status(online=True)
        # Now advance time past the stale threshold
        with patch("time.monotonic", return_value=base_time + DAEMON_STALE_TIMEOUT + 60):
            assert await printer.is_online() is False

    async def test_get_media_size_from_config(self):
        config = _make_bridge_config(tape_width_mm=9)
        printer = BridgePTouchPrinter(config)
        result = await printer.get_media_size()
        assert result == (9.0, 0.0)

    async def test_get_media_size_none_when_not_set(self):
        config = _make_bridge_config(tape_width_mm=None)
        printer = BridgePTouchPrinter(config)
        result = await printer.get_media_size()
        assert result is None

    async def test_take_pending_job_returns_none_when_empty(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        assert printer.take_pending_job() is None

    async def test_print_raw_and_poll_flow(self):
        """Test the full print_raw -> poll -> result flow."""
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        test_data = b"\x1b@\x1bia\x01" + b"\x00" * 100

        async def simulate_daemon():
            """Simulate daemon polling and reporting result."""
            # Wait a bit for print_raw to store the data
            await asyncio.sleep(0.05)
            data = printer.take_pending_job()
            assert data == test_data
            # Simulate print success
            printer.report_result(ok=True)

        # Run print_raw and daemon simulation concurrently
        await asyncio.gather(
            printer.print_raw(test_data),
            simulate_daemon(),
        )

    async def test_print_raw_raises_on_failure_result(self):
        """Test that print_raw raises when daemon reports failure."""
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)

        async def simulate_daemon_failure():
            await asyncio.sleep(0.05)
            printer.take_pending_job()
            printer.report_result(ok=False, error="USB write failed")

        with pytest.raises(PrinterError, match="USB write failed"):
            await asyncio.gather(
                printer.print_raw(b"\x00" * 10),
                simulate_daemon_failure(),
            )

    async def test_print_raw_raises_on_timeout(self):
        """Test that print_raw raises if daemon doesn't respond."""
        from labelable.printers import bridge

        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)

        # Temporarily reduce timeout for fast test
        original_timeout = bridge.JOB_TIMEOUT
        bridge.JOB_TIMEOUT = 0.1
        try:
            with pytest.raises(PrinterError, match="timeout"):
                await printer.print_raw(b"\x00" * 10)
        finally:
            bridge.JOB_TIMEOUT = original_timeout

    def test_report_status_updates_tape_width(self):
        config = _make_bridge_config(tape_width_mm=None)
        printer = BridgePTouchPrinter(config)
        assert printer.config.connection.tape_width_mm is None
        printer.report_status(online=True, tape_width_mm=12)
        assert printer.config.connection.tape_width_mm == 12

    def test_report_status_stores_extended_fields(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        printer.report_status(
            online=True,
            model_info="P-Touch (9mm Laminated Tape)",
            tape_width_mm=9,
            media_kind="Laminated Tape",
            tape_colour="White",
            text_colour="Black",
            low_battery=False,
            errors=[],
        )
        assert printer.media_kind == "Laminated Tape"
        assert printer.tape_colour == "White"
        assert printer.text_colour == "Black"
        assert printer.low_battery is False
        assert printer.errors == []

    def test_report_status_with_errors_and_low_battery(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        printer.report_status(
            online=False,
            low_battery=True,
            errors=["WEAK_BATTERY", "NO_MEDIA"],
        )
        assert printer.low_battery is True
        assert printer.errors == ["WEAK_BATTERY", "NO_MEDIA"]

    def test_extended_fields_default_to_none(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        assert printer.media_kind is None
        assert printer.tape_colour is None
        assert printer.text_colour is None
        assert printer.low_battery is None
        assert printer.errors == []

    def test_report_status_clears_extended_fields_when_not_provided(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        # First report with all fields
        printer.report_status(
            online=True,
            media_kind="Laminated Tape",
            tape_colour="White",
            text_colour="Black",
            low_battery=True,
            errors=["WEAK_BATTERY"],
        )
        assert printer.media_kind == "Laminated Tape"
        # Second report without extended fields — should clear them
        printer.report_status(online=True)
        assert printer.media_kind is None
        assert printer.tape_colour is None
        assert printer.text_colour is None
        assert printer.low_battery is None
        assert printer.errors == []

    def test_take_pending_job_clears_data(self):
        config = _make_bridge_config()
        printer = BridgePTouchPrinter(config)
        printer._pending_data = b"test"
        assert printer.take_pending_job() == b"test"
        assert printer.take_pending_job() is None


class TestBridgeConnectionModel:
    """Tests for the BridgeConnection model."""

    def test_bridge_connection_defaults(self):
        conn = BridgeConnection(serial_number="ABC")
        assert conn.type == "bridge"
        assert conn.tape_width_mm is None

    def test_bridge_connection_with_tape_width(self):
        conn = BridgeConnection(serial_number="ABC", tape_width_mm=12)
        assert conn.tape_width_mm == 12

    def test_bridge_connection_in_printer_config(self):
        config = PrinterConfig(
            name="bridge-printer",
            type=PrinterType.PTOUCH,
            connection=BridgeConnection(serial_number="XYZ"),
        )
        assert isinstance(config.connection, BridgeConnection)
        assert config.connection.serial_number == "XYZ"
