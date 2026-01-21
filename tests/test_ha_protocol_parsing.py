"""Tests for HA integration protocol parsing.

These tests cover the ZPL and EPL2 protocol parsing logic in the
custom_components/zebra_printer/protocol/ module.

Note: We use importlib to load modules directly without triggering
the package __init__.py which has HA dependencies.
"""

import importlib.util
import sys
import types
from pathlib import Path

# Load modules directly from files to avoid HA dependencies


def _load_module_from_file(name: str, filepath: Path):
    """Load a module directly from a file path."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Get the custom_components path
_custom_components = Path(__file__).parent.parent / "custom_components" / "zebra_printer"

# Load const module first (has no dependencies)
_const = _load_module_from_file(
    "zebra_printer.const",
    _custom_components / "const.py"
)

# Patch the const import in protocol modules
sys.modules["..const"] = _const

# Load base protocol
_base = _load_module_from_file(
    "zebra_printer.protocol.base",
    _custom_components / "protocol" / "base.py"
)
PrinterStatus = _base.PrinterStatus

# Now we need to make the relative import work for zpl.py and epl2.py
# They import from ..const, so we'll create a mock package structure
zebra_printer_pkg = types.ModuleType("zebra_printer")
zebra_printer_pkg.const = _const
sys.modules["zebra_printer"] = zebra_printer_pkg

# Also set up the protocol subpackage
protocol_pkg = types.ModuleType("zebra_printer.protocol")
protocol_pkg.base = _base
sys.modules["zebra_printer.protocol"] = protocol_pkg

# Now load zpl and epl2 with the mocked structure
_zpl = _load_module_from_file(
    "zebra_printer.protocol.zpl",
    _custom_components / "protocol" / "zpl.py"
)
ZPLProtocol = _zpl.ZPLProtocol

_epl2 = _load_module_from_file(
    "zebra_printer.protocol.epl2",
    _custom_components / "protocol" / "epl2.py"
)
EPL2Protocol = _epl2.EPL2Protocol


class TestZPLHostIdentification:
    """Tests for ZPL ~HI response parsing."""

    def test_parse_hi_full_response(self):
        """Test parsing complete ~HI response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # Typical ~HI response with STX/ETX
        response = "\x02ZTC ZD420-300dpi ZPL,V84.20.21Z,300,262144\x03"
        protocol._parse_host_identification(response, status)

        assert status.model == "ZTC ZD420-300dpi ZPL"
        assert status.firmware == "V84.20.21Z"
        assert "hi_response" in status.raw_status

    def test_parse_hi_without_stx_etx(self):
        """Test parsing ~HI response without control characters."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "GK420d-200dpi,V61.17.16Z,8,2104KB"
        protocol._parse_host_identification(response, status)

        assert status.model == "GK420d-200dpi"
        assert status.firmware == "V61.17.16Z"

    def test_parse_hi_empty_response(self):
        """Test parsing empty ~HI response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_host_identification("", status)

        assert status.model is None
        assert status.firmware is None

    def test_parse_hi_only_model(self):
        """Test parsing ~HI response with only model."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "ZD410"
        protocol._parse_host_identification(response, status)

        assert status.model == "ZD410"
        assert status.firmware is None


class TestZPLHostStatus:
    """Tests for ZPL ~HS response parsing."""

    def test_parse_hs_normal_status(self):
        """Test parsing normal ~HS response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # Typical ~HS response (3 lines)
        # Line 1: comm,paper_out,pause,label_length,labels_remaining,buffer_full,...
        # Line 2: func,unused,head_up,ribbon_out,thermal,mode,width,speed,unused,unused,darkness
        # Line 3: labels_printed,unused
        response = "\x020,0,0,0800,0,0,0,0,0,0,0,0\r\n0,0,0,0,1,2,832,4,0,0,15\r\n1234,0"
        protocol._parse_host_status(response, status)

        assert status.paper_out is False
        assert status.paused is False
        assert status.buffer_full is False
        assert status.head_open is False
        assert status.ribbon_out is False
        assert status.print_mode == "tear_off"  # mode 2
        assert status.label_length_mm == 100.0  # 800 dots / 8
        assert status.print_method == "thermal_transfer"  # field 4 = 1
        assert status.print_speed == 4  # field 7
        assert status.darkness == 15  # field 10
        assert status.labels_printed == 1234  # line 3 field 0

    def test_parse_hs_error_status(self):
        """Test parsing ~HS response with errors."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # Paper out, paused, head open
        response = "\x020,1,1,0800,0,0,0,0,0,0,0,0\r\n0,0,1,1,0,2,0832,4,0,0,0,0\r\n0,0,0"
        protocol._parse_host_status(response, status)

        assert status.paper_out is True
        assert status.paused is True
        assert status.head_open is True
        assert status.ribbon_out is True

    def test_parse_hs_different_print_modes(self):
        """Test parsing different print modes."""
        protocol = ZPLProtocol("192.168.1.100")

        modes = {
            "0": "rewind",
            "1": "peel_off",
            "2": "tear_off",
            "3": "cutter",
            "4": "delayed_cut",
            "5": "rfid",
            "6": "applicator",
            "9": "unknown",  # Invalid mode
        }

        for mode_code, expected_mode in modes.items():
            status = PrinterStatus()
            response = f"\x020,0,0,0800,0,0,0,0,0,0,0,0\r\n0,0,0,0,0,{mode_code},0832,4"
            protocol._parse_host_status(response, status)
            assert status.print_mode == expected_mode

    def test_parse_hs_empty_response(self):
        """Test parsing empty ~HS response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_host_status("", status)

        assert status.paper_out is None
        assert status.paused is None

    def test_parse_hs_single_line(self):
        """Test parsing ~HS with only one line returns early."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # Single line response - parser requires 2 lines minimum
        response = "\x020,1,0,0800,0,1,0,0,0,0,0,0"
        protocol._parse_host_status(response, status)

        # Parser returns early with < 2 lines, so nothing is parsed
        assert status.paper_out is None
        assert status.buffer_full is None
        assert status.head_open is None
        # But raw response is still stored
        assert "hs_response" in status.raw_status


class TestZPLLine3Parsing:
    """Tests for ZPL ~HS line 3 parsing (labels printed)."""

    def test_parse_hs_line3_labels_printed(self):
        """Test parsing labels printed from line 3."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # 3-line response with label count in line 3
        response = "\x020,0,0,0800,0,0,0,0,0,0,0,0\r\n0,0,0,0,0,2,832,4\r\n5678,0"
        protocol._parse_host_status(response, status)

        assert status.labels_printed == 5678

    def test_parse_hs_direct_thermal_mode(self):
        """Test parsing direct thermal mode from line 2 field 4."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # field 4 = 0 means direct thermal
        response = "\x020,0,0,0800,0,0,0,0,0,0,0,0\r\n0,0,0,0,0,2,832,4\r\n0,0"
        protocol._parse_host_status(response, status)

        assert status.print_method == "direct_thermal"

    def test_parse_hs_thermal_transfer_mode(self):
        """Test parsing thermal transfer mode from line 2 field 4."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # field 4 = 1 means thermal transfer
        response = "\x020,0,0,0800,0,0,0,0,0,0,0,0\r\n0,0,0,0,1,2,832,4\r\n0,0"
        protocol._parse_host_status(response, status)

        assert status.print_method == "thermal_transfer"


