"""Tests for Home Assistant integration support."""

import base64
import importlib.util
import os
from pathlib import Path
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
                "entity_id": "sensor.warehouse_printer_language",
                "state": "ZPL",
                "attributes": {"friendly_name": "Warehouse Printer Language"},
            },
            {
                "entity_id": "sensor.label_maker_language",
                "state": "EPL2",
                "attributes": {"friendly_name": "Label Maker Language"},
            },
            {
                "entity_id": "binary_sensor.warehouse_printer_ready",
                "state": "on",
                "attributes": {"friendly_name": "Warehouse Printer Ready"},
            },
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "Living Room Light"},
            },
        ]

    async def test_discover_no_supervisor_token(self):
        """Test discovery returns empty list without SUPERVISOR_TOKEN."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure SUPERVISOR_TOKEN is not set
            os.environ.pop("SUPERVISOR_TOKEN", None)
            printers = await discover_ha_printers()
            assert printers == []

    async def test_discover_finds_printers(self, mock_ha_states_response):
        """Test discovery finds printers from HA states API."""
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
        assert epl_printer.connection.device_id == "label_maker"

    async def test_discover_handles_api_error(self):
        """Test discovery handles API errors gracefully."""
        mock_response = AsyncMock()
        mock_response.status = 401  # Unauthorized
        mock_response.text = AsyncMock(return_value="Unauthorized")

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
            json={"device_id": "printer_123", "data_b64": base64.b64encode(b"^XA^FDHello^FS^XZ").decode()},
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

        await printer._send_via_ha(b'N\nA50,50,0,1,1,1,N,"Hello"\nP1\n')

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args[1]["json"]["device_id"] == "epl_printer"


def _load_payload_module():
    """Load the integration's payload helper without Home Assistant installed."""
    path = Path(__file__).parent.parent / "custom_components" / "zebra_printer" / "payload.py"
    spec = importlib.util.spec_from_file_location("zebra_printer_payload", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _response(status: int, text: str = ""):
    resp = AsyncMock()
    resp.status = status

    async def _text():
        return text

    resp.text = _text
    return resp


def _ha_epl2_printer(*responses):
    from labelable.printers.epl2 import EPL2Printer

    printer = EPL2Printer(
        PrinterConfig(name="lp2844", type=PrinterType.EPL2, connection=HAConnection(device_id="lp2844"))
    )
    session = MagicMock()
    session.post = MagicMock(side_effect=[create_async_context_manager(r) for r in responses])
    printer._ha_session = session
    printer._ha_device_id = "lp2844"
    printer._ha_url = "http://ha.local"
    return printer, session


# An EPL2 GW bitmap is arbitrary binary, including bytes >= 0x80
BINARY_EPL2 = b'N\nGW10,10,2,2,\x00\xff\x80\x7f\nA10,50,0,3,1,1,N,"caf\xe9"\nP1\n'


class TestHABinaryPayload:
    """Bytes must reach the printer unchanged through the HA service call."""

    @pytest.mark.asyncio
    async def test_binary_job_round_trips_through_integration(self):
        printer, session = _ha_epl2_printer(_response(200))
        await printer._send_via_ha(BINARY_EPL2)

        sent = session.post.call_args.kwargs["json"]
        assert "data" not in sent
        payload = _load_payload_module()
        assert payload.payload_bytes(sent.get("data"), sent.get("data_b64")) == BINARY_EPL2

    def test_old_text_path_corrupted_binary(self):
        """Positive control for the bug: latin-1 out, UTF-8 back in."""
        assert BINARY_EPL2.decode("latin-1").encode() != BINARY_EPL2

    def test_text_data_is_utf8(self):
        payload = _load_payload_module()
        assert payload.payload_bytes("^XA^FDcafé^FS^XZ", None) == "^XA^FDcafé^FS^XZ".encode()

    @pytest.mark.parametrize(("data", "data_b64"), [(None, None), (None, "not base64!")])
    def test_invalid_payload_rejected(self, data, data_b64):
        with pytest.raises(ValueError):
            _load_payload_module().payload_bytes(data, data_b64)

    @pytest.mark.asyncio
    async def test_old_integration_ascii_job_falls_back_to_text(self):
        printer, session = _ha_epl2_printer(_response(400, "extra keys not allowed"), _response(200))
        await printer._send_via_ha(b'N\nA10,10,0,3,1,1,N,"hi"\nP1\n')
        assert session.post.call_count == 2
        assert session.post.call_args.kwargs["json"]["data"] == 'N\nA10,10,0,3,1,1,N,"hi"\nP1\n'

    @pytest.mark.asyncio
    async def test_old_integration_binary_job_refused_not_corrupted(self):
        printer, session = _ha_epl2_printer(_response(400, "extra keys not allowed"))
        with pytest.raises(ConnectionError, match="integration updated"):
            await printer._send_via_ha(BINARY_EPL2)
        assert session.post.call_count == 1


def _load_matching_module():
    path = Path(__file__).parent.parent / "custom_components" / "zebra_printer" / "matching.py"
    spec = importlib.util.spec_from_file_location("zebra_printer_matching", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDeviceNameMatching:
    TITLES = {"e1": "Office Printer", "e2": "Garage Printer", "e3": "Shed"}

    def test_exact_match_wins(self):
        assert _load_matching_module().match_by_title(self.TITLES, "office_printer") == "e1"

    def test_exact_match_beats_partial(self):
        titles = {"e1": "Printer Two", "e2": "Printer"}
        assert _load_matching_module().match_by_title(titles, "printer") == "e2"

    def test_unique_partial_match_accepted(self):
        assert _load_matching_module().match_by_title(self.TITLES, "garage") == "e2"

    def test_ambiguous_partial_match_refused(self):
        with pytest.raises(ValueError, match="more than one"):
            _load_matching_module().match_by_title(self.TITLES, "printer")

    def test_no_match(self):
        assert _load_matching_module().match_by_title(self.TITLES, "kitchen") is None
