"""ZPL protocol implementation for Zebra printers."""

from __future__ import annotations

import re

from ..const import (
    ERROR_FLAGS,
    PRINT_MODE_APPLICATOR,
    PRINT_MODE_CUTTER,
    PRINT_MODE_DELAYED_CUT,
    PRINT_MODE_PEEL,
    PRINT_MODE_REWIND,
    PRINT_MODE_RFID,
    PRINT_MODE_TEAR,
    PRINT_MODE_UNKNOWN,
    WARNING_FLAGS,
    ZPL_CALIBRATE,
    ZPL_EXTENDED_STATUS,
    ZPL_FEED_LABEL,
    ZPL_HOST_IDENTIFICATION,
    ZPL_HOST_STATUS,
    ZPL_ODOMETER,
)
from .base import PrinterProtocol, PrinterStatus

# Print mode mapping from ~HS response
PRINT_MODES = {
    "0": PRINT_MODE_REWIND,
    "1": PRINT_MODE_PEEL,
    "2": PRINT_MODE_TEAR,
    "3": PRINT_MODE_CUTTER,
    "4": PRINT_MODE_DELAYED_CUT,
    "5": PRINT_MODE_RFID,
    "6": PRINT_MODE_APPLICATOR,
}

# Print method (thermal transfer vs direct thermal)
PRINT_METHOD_DIRECT = "direct_thermal"
PRINT_METHOD_TRANSFER = "thermal_transfer"