class TestZPLCommands:
    """Tests for ZPL command generation."""

    def test_calibrate_command(self):
        """Test calibration command."""
        protocol = ZPLProtocol("192.168.1.100")
        assert protocol.get_calibrate_command() == "~JC"

    def test_feed_command_single(self):
        """Test single feed command."""
        protocol = ZPLProtocol("192.168.1.100")
        assert protocol.get_feed_command(1) == "^XA^XZ"

    def test_feed_command_multiple(self):
        """Test multiple feed command."""
        protocol = ZPLProtocol("192.168.1.100")
        assert protocol.get_feed_command(3) == "^XA^XZ^XA^XZ^XA^XZ"


class TestEPL2StatusParsing:
    """Tests for EPL2 UQ response parsing."""

    def test_parse_uq_single_line(self):
        """Test parsing single-line UQ response."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        response = "UKQ1935HMU V4.70,8,200,0001,000"
        protocol._parse_uq_response(response, status)

        assert status.model == "UKQ1935HMU"
        assert status.firmware == "V4.70"
        assert status.print_speed == 8

    def test_parse_uq_single_line_no_comma(self):
        """Test parsing single-line UQ response without commas."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        # User reported this format: "UKQ1935HLU V4.42"
        response = "UKQ1935HLU V4.42"
        protocol._parse_uq_response(response, status)

        assert status.model == "UKQ1935HLU"
        assert status.firmware == "V4.42"

    def test_parse_uq_multi_line(self):
        """Test parsing multi-line UQ response."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        response = "LP2844\nV4.45\nSerial: ABC123"
        protocol._parse_uq_response(response, status)

        assert status.model == "LP2844"
        assert status.firmware == "V4.45"

    def test_parse_uq_empty_response(self):
        """Test parsing empty UQ response."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_uq_response("", status)

        assert status.model is None
        assert status.firmware is None

    def test_parse_status_flags(self):
        """Test parsing EPL2 status flags."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        # Flags: 0x01=paper_out, 0x02=paused, 0x04=head_open, 0x08=ribbon_out
        # 0x0F = all flags set
        response = "LP2844 V4.45,8,200,0001,15"
        protocol._parse_uq_response(response, status)

        assert status.paper_out is True
        assert status.paused is True
        assert status.head_open is True
        assert status.ribbon_out is True

    def test_parse_status_flags_none_set(self):
        """Test parsing EPL2 status flags with none set."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        response = "LP2844 V4.45,8,200,0001,0"
        protocol._parse_uq_response(response, status)

        assert status.paper_out is False
        assert status.paused is False


