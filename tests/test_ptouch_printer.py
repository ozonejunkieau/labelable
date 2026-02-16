"""Tests for P-Touch USB printer implementation."""

from unittest.mock import MagicMock, patch

import pytest

from labelable.models.printer import PrinterConfig, PrinterType, TCPConnection, USBConnection
from labelable.printers.base import PrinterError
from labelable.printers.ptouch import PTouchPrinter


def _make_config(connection=None) -> PrinterConfig:
    return PrinterConfig(
        name="test-ptouch",
        type=PrinterType.PTOUCH,
        connection=connection or USBConnection(),
    )


def _make_status_bytes(**overrides: int) -> bytes:
    """Build a 32-byte status response with optional byte overrides by offset."""
    data = bytearray(32)
    # Set standard header bytes
    data[0] = 0x80
    data[1] = 0x20
    data[2] = 0x42
    data[3] = 0x30
    for offset_str, value in overrides.items():
        data[int(offset_str)] = value
    return bytes(data)


def _ok_status(width_mm: int = 12, media_kind: int = 0x01) -> bytes:
    """Build a healthy status response."""
    data = bytearray(32)
    data[10] = width_mm
    data[11] = media_kind
    data[18] = 0x00  # REPLY
    data[24] = 0x01  # White tape
    data[25] = 0x08  # Black text
    return bytes(data)


def _error_status(error1: int = 0, error2: int = 0) -> bytes:
    """Build a status response with error flags."""
    data = bytearray(32)
    data[8] = error1
    data[9] = error2
    data[10] = 12
    data[11] = 0x01
    return bytes(data)


