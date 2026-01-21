"""Tests for Home Assistant integration support."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labelable.config import discover_ha_printers, load_config_async
from labelable.models.printer import HAConnection, PrinterConfig, PrinterType


def create_async_context_manager(return_value):
    """Helper to create a properly mocked async context manager."""
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=return_value)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm


class TestHAConnectionModel:
    """Tests for the HAConnection model."""

    def test_ha_connection_defaults(self):
        """Test HAConnection with default values."""
        conn = HAConnection(device_id="my_printer")
        assert conn.type == "ha"
        assert conn.device_id == "my_printer"
        assert conn.ha_url == "http://supervisor/core"
        assert conn.ha_token is None

    def test_ha_connection_custom_values(self):
        """Test HAConnection with custom values."""
        conn = HAConnection(
            device_id="warehouse_printer",
            ha_url="http://homeassistant.local:8123",
            ha_token="my_token_123",
        )
        assert conn.device_id == "warehouse_printer"
        assert conn.ha_url == "http://homeassistant.local:8123"
        assert conn.ha_token == "my_token_123"

    def test_ha_connection_in_printer_config(self):
        """Test HAConnection can be used in PrinterConfig."""
        config = PrinterConfig(
            name="ha-printer",
            type=PrinterType.ZPL,
            connection=HAConnection(device_id="test_device"),
        )
        assert config.name == "ha-printer"
        assert config.type == PrinterType.ZPL
        assert isinstance(config.connection, HAConnection)
        assert config.connection.device_id == "test_device"


class TestDiscoverHAPrinters:
    """Tests for discover_ha_printers function."""

    @pytest.fixture
    def mock_ha_states_response(self):
        """Sample HA states API response with zebra_printer entities."""
        return [
            {
                "entity_id": "binary_sensor.warehouse_printer_online",
                "state": "on",
                "attributes": {
                    "device_class": "connectivity",
                    "model": "ZTC ZD420-300dpi ZPL",
                    "friendly_name": "Warehouse Printer Online",
                },
            },
            {
                "entity_id": "binary_sensor.label_maker_online",
                "state": "off",
                "attributes": {
                    "device_class": "connectivity",
                    "model": "LP2844 EPL2",
                    "friendly_name": "Label Maker Online",
                },
            },
            {
                "entity_id": "sensor.warehouse_printer_labels_printed",
                "state": "1234",
                "attributes": {"friendly_name": "Warehouse Printer Labels Printed"},
            },
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "Living Room Light"},
            },
            {
                "entity_id": "binary_sensor.motion_sensor_online",
                "state": "on",
                "attributes": {
                    "device_class": "motion",  # Not connectivity
                    "friendly_name": "Motion Sensor",
                },
            },
        ]

    async def test_discover_no_supervisor_token(self):
        """Test discovery returns empty list without SUPERVISOR_TOKEN."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure SUPERVISOR_TOKEN is not set
            os.environ.pop("SUPERVISOR_TOKEN", None)
            printers = await discover_ha_printers()
            assert printers == []

    async def test_discover_finds_zpl_printer(self, mock_ha_states_response):
        """Test discovery finds ZPL printer from HA states."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_ha_states_response)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=create_async_context_manager(mock_response))

        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "test_token"}):
            with patch("aiohttp.ClientSession") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_client.return_value.__aexit__ = AsyncMock()

                printers = await discover_ha_printers()

        assert len(printers) == 2

        # Check ZPL printer
        zpl_printer = next(p for p in printers if "warehouse" in p.name)
        assert zpl_printer.name == "ha-warehouse_printer"
        assert zpl_printer.type == PrinterType.ZPL
        assert isinstance(zpl_printer.connection, HAConnection)
        assert zpl_printer.connection.device_id == "warehouse_printer"

        # Check EPL2 printer
        epl_printer = next(p for p in printers if "label" in p.name)
        assert epl_printer.name == "ha-label_maker"
        assert epl_printer.type == PrinterType.EPL2

    async def test_discover_handles_api_error(self):
        """Test discovery handles API errors gracefully."""
        mock_response = AsyncMock()
        mock_response.status = 401  # Unauthorized

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=create_async_context_manager(mock_response))

        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "bad_token"}):
            with patch("aiohttp.ClientSession") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_client.return_value.__aexit__ = AsyncMock()

                printers = await discover_ha_printers()

        assert printers == []

    async def test_discover_handles_connection_error(self):
        """Test discovery handles connection errors gracefully."""
        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "test_token"}):
            with patch("aiohttp.ClientSession") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))

                printers = await discover_ha_printers()

        assert printers == []


class TestLoadConfigAsync:
    """Tests for load_config_async function."""

    async def test_load_config_with_printers_skips_discovery(self, tmp_path):
        """Test that discovery is skipped when printers are configured."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
printers:
  - name: my-printer
    type: zpl
    connection:
      type: tcp
      host: 192.168.1.100
      port: 9100
""")

        with patch("labelable.config.discover_ha_printers") as mock_discover:
            config = await load_config_async(config_file)

        # Discovery should not be called when printers exist
        mock_discover.assert_not_called()
        assert len(config.printers) == 1
        assert config.printers[0].name == "my-printer"

    async def test_load_config_empty_triggers_discovery(self, tmp_path):
        """Test that discovery is triggered when no printers configured."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
