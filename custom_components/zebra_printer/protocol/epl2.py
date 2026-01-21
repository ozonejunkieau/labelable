"""EPL2 protocol implementation for Zebra printers."""

from __future__ import annotations

import re

from ..const import EPL2_CALIBRATE, EPL2_STATUS
from .base import PrinterProtocol, PrinterStatus


class EPL2Protocol(PrinterProtocol):
    """EPL2 protocol implementation."""

    async def probe(self) -> bool:
        """Probe if EPL2 is supported by checking UQ response."""
        response = await self.send_command(EPL2_STATUS)
        # EPL2 UQ response should have substantial content
        return len(response) > 5 and not response.startswith("\x02")

    async def get_status(self) -> PrinterStatus:
        """Get printer status from UQ command.

        UQ response format varies by model but typically includes:
        Model name, firmware version, and basic status info.

        Example response:
        "UKQ1935HMU V4.70,8,200,0001,000"
        Where: Model V<firmware>,<speed>,<dpi>,<label_count>,<status>

        Or for older models:
        "LP2844"
        "V4.45"
        etc.
        """
        status = PrinterStatus(online=True)

        response = await self.send_command(EPL2_STATUS)
        if not response:
            status.online = False
            return status

        status.raw_status["uq_response"] = response
        self._parse_uq_response(response, status)

        return status

    def _parse_uq_response(self, response: str, status: PrinterStatus) -> None:
        """Parse UQ response for model and firmware info.

        Response formats vary:
        - Single line with commas: "UKQ1935HMU V4.70,8,200,0001,000"
        - Single line without commas: "UKQ1935HLU V4.42"
        - Multi-line: "LP2844\\nV4.45\\n..."
        """
        lines = response.strip().split("\n")
        lines = [line.strip() for line in lines if line.strip()]

        if not lines:
            return

        first_line = lines[0]

        # Try to parse as comma-separated format first
        if "," in first_line:
            parts = first_line.split(",")
            if parts:
                first = parts[0].strip()
                self._extract_model_firmware(first, status)

                # Try to extract additional info from comma-separated values
                if len(parts) >= 2:
                    try:
                        status.print_speed = int(parts[1])
                    except ValueError:
                        pass

                # Check for status flags in last field (if present)
                if len(parts) >= 5:
                    self._parse_status_flags(parts[-1], status)
        else:
            # Single line without commas or multi-line format
            # Try to extract model and firmware from first line
            self._extract_model_firmware(first_line, status)

            # If firmware not found, look in subsequent lines
            if status.firmware is None:
                for line in lines[1:]:
                    if line.startswith("V") and "." in line:
                        status.firmware = line
                        break

    def _extract_model_firmware(self, text: str, status: PrinterStatus) -> None:
        """Extract model and firmware from a text string.

        Handles formats like:
        - "UKQ1935HMU V4.70" -> model="UKQ1935HMU", firmware="V4.70"
        - "UKQ1935HLU V4.42" -> model="UKQ1935HLU", firmware="V4.42"
        - "LP2844" -> model="LP2844", firmware=None
        """
        # Look for version pattern (V followed by digits and dots)
        version_match = re.search(r"\s+(V\d+(?:\.\d+)+)", text)
        if version_match:
            status.firmware = version_match.group(1)
            # Model is everything before the version
            model_part = text[: version_match.start()].strip()
            if model_part:
                status.model = model_part
        else:
            # No version found, whole text is model
            status.model = text

    def _parse_status_flags(self, flags_str: str, status: PrinterStatus) -> None:
        """Parse EPL2 status flags.

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
            # Head open and ribbon out are less reliable on EPL2
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