class TestPTouchPrinterInit:
    def test_creates_with_usb_connection(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        assert printer.name == "test-ptouch"
        assert not printer.is_connected

    def test_rejects_non_usb_connection(self):
        config = _make_config(TCPConnection(host="192.168.1.1"))
        printer = PTouchPrinter(config)
        with pytest.raises(PrinterError, match="USB connection"):
            import asyncio

            asyncio.run(printer.connect())


class TestPTouchConnect:
    @patch("labelable.printers.ptouch.usb.core.find")
    async def test_connect_finds_device(self, mock_find):
        mock_dev = MagicMock()
        mock_dev.is_kernel_driver_active.return_value = False
        mock_cfg = MagicMock()
        mock_intf = MagicMock()
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)
        mock_dev.get_active_configuration.return_value = mock_cfg

        mock_ep_out = MagicMock()
        mock_ep_out.bEndpointAddress = 0x02
        mock_ep_in = MagicMock()
        mock_ep_in.bEndpointAddress = 0x81

        mock_intf.__iter__ = MagicMock(return_value=iter([mock_ep_out, mock_ep_in]))
        mock_find.return_value = mock_dev

        with patch("labelable.printers.ptouch.usb.util.find_descriptor", side_effect=[mock_ep_out, mock_ep_in]):
            config = _make_config()
            printer = PTouchPrinter(config)
            await printer.connect()

            assert printer.is_connected
            mock_find.assert_called_once_with(idVendor=0x04F9, idProduct=0x20AF)
            # Init sequence: 100 null bytes + reset (ESC @)
            assert mock_ep_out.write.call_count == 2

    @patch("labelable.printers.ptouch.usb.core.find")
    async def test_connect_device_not_found(self, mock_find):
        mock_find.return_value = None
        config = _make_config()
        printer = PTouchPrinter(config)

        with pytest.raises(ConnectionError, match="not found"):
            await printer.connect()

        assert not printer.is_connected

    @patch("labelable.printers.ptouch.usb.core.find")
    async def test_connect_detaches_kernel_driver(self, mock_find):
        mock_dev = MagicMock()
        mock_dev.is_kernel_driver_active.return_value = True
        mock_cfg = MagicMock()
        mock_intf = MagicMock()
        mock_cfg.__getitem__ = MagicMock(return_value=mock_intf)
        mock_dev.get_active_configuration.return_value = mock_cfg

        mock_ep_out = MagicMock()
        mock_ep_in = MagicMock()
        mock_intf.__iter__ = MagicMock(return_value=iter([mock_ep_out, mock_ep_in]))
        mock_find.return_value = mock_dev

        with patch("labelable.printers.ptouch.usb.util.find_descriptor", side_effect=[mock_ep_out, mock_ep_in]):
            config = _make_config()
            printer = PTouchPrinter(config)
            await printer.connect()

            mock_dev.detach_kernel_driver.assert_called_once_with(0)

    async def test_connect_noop_if_already_connected(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True

        # Should not raise or attempt USB operations
        await printer.connect()
        assert printer.is_connected


class TestPTouchDisconnect:
    async def test_disconnect_disposes_resources(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        mock_dev = MagicMock()
        printer._usb_dev = mock_dev
        printer._usb_ep_out = MagicMock()
        printer._usb_ep_in = MagicMock()
        printer._connected = True

        with patch("labelable.printers.ptouch.usb.util.dispose_resources") as mock_dispose:
            await printer.disconnect()

            mock_dispose.assert_called_once_with(mock_dev)

        assert not printer.is_connected
        assert printer._usb_dev is None
        assert printer._usb_ep_out is None
        assert printer._usb_ep_in is None

    async def test_disconnect_when_not_connected(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        # Should not raise
        await printer.disconnect()
        assert not printer.is_connected


class TestPTouchIsOnline:
    async def test_online_with_healthy_status(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True

        mock_ep_out = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.read.return_value = _ok_status(width_mm=9, media_kind=0x03)
        printer._usb_ep_out = mock_ep_out
        printer._usb_ep_in = mock_ep_in

        online = await printer.is_online()

        assert online is True
        assert printer.get_cached_online_status() is True
        assert printer._last_status is not None
        assert printer._last_status.media_width_mm == 9
        assert printer.model_info is not None
        assert "9mm" in printer.model_info

    async def test_offline_with_error_status(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True

        mock_ep_out = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.read.return_value = _error_status(error1=0x01)  # NO_MEDIA
        printer._usb_ep_out = mock_ep_out
        printer._usb_ep_in = mock_ep_in

        online = await printer.is_online()

        assert online is False
        assert printer.get_cached_online_status() is False

    async def test_offline_with_short_response(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True

        mock_ep_out = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.read.return_value = b"\x00" * 10  # Too short
        printer._usb_ep_out = mock_ep_out
        printer._usb_ep_in = mock_ep_in

        online = await printer.is_online()

        assert online is False

    async def test_offline_with_timeout(self):
        import usb.core

        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True

        mock_ep_out = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.read.side_effect = usb.core.USBTimeoutError("timeout")
        printer._usb_ep_out = mock_ep_out
        printer._usb_ep_in = mock_ep_in

        online = await printer.is_online()

        # Timeout returns b"", which is too short → offline
        assert online is False

    async def test_auto_connects_if_disconnected(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = False

        # Mock connect to set up endpoints
        async def fake_connect():
            printer._connected = True
            printer._usb_ep_out = MagicMock()
            printer._usb_ep_in = MagicMock()
            printer._usb_ep_in.read.return_value = _ok_status()

        printer.connect = fake_connect  # type: ignore[assignment]

        online = await printer.is_online()
        assert online is True


class TestPTouchMediaSize:
    async def test_media_size_from_status(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True

        mock_ep_out = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.read.return_value = _ok_status(width_mm=24)
        printer._usb_ep_out = mock_ep_out
        printer._usb_ep_in = mock_ep_in

        await printer.is_online()
        size = await printer.get_media_size()

        assert size == (24.0, 0.0)

    async def test_media_size_none_before_status(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        size = await printer.get_media_size()
        assert size is None


class TestPTouchPrintRaw:
    async def test_print_raw_sends_data(self):
        config = _make_config()
        printer = PTouchPrinter(config)
        printer._connected = True
        mock_ep_out = MagicMock()
        printer._usb_ep_out = mock_ep_out

        await printer.print_raw(b"\x1b\x40test data")

        mock_ep_out.write.assert_called_once_with(b"\x1b\x40test data")

    async def test_print_raw_raises_if_not_connected(self):
        config = _make_config()
        printer = PTouchPrinter(config)

        with pytest.raises(ConnectionError, match="not connected"):
            await printer.print_raw(b"data")


class TestPTouchSendRecv:
    async def test_send_raises_if_no_endpoint(self):
        config = _make_config()
        printer = PTouchPrinter(config)

        with pytest.raises(ConnectionError, match="USB not connected"):
            await printer._send(b"data")

    async def test_recv_raises_if_no_endpoint(self):
        config = _make_config()
        printer = PTouchPrinter(config)

        with pytest.raises(ConnectionError, match="USB not connected"):
            await printer._recv()

    async def test_recv_returns_empty_on_timeout(self):
        import usb.core

        config = _make_config()
        printer = PTouchPrinter(config)
        mock_ep_in = MagicMock()
        mock_ep_in.read.side_effect = usb.core.USBTimeoutError("timeout")
        printer._usb_ep_in = mock_ep_in

        result = await printer._recv(size=32, timeout=1.0)
        assert result == b""