class ZPLProtocol(PrinterProtocol):
    """ZPL protocol implementation."""

    async def probe(self) -> bool:
        """Probe if ZPL is supported by checking for ~HI response."""
        response = await self.send_command(ZPL_HOST_IDENTIFICATION)
        # ZPL responses typically start with STX (0x02)
        return "\x02" in response or "V" in response

    async def get_status(self) -> PrinterStatus:
        """Get comprehensive printer status."""
        status = PrinterStatus(online=True, protocol_type="ZPL")

        # Get host identification (~HI)
        hi_response = await self.send_command(ZPL_HOST_IDENTIFICATION)
        self._parse_host_identification(hi_response, status)

        # Get host status (~HS) - contains most status info
        hs_response = await self.send_command(ZPL_HOST_STATUS)
        self._parse_host_status(hs_response, status)

        # Get odometer (~HQOD) - head distance in inches
        od_response = await self.send_command(ZPL_ODOMETER)
        self._parse_odometer(od_response, status)

        # Get extended status (~HQES) - error and warning flags
        es_response = await self.send_command(ZPL_EXTENDED_STATUS)
        self._parse_extended_status(es_response, status)

        # Query DPI if not found in model string
        if status.dpi is None:
            dpi_response = await self.send_command('! U1 getvar "head.resolution.in_dpi"')
            self._parse_dpi_response(dpi_response, status)

        return status

    def _parse_dpi_response(self, response: str, status: PrinterStatus) -> None:
        """Parse DPI from getvar response.

        Response format: "203" or "300" or "600" (quoted string)
        """
        if not response:
            return

        status.raw_status["dpi_response"] = response

        # Extract numeric value from response (may be quoted)
        match = re.search(r'"?(\d{3})"?', response)
        if match:
            dpi = int(match.group(1))
            if dpi in (203, 300, 600):
                status.dpi = dpi

    def _parse_host_identification(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HI response for model, firmware, and capabilities.

        Format: STX <model>,<firmware>,<dpi>,<print_method_capability> ETX
        Example: \x02GX430t-300dpi,V56.17.17Z,12,2104KB\x03
        Or: \x02GX420d,V1.0,1234,D\x03

        The last field indicates print method capability:
        - D = Direct thermal only
        - T = Thermal transfer capable
        """
        if not response:
            return

        status.raw_status["hi_response"] = response

        # Remove STX/ETX and clean up
        clean = response.replace("\x02", "").replace("\x03", "").strip()
        if not clean:
            return

        parts = clean.split(",")
        if len(parts) >= 1:
            status.model = parts[0].strip()
            # Try to extract DPI from model string (e.g., "GX430t-300dpi")
            dpi_match = re.search(r"-(\d{3})dpi", status.model, re.IGNORECASE)
            if dpi_match:
                status.dpi = int(dpi_match.group(1))
        if len(parts) >= 2:
            status.firmware = parts[1].strip()
        # Note: thermal_transfer_capable is detected from ~HS line 2 field 4
        # where 1 = thermal transfer mode active (confirms capability)

    def _parse_host_status(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HS response for status flags and configuration.

        Response is 3 lines of comma-separated values (each wrapped in STX/ETX):
        Line 1: communication,paper_out,pause,label_length,labels_remaining,
                buffer_full,comm_diag_mode,partial_format,unused,corrupt_ram,
                under_temp,over_temp
        Line 2: function_settings,unused,head_up,ribbon_out,thermal_transfer,
                print_mode,print_width_dots,label_home,unused,unused,darkness
        Line 3: varies by model (not used)
        """
        if not response:
            return

        status.raw_status["hs_response"] = response

        # Split into lines, handling various line endings
        lines = re.split(r"[\r\n]+", response)
        lines = [line.strip() for line in lines if line.strip()]

        if len(lines) < 2:
            return

        # Parse line 1 (status flags)
        self._parse_hs_line1(lines[0], status)

        # Parse line 2 (configuration)
        self._parse_hs_line2(lines[1], status)

    def _parse_hs_line1(self, line: str, status: PrinterStatus) -> None:
        """Parse first line of ~HS response."""
        # Remove STX if present
        line = line.replace("\x02", "")
        parts = line.split(",")

        if len(parts) >= 2:
            status.paper_out = parts[1].strip() == "1"
        if len(parts) >= 3:
            status.paused = parts[2].strip() == "1"
        if len(parts) >= 4:
            # Label length in dots (convert to mm assuming 203dpi for now)
            try:
                dots = int(parts[3])
                # 203 dpi = 8 dots/mm, but we need printer DPI to be accurate
                # Default to 203 dpi (8 dots/mm)
                status.label_length_mm = round(dots / 8.0, 1)
            except (ValueError, TypeError):
                pass
        if len(parts) >= 6:
            status.buffer_full = parts[5].strip() == "1"

    def _parse_hs_line2(self, line: str, status: PrinterStatus) -> None:
        """Parse second line of ~HS response.

        Format: function_settings,unused,head_up,ribbon_out,thermal_transfer,
                print_mode,print_width_dots,label_home,unused,unused,darkness
        Example: 129,0,0,0,1,2,6,0,00000000,1,000
        """
        # Remove STX/ETX if present
        line = line.replace("\x02", "").replace("\x03", "")
        parts = line.split(",")

        if len(parts) >= 3:
            status.head_open = parts[2].strip() == "1"
        if len(parts) >= 4:
            status.ribbon_out = parts[3].strip() == "1"
        if len(parts) >= 5:
            # Thermal transfer mode: 0=direct thermal, 1=thermal transfer
            # Note: This indicates current mode, not capability (capability is stored in config)
            tt_mode = parts[4].strip()
            if tt_mode == "1":
                status.print_method = PRINT_METHOD_TRANSFER
            else:
                status.print_method = PRINT_METHOD_DIRECT
        if len(parts) >= 6:
            mode_code = parts[5].strip()
            status.print_mode = PRINT_MODES.get(mode_code, PRINT_MODE_UNKNOWN)
        if len(parts) >= 8:
            # Print speed (field 7)
            try:
                speed = int(parts[7])
                if speed > 0:  # Only set if non-zero
                    status.print_speed = speed
            except (ValueError, TypeError):
                pass
        if len(parts) >= 11:
            # Darkness is often in field 10 (varies by model)
            try:
                darkness = int(parts[10])
                if 0 <= darkness <= 30:  # Valid darkness range
                    status.darkness = darkness
            except (ValueError, TypeError):
                pass

    def _parse_odometer(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HQOD response for head distance.

        Response format (human readable):
          PRINT METERS
             TOTAL NONRESETTABLE:              69 "
             USER RESETTABLE CNTR1:            69 "
             USER RESETTABLE CNTR2:            69 "

        Units can be inches (") or centimeters (cm) depending on printer config.
        We convert to cm for consistency in HA.
        """
        if not response:
            return

        status.raw_status["od_response"] = response

        # Look for TOTAL NONRESETTABLE line and extract number + unit
        # Match number followed by " (inches) or cm
        match = re.search(r"TOTAL\s+NONRESETTABLE:\s*(\d+)\s*(cm|\")", response)
        if match:
            try:
                value = float(match.group(1))
                unit = match.group(2)
                # Convert inches to cm if needed (1 inch = 2.54 cm)
                if unit == '"':
                    value = round(value * 2.54, 1)
                status.head_distance_cm = value
            except (ValueError, TypeError):
                pass

    def _parse_extended_status(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HQES response for error and warning flags.

        Response format:
          PRINTER STATUS
             ERRORS:         0 00000000 00000000
             WARNINGS:       0 00000000 00000000

        First number (0 or 1) indicates if any error/warning is present.
        Second group is always 00000000 (nibbles 16-9).
        Third group is the bitmask (nibbles 8-1) as 8 hex digits.
        """
        if not response:
            return

        status.raw_status["es_response"] = response

        # Parse ERRORS line
        error_match = re.search(r"ERRORS:\s*(\d)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})", response)
        if error_match:
            has_error = error_match.group(1) == "1"
            # group1 is nibbles 8-1 (rightmost 8 hex digits)
            error_bitmask = int(error_match.group(3), 16)

            status.has_error = has_error

            if has_error and error_bitmask > 0:
                errors = []
                for bit_value, description in ERROR_FLAGS.items():
                    if error_bitmask & bit_value:
                        errors.append(description)
                status.error_flags = ", ".join(errors) if errors else "None"
            else:
                status.error_flags = "None"

        # Parse WARNINGS line
        warning_match = re.search(r"WARNINGS:\s*(\d)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})", response)
        if warning_match:
            has_warning = warning_match.group(1) == "1"
            # group1 is nibbles 8-1 (rightmost 8 hex digits)
            warning_bitmask = int(warning_match.group(3), 16)

            if has_warning and warning_bitmask > 0:
                warnings = []
                for bit_value, description in WARNING_FLAGS.items():
                    if warning_bitmask & bit_value:
                        warnings.append(description)
                status.warning_flags = ", ".join(warnings) if warnings else "None"
            else:
                status.warning_flags = "None"

    def get_calibrate_command(self) -> str:
        """Get ZPL calibration command."""
        return ZPL_CALIBRATE

    def get_feed_command(self, count: int = 1) -> str:
        """Get ZPL feed command."""
        return ZPL_FEED_LABEL * count
