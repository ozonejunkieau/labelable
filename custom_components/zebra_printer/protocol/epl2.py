"""EPL2 protocol implementation for Zebra printers."""

from __future__ import annotations

import re

from ..const import EPL2_CALIBRATE, EPL2_STATUS
from .base import PrinterProtocol, PrinterStatus

# Print method constants
PRINT_METHOD_DIRECT = "direct_thermal"
PRINT_METHOD_TRANSFER = "thermal_transfer"


class EPL2Protocol(PrinterProtocol):
    """EPL2 protocol implementation."""

    async def probe(self) -> bool:
        """Probe if EPL2 is supported by checking UQ response."""
        response = await self.send_command(EPL2_STATUS)
        # EPL2 UQ response should have substantial content
        return len(response) > 5 and not response.startswith("\x02")

    async def get_status(self) -> PrinterStatus:
        """Get printer status from UQ command.

        Multi-line response example:
        UKQ1935HLU      V4.42
        Serial port:96,N,8,1
        Page Mode
        Image buffer size:0245K
        Fmem used: 0 (bytes)
        Gmem used: 0
        Emem used: 29600
        Available: 100959
        I8,0,001 rY JF WN
        S3 D09 R256,000 ZT UN
        q320 Q120,24
        Option:d,Ff
        09 18 29
        Cover: T=127, C=148
        """
        status = PrinterStatus(online=True, protocol_type="EPL2")

        response = await self.send_command(EPL2_STATUS)
        if not response:
            status.online = False
            return status

        status.raw_status["uq_response"] = response
        self._parse_uq_response(response, status)

        return status

    def _parse_uq_response(self, response: str, status: PrinterStatus) -> None:
        """Parse UQ response for model, firmware, and status info."""
        lines = response.strip().split("\n")
        lines = [line.strip() for line in lines if line.strip()]

        if not lines:
            return

        # First line: model and firmware (e.g., "UKQ1935HLU      V4.42")
        first_line = lines[0]
        self._extract_model_firmware(first_line, status)

        # Parse remaining lines for status info
        for line in lines[1:]:
            line = line.strip()

            # I line: speed, sensors, ribbon status
            # Format: I8,0,001 rY JF WN
            if line.startswith("I") and "," in line:
                self._parse_i_line(line, status)

            # q line: label width in dots
            # Format: q320
            elif line.startswith("q") and line[1:].split()[0].isdigit():
                self._parse_q_line(line, status)

            # Q line: label length and gap
            # Format: Q120,24
            elif line.startswith("Q") and "," in line:
                self._parse_Q_line(line, status)

            # Option line: direct/transfer mode
            # Format: Option:d,Ff or Option:D,Ff
            elif line.startswith("Option:"):
                self._parse_option_line(line, status)

            # S line: darkness and other settings
            # Format: S3 D09 R256,000 ZT UN
            elif line.startswith("S") and " D" in line:
                self._parse_s_line(line, status)

    def _extract_model_firmware(self, text: str, status: PrinterStatus) -> None:
        """Extract model and firmware from first line.

        Handles formats like:
        - "UKQ1935HLU      V4.42" -> model="UKQ1935HLU", firmware="V4.42"
        - "UKQ1935HMU V4.70,8,200" -> model="UKQ1935HMU", firmware="V4.70"
        """
        # Handle comma-separated format (older style)
        if "," in text:
            text = text.split(",")[0]

        # Look for version pattern (V followed by digits and dots)
        version_match = re.search(r"\s+(V\d+(?:\.\d+)+)", text)
        if version_match:
            status.firmware = version_match.group(1)
            model_part = text[: version_match.start()].strip()
            if model_part:
                status.model = model_part
        else:
            status.model = text.strip()

    def _parse_i_line(self, line: str, status: PrinterStatus) -> None:
        """Parse I line for speed and ribbon status.

        Format: I8,0,001 rY JF WN
        - First number after I is print speed
        - rY = ribbon present, rN = ribbon out
        """
        # Extract speed (first number after I)
        speed_match = re.match(r"I(\d+)", line)
        if speed_match:
            try:
                status.print_speed = int(speed_match.group(1))
            except ValueError:
                pass

        # Check ribbon status
        if " rN " in line or line.endswith(" rN"):
            status.ribbon_out = True
        elif " rY " in line or line.endswith(" rY"):
            status.ribbon_out = False

    def _parse_q_line(self, line: str, status: PrinterStatus) -> None:
        """Parse q line for label width.

        Format: q320 (width in dots at 203 dpi = 8 dots/mm)
        """
        match = re.match(r"q(\d+)", line)
        if match:
            try:
                dots = int(match.group(1))
                status.print_width_mm = round(dots / 8.0, 1)
            except ValueError:
                pass

    def _parse_Q_line(self, line: str, status: PrinterStatus) -> None:
        """Parse Q line for label length.

        Format: Q120,24 (length in dots, gap in dots)
        """
        match = re.match(r"Q(\d+),(\d+)", line)
        if match:
            try:
                length_dots = int(match.group(1))
                status.label_length_mm = round(length_dots / 8.0, 1)
            except ValueError:
                pass

    def _parse_option_line(self, line: str, status: PrinterStatus) -> None:
        """Parse Option line for print method.

        Format: Option:d,Ff or Option:D,Ff
        - d = direct thermal
        - D = thermal transfer
        """
        # Extract the option character after "Option:"
        match = re.match(r"Option:([dD])", line)
        if match:
            option = match.group(1)
            if option == "d":
                status.print_method = PRINT_METHOD_DIRECT
                status.thermal_transfer_capable = False
            elif option == "D":
                status.print_method = PRINT_METHOD_TRANSFER
                status.thermal_transfer_capable = True

    def _parse_s_line(self, line: str, status: PrinterStatus) -> None:
        """Parse S line for darkness setting.

        Format: S3 D09 R256,000 ZT UN
        - S3 = speed setting (already have from I line)
        - D09 = darkness (0-15)
        """
        darkness_match = re.search(r"D(\d+)", line)
        if darkness_match:
            try:
                status.darkness = int(darkness_match.group(1))
            except ValueError:
                pass

    def _parse_status_flags(self, flags_str: str, status: PrinterStatus) -> None:
        """Parse EPL2 status flags from comma-separated format.

        Status byte interpretation (varies by model):
        Bit 0: Paper out
        Bit 1: Pause
        Bit 2: Head up (some models)
        Bit 3: Ribbon out (some models)
        """
        try:
            flags = int(flags_str)
            status.paper_out = bool(flags & 0x01)
            status.paused = bool(flags & 0x02)
            if flags & 0x04:
                status.head_open = True
            if flags & 0x08:
                status.ribbon_out = True
        except ValueError:
            pass

    def get_calibrate_command(self) -> str:
        """Get EPL2 calibration command."""
        return EPL2_CALIBRATE

    def get_feed_command(self, count: int = 1) -> str:
        """Get EPL2 feed command."""
        return f"P{count}"
