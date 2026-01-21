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

        # Get host status (~HS)
        hs_response = await self.send_command(ZPL_HOST_STATUS)
        self._parse_host_status(hs_response, status)

        # Get odometer (~HQOD)
        od_response = await self.send_command(ZPL_ODOMETER)
        self._parse_odometer(od_response, status)

        return status

    def _parse_host_identification(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HI response for model and firmware.

        Format: STX <model>,<firmware>,<dpi>,<memory> ETX
        Example: \x02ZTC ZD420-300dpi ZPL,V84.20.21Z,300,262144\x03
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

    def _parse_host_status(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HS response for status flags and configuration.

        Response is 3 lines of comma-separated values:
        Line 1: communication,paper_out,pause,label_length,labels_remaining,
                buffer_full,comm_diag_mode,partial_format,unused,corrupt_ram,
                under_temp,over_temp
        Line 2: function_settings,unused,head_up,ribbon_out,thermal_transfer,
                print_mode,head_element_count,speed,unused...
        Line 3: ZPL_mode,pw_protected,pw_level,...
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
        """Parse second line of ~HS response."""
        parts = line.split(",")

        if len(parts) >= 3:
            status.head_open = parts[2].strip() == "1"
        if len(parts) >= 4:
            status.ribbon_out = parts[3].strip() == "1"
        if len(parts) >= 6:
            mode_code = parts[5].strip()
            status.print_mode = PRINT_MODES.get(mode_code, PRINT_MODE_UNKNOWN)
        if len(parts) >= 7:
            # Print width in dots
            try:
                dots = int(parts[6])
                status.print_width_mm = round(dots / 8.0, 1)
            except (ValueError, TypeError):
                pass
        if len(parts) >= 8:
            # Print speed
            try:
                status.print_speed = int(parts[7])
            except (ValueError, TypeError):
                pass

    def _parse_odometer(self, response: str, status: PrinterStatus) -> None:
        """Parse ~HQOD response for odometer data.

        Response format varies by model but typically includes:
        - LABEL PRINTED counter
        - HEAD DISTANCE (total travel in inches)
        """
        if not response:
            return

        status.raw_status["od_response"] = response

        # Look for label count
        label_match = re.search(r"(?:LABEL|LABELS?)[\s:]+(\d+)", response, re.IGNORECASE)
        if label_match:
            try:
                status.labels_printed = int(label_match.group(1))
            except ValueError:
                pass

        # Look for head distance (in inches, convert to cm)
        # Format: "xxx,xxx,xxx" inches or "HEAD DISTANCE: xxx"
        head_match = re.search(
            r"(?:HEAD|TOTAL)[\s:]*(?:DISTANCE)?[\s:]*(\d+(?:,\d+)*)", response, re.IGNORECASE
        )
        if head_match:
            try:
                # Remove commas and convert
                inches_str = head_match.group(1).replace(",", "")
                inches = float(inches_str)
                status.head_distance_cm = round(inches * 2.54, 2)
            except ValueError:
                pass

    def get_calibrate_command(self) -> str:
        """Get ZPL calibration command."""
        return ZPL_CALIBRATE

    def get_feed_command(self, count: int = 1) -> str:
        """Get ZPL feed command."""
        return ZPL_FEED_LABEL * count
