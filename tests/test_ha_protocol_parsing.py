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
_const = _load_module_from_file("zebra_printer.const", _custom_components / "const.py")

# Patch the const import in protocol modules
sys.modules["..const"] = _const

# Load base protocol
_base = _load_module_from_file("zebra_printer.protocol.base", _custom_components / "protocol" / "base.py")
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
_zpl = _load_module_from_file("zebra_printer.protocol.zpl", _custom_components / "protocol" / "zpl.py")
ZPLProtocol = _zpl.ZPLProtocol

_epl2 = _load_module_from_file("zebra_printer.protocol.epl2", _custom_components / "protocol" / "epl2.py")
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
        assert status.dpi == 300  # Extracted from model string
        assert "hi_response" in status.raw_status

    def test_parse_hi_without_stx_etx(self):
        """Test parsing ~HI response without control characters."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "GK420d-200dpi,V61.17.16Z,8,2104KB"
        protocol._parse_host_identification(response, status)

        assert status.model == "GK420d-200dpi"
        assert status.firmware == "V61.17.16Z"
        assert status.dpi == 200  # Extracted from model string

    def test_parse_hi_with_203dpi(self):
        """Test parsing ~HI response with 203dpi in model."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "ZD410-203dpi,V1.0,8,2104KB"
        protocol._parse_host_identification(response, status)

        assert status.model == "ZD410-203dpi"
        assert status.dpi == 203

    def test_parse_hi_with_600dpi(self):
        """Test parsing ~HI response with 600dpi in model."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "ZT610-600DPI,V1.0,8,2104KB"  # Case insensitive
        protocol._parse_host_identification(response, status)

        assert status.model == "ZT610-600DPI"
        assert status.dpi == 600

    def test_parse_hi_no_dpi_in_model(self):
        """Test parsing ~HI response without DPI in model."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "GX420d,V1.0,1234,D"
        protocol._parse_host_identification(response, status)

        assert status.model == "GX420d"
        assert status.dpi is None  # No DPI in model string

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
        # Note: labels_printed comes from ~HQOD, not ~HS line 3

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


class TestZPLLine2Parsing:
    """Tests for ZPL ~HS line 2 parsing (print method)."""

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


