"""ZPL protocol implementation for Zebra printers."""

from __future__ import annotations

import re

from ..const import (
    PRINT_MODE_APPLICATOR,
    PRINT_MODE_CUTTER,
    PRINT_MODE_DELAYED_CUT,
    PRINT_MODE_PEEL,
    PRINT_MODE_REWIND,
    PRINT_MODE_RFID,
    PRINT_MODE_TEAR,
    PRINT_MODE_UNKNOWN,
    ZPL_CALIBRATE,
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
        status = PrinterStatus(online=True)

        # Get host identification (~HI)
        hi_response = await self.send_command(ZPL_HOST_IDENTIFICATION)
        self._parse_host_identification(hi_response, status)

        # Get host status (~HS) - contains most status info
        hs_response = await self.send_command(ZPL_HOST_STATUS)
        self._parse_host_status(hs_response, status)

        # Get odometer (~HQOD) - head distance in inches
        od_response = await self.send_command(ZPL_ODOMETER)
        self._parse_odometer(od_response, status)

        return status

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
        if len(parts) >= 2:
            status.firmware = parts[1].strip()

        # Check last field for print method capability (D or T)
        if len(parts) >= 4:
            last_field = parts[-1].strip().upper()
            # Could be just "D" or "T", or might be embedded in memory string
            if last_field == "T" or last_field.endswith("T"):
                status.thermal_transfer_capable = True
            elif last_field == "D" or last_field.endswith("D"):
                status.thermal_transfer_capable = False
            else:
                # If we can't determine from ~HI, check if print_method is thermal_transfer
                # (will be set later from ~HS parsing)
                pass

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
            tt_mode = parts[4].strip()
            status.print_method = PRINT_METHOD_TRANSFER if tt_mode == "1" else PRINT_METHOD_DIRECT
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

    def get_calibrate_command(self) -> str:
        """Get ZPL calibration command."""
        return ZPL_CALIBRATE

    def get_feed_command(self, count: int = 1) -> str:
        """Get ZPL feed command."""
        return ZPL_FEED_LABEL * count