class TestEPL2Commands:
    """Tests for EPL2 command generation."""

    def test_calibrate_command(self):
        """Test calibration command."""
        protocol = EPL2Protocol("192.168.1.100")
        assert protocol.get_calibrate_command() == "xa"

    def test_feed_command_single(self):
        """Test single feed command."""
        protocol = EPL2Protocol("192.168.1.100")
        assert protocol.get_feed_command(1) == "P1"

    def test_feed_command_multiple(self):
        """Test multiple feed command."""
        protocol = EPL2Protocol("192.168.1.100")
        assert protocol.get_feed_command(5) == "P5"


class TestPrinterStatus:
    """Tests for PrinterStatus dataclass."""

    def test_default_values(self):
        """Test PrinterStatus default values."""
        status = PrinterStatus()

        assert status.online is False
        assert status.model is None
        assert status.firmware is None
        assert status.head_open is None
        assert status.paper_out is None
        assert status.ribbon_out is None
        assert status.paused is None
        assert status.buffer_full is None
        assert status.labels_printed is None
        assert status.head_distance_inches is None
        assert status.print_speed is None
        assert status.darkness is None
        assert status.label_length_mm is None
        assert status.print_width_mm is None
        assert status.print_mode is None
        assert status.raw_status == {}

    def test_online_status(self):
        """Test setting online status."""
        status = PrinterStatus(online=True)
        assert status.online is True

    def test_raw_status_storage(self):
        """Test that raw responses can be stored."""
        status = PrinterStatus()
        status.raw_status["test_response"] = "raw data here"

        assert status.raw_status["test_response"] == "raw data here"