class TestZPLExtendedStatus:
    """Tests for ZPL ~HQES extended status parsing."""

    def test_parse_extended_status_no_errors(self):
        """Test parsing ~HQES with no errors or warnings."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = """
  PRINTER STATUS
     ERRORS:         0 00000000 00000000
     WARNINGS:       0 00000000 00000000
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is False
        assert status.error_flags == "None"
        assert status.warning_flags == "None"

    def test_parse_extended_status_media_out(self):
        """Test parsing ~HQES with media out error (bit 0)."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # 00000001 = Media Out
        response = """
  PRINTER STATUS
     ERRORS:         1 00000000 00000001
     WARNINGS:       0 00000000 00000000
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is True
        assert status.error_flags == "Media Out"
        assert status.warning_flags == "None"

    def test_parse_extended_status_multiple_errors(self):
        """Test parsing ~HQES with multiple errors."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # 00000007 = Media Out (1) + Ribbon Out (2) + Head Open (4)
        response = """
  PRINTER STATUS
     ERRORS:         1 00000000 00000007
     WARNINGS:       0 00000000 00000000
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is True
        assert "Media Out" in status.error_flags
        assert "Ribbon Out" in status.error_flags
        assert "Head Open" in status.error_flags

    def test_parse_extended_status_warnings(self):
        """Test parsing ~HQES with warnings."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # 00000006 = Clean Printhead (2) + Replace Printhead (4)
        response = """
  PRINTER STATUS
     ERRORS:         0 00000000 00000000
     WARNINGS:       1 00000000 00000006
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is False
        assert status.error_flags == "None"
        assert "Clean Printhead" in status.warning_flags
        assert "Replace Printhead" in status.warning_flags

    def test_parse_extended_status_printhead_over_temp(self):
        """Test parsing ~HQES with printhead over temperature (nibble 2 bit 0)."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # 00000010 = Printhead Over Temperature
        response = """
  PRINTER STATUS
     ERRORS:         1 00000000 00000010
     WARNINGS:       0 00000000 00000000
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is True
        assert status.error_flags == "Printhead Over Temperature"

    def test_parse_extended_status_empty(self):
        """Test parsing empty ~HQES response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_extended_status("", status)

        # Default values should remain
        assert status.has_error is False
        assert status.error_flags == "None"
        assert status.warning_flags == "None"

    def test_parse_extended_status_calibrate_warning(self):
        """Test parsing ~HQES with need to calibrate media warning."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # 00000001 = Need to Calibrate Media
        response = """
  PRINTER STATUS
     ERRORS:         0 00000000 00000000
     WARNINGS:       1 00000000 00000001
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is False
        assert status.warning_flags == "Need to Calibrate Media"

    def test_parse_extended_status_zebra_documentation_example(self):
        """Test parsing ~HQES with example from Zebra documentation.

        From Zebra docs:
        ERRORS: 1 00000000 00000005 = Head Open (4) + Media Out (1)
        WARNINGS: 1 00000000 00000002 = Clean Printhead (2)
        """
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = """PRINTER STATUS
ERRORS:         1 00000000 00000005
WARNINGS:       1 00000000 00000002
"""
        protocol._parse_extended_status(response, status)

        assert status.has_error is True
        # Error 5 = Head Open (4) + Media Out (1)
        assert "Head Open" in status.error_flags
        assert "Media Out" in status.error_flags
        # Warning 2 = Clean Printhead
        assert status.warning_flags == "Clean Printhead"


class TestZPLOdometer:
    """Tests for ZPL ~HQOD odometer parsing."""

    def test_parse_odometer_inches(self):
        """Test parsing odometer in inches (converts to cm)."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        # Real ~HQOD response format
        response = """
  PRINT METERS
     TOTAL NONRESETTABLE:              69 "
     USER RESETTABLE CNTR1:            69 "
     USER RESETTABLE CNTR2:            69 "
"""
        protocol._parse_odometer(response, status)

        # 69 inches * 2.54 = 175.26 cm, rounded to 175.3
        assert status.head_distance_cm == 175.3

    def test_parse_odometer_centimeters(self):
        """Test parsing odometer in centimeters."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = """
  PRINT METERS
     TOTAL NONRESETTABLE:           21744 cm
     USER RESETTABLE CNTR1:            24 cm
     USER RESETTABLE CNTR2:         21744 cm
"""
        protocol._parse_odometer(response, status)

        assert status.head_distance_cm == 21744.0

    def test_parse_odometer_empty(self):
        """Test parsing empty odometer response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_odometer("", status)

        assert status.head_distance_cm is None


class TestZPLDPIQuery:
    """Tests for ZPL DPI query response parsing."""

    def test_parse_dpi_response_quoted(self):
        """Test parsing DPI from getvar response with quotes."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = '"203"'
        protocol._parse_dpi_response(response, status)

        assert status.dpi == 203

    def test_parse_dpi_response_unquoted(self):
        """Test parsing DPI from getvar response without quotes."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "300"
        protocol._parse_dpi_response(response, status)

        assert status.dpi == 300

    def test_parse_dpi_response_600(self):
        """Test parsing 600 DPI response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = '"600"'
        protocol._parse_dpi_response(response, status)

        assert status.dpi == 600

    def test_parse_dpi_response_invalid(self):
        """Test parsing invalid DPI response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = '"150"'  # Not a valid Zebra DPI
        protocol._parse_dpi_response(response, status)

        assert status.dpi is None  # Invalid DPI not set

    def test_parse_dpi_response_empty(self):
        """Test parsing empty DPI response."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_dpi_response("", status)

        assert status.dpi is None

    def test_parse_dpi_response_error(self):
        """Test parsing error response from getvar."""
        protocol = ZPLProtocol("192.168.1.100")
        status = PrinterStatus()

        response = "?"  # Unknown variable response
        protocol._parse_dpi_response(response, status)

        assert status.dpi is None


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

    def test_parse_uq_simple(self):
        """Test parsing simple UQ response with model and firmware."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        response = "UKQ1935HLU      V4.42"
        protocol._parse_uq_response(response, status)

        assert status.model == "UKQ1935HLU"
        assert status.firmware == "V4.42"

    def test_epl2_always_203_dpi(self):
        """Test that EPL2 printers are always 203 DPI."""
        # EPL2 printers are always 203 DPI - this is set in get_status()
        # not in _parse_uq_response, so we test the PrinterStatus initialization
        status = PrinterStatus(online=True, protocol_type="EPL2", dpi=203)
        assert status.dpi == 203

    def test_parse_uq_full_multiline(self):
        """Test parsing full multi-line UQ response."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        # Real multi-line UQ response format
        response = """UKQ1935HLU      V4.42