queue_timeout_seconds: 300
printers: []
""")

        mock_printers = [
            PrinterConfig(
                name="ha-discovered",
                type=PrinterType.ZPL,
                connection=HAConnection(device_id="discovered_printer"),
            )
        ]

        with patch("labelable.config.discover_ha_printers", return_value=mock_printers) as mock_discover:
            config = await load_config_async(config_file)

        mock_discover.assert_called_once()
        assert len(config.printers) == 1
        assert config.printers[0].name == "ha-discovered"

    async def test_load_config_missing_file_triggers_discovery(self, tmp_path):
        """Test that discovery is triggered when config file doesn't exist."""
        config_file = tmp_path / "nonexistent.yaml"

        mock_printers = [
            PrinterConfig(
                name="ha-auto",
                type=PrinterType.EPL2,
                connection=HAConnection(device_id="auto_printer"),
            )
        ]

        with patch("labelable.config.discover_ha_printers", return_value=mock_printers) as mock_discover:
            config = await load_config_async(config_file)

        mock_discover.assert_called_once()
        assert len(config.printers) == 1


class TestPrinterHATransport:
    """Tests for printer HA transport methods."""

    async def test_zpl_connect_ha_with_token(self):
        """Test ZPL printer connects via HA with explicit token."""
        from labelable.printers.zpl import ZPLPrinter

        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=HAConnection(
                device_id="my_device",
                ha_url="http://ha.local:8123",
                ha_token="explicit_token",
            ),
        )

        printer = ZPLPrinter(config)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            await printer.connect()

        # Verify session created with correct auth header
        mock_session_class.assert_called_once()
        call_kwargs = mock_session_class.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer explicit_token"

        assert printer._connected
        assert printer._ha_device_id == "my_device"
        assert printer._ha_url == "http://ha.local:8123"

    async def test_zpl_connect_ha_with_supervisor_token(self):
        """Test ZPL printer connects via HA using SUPERVISOR_TOKEN."""
        from labelable.printers.zpl import ZPLPrinter

        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=HAConnection(device_id="my_device"),  # No explicit token
        )

        printer = ZPLPrinter(config)

        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "supervisor_secret"}):
            with patch("aiohttp.ClientSession") as mock_session_class:
                mock_session = MagicMock()
                mock_session_class.return_value = mock_session

                await printer.connect()

        call_kwargs = mock_session_class.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer supervisor_secret"

    async def test_zpl_send_via_ha(self):
        """Test ZPL printer sends data via HA service call."""
        from labelable.printers.zpl import ZPLPrinter

        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=HAConnection(device_id="printer_123", ha_url="http://ha.local"),
        )

        printer = ZPLPrinter(config)

        # Set up mock session
        mock_response = AsyncMock()
        mock_response.status = 200

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=create_async_context_manager(mock_response))

        printer._ha_session = mock_session
        printer._ha_device_id = "printer_123"
        printer._ha_url = "http://ha.local"
        printer._connected = True

        # Send data
        await printer._send_via_ha(b"^XA^FDHello^FS^XZ")

        # Verify service call
        mock_session.post.assert_called_once_with(
            "http://ha.local/api/services/zebra_printer/print_raw",
            json={"device_id": "printer_123", "data": "^XA^FDHello^FS^XZ"},
        )

    async def test_zpl_send_via_ha_error(self):
        """Test ZPL printer handles HA service call errors."""
        from labelable.printers.zpl import ZPLPrinter

        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=HAConnection(device_id="printer_123"),
        )

        printer = ZPLPrinter(config)

        # Set up mock session with error response
        mock_response = AsyncMock()
        mock_response.status = 500
        # text() is a coroutine that returns a string
        async def mock_text():
            return "Internal Server Error"
        mock_response.text = mock_text

        # Create async context manager mock properly
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_cm)

        printer._ha_session = mock_session
        printer._ha_device_id = "printer_123"
        printer._ha_url = "http://supervisor/core"

        with pytest.raises(ConnectionError, match="HA service call failed: 500"):
            await printer._send_via_ha(b"^XA^XZ")

    async def test_zpl_disconnect_ha(self):
        """Test ZPL printer disconnects HA session."""
        from labelable.printers.zpl import ZPLPrinter

        config = PrinterConfig(
            name="test-zpl",
            type=PrinterType.ZPL,
            connection=HAConnection(device_id="printer_123"),
        )

        printer = ZPLPrinter(config)

        # Set up mock session
        mock_session = AsyncMock()
        printer._ha_session = mock_session
        printer._ha_device_id = "printer_123"
        printer._ha_url = "http://supervisor/core"
        printer._connected = True

        await printer.disconnect()

        mock_session.close.assert_called_once()
        assert printer._ha_session is None
        assert printer._ha_device_id is None
        assert printer._ha_url is None
        assert not printer._connected

    async def test_epl2_connect_ha(self):
        """Test EPL2 printer connects via HA."""
        from labelable.printers.epl2 import EPL2Printer

        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=HAConnection(device_id="epl_printer", ha_token="token123"),
        )

        printer = EPL2Printer(config)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            await printer.connect()

        assert printer._connected
        assert printer._ha_device_id == "epl_printer"

    async def test_epl2_send_via_ha(self):
        """Test EPL2 printer sends data via HA service call."""
        from labelable.printers.epl2 import EPL2Printer

        config = PrinterConfig(
            name="test-epl2",
            type=PrinterType.EPL2,
            connection=HAConnection(device_id="epl_printer"),
        )

        printer = EPL2Printer(config)

        # Set up mock session
        mock_response = AsyncMock()
        mock_response.status = 200

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=create_async_context_manager(mock_response))

        printer._ha_session = mock_session
        printer._ha_device_id = "epl_printer"
        printer._ha_url = "http://supervisor/core"
        printer._connected = True

        await printer._send_via_ha(b"N\nA50,50,0,1,1,1,N,\"Hello\"\nP1\n")

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args[1]["json"]["device_id"] == "epl_printer"