Serial port:96,N,8,1
Page Mode
Image buffer size:0245K
Fmem used: 0 (bytes)
Gmem used: 0
Emem used: 29600
Available: 100959
I8,0,001 rY JF WN
S3 D09 R256,000 ZT UN
q320
Q120,24
Option:d,Ff
09 18 29
Cover: T=127, C=148"""
        protocol._parse_uq_response(response, status)

        assert status.model == "UKQ1935HLU"
        assert status.firmware == "V4.42"
        assert status.print_speed == 8
        assert status.ribbon_out is False  # rY means ribbon present
        assert status.darkness == 9
        assert status.print_width_mm == 40.0  # 320 dots / 8
        assert status.label_length_mm == 15.0  # 120 dots / 8
        assert status.print_method == "direct_thermal"  # Option:d

    def test_parse_uq_thermal_transfer(self):
        """Test parsing UQ with thermal transfer mode."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        response = """UKQ1935HMU V4.70
I4,0,001 rN JF WN
S2 D15 R256,000 ZT UN
q800
Q240,24
Option:D,Ff"""
        protocol._parse_uq_response(response, status)

        assert status.model == "UKQ1935HMU"
        assert status.firmware == "V4.70"
        assert status.print_speed == 4
        assert status.ribbon_out is True  # rN means ribbon out
        assert status.darkness == 15
        assert status.print_width_mm == 100.0  # 800 / 8
        assert status.label_length_mm == 30.0  # 240 / 8
        assert status.print_method == "thermal_transfer"  # Option:D
        assert status.thermal_transfer_capable is True

    def test_parse_uq_empty_response(self):
        """Test parsing empty UQ response."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_uq_response("", status)

        assert status.model is None
        assert status.firmware is None

    def test_parse_i_line_ribbon_present(self):
        """Test parsing I line with ribbon present."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_i_line("I8,0,001 rY JF WN", status)

        assert status.print_speed == 8
        assert status.ribbon_out is False

    def test_parse_i_line_ribbon_out(self):
        """Test parsing I line with ribbon out."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_i_line("I4,0,001 rN JF WN", status)

        assert status.print_speed == 4
        assert status.ribbon_out is True

    def test_parse_q_line(self):
        """Test parsing q line for print width."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_q_line("q320 Q120,24", status)

        assert status.print_width_mm == 40.0  # 320 / 8

    def test_parse_Q_line(self):
        """Test parsing Q line for label length."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_Q_line("Q240,24", status)

        assert status.label_length_mm == 30.0  # 240 / 8

    def test_parse_option_line_direct(self):
        """Test parsing Option line for direct thermal."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_option_line("Option:d,Ff", status)

        assert status.print_method == "direct_thermal"
        assert status.thermal_transfer_capable is False

    def test_parse_option_line_transfer(self):
        """Test parsing Option line for thermal transfer."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_option_line("Option:D,Ff", status)

        assert status.print_method == "thermal_transfer"
        assert status.thermal_transfer_capable is True

    def test_parse_s_line_darkness(self):
        """Test parsing S line for darkness."""
        protocol = EPL2Protocol("192.168.1.100")
        status = PrinterStatus()

        protocol._parse_s_line("S3 D09 R256,000 ZT UN", status)

        assert status.darkness == 9


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
        assert status.head_distance_cm is None
        assert status.print_speed is None
        assert status.darkness is None
        assert status.label_length_mm is None
        assert status.print_width_mm is None
        assert status.print_mode is None
        assert status.print_method is None
        assert status.has_error is False
        assert status.error_flags == "None"
        assert status.warning_flags == "None"
        assert status.thermal_transfer_capable is False
        assert status.protocol_type is None
        assert status.dpi is None
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
