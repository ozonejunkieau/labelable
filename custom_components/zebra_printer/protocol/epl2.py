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
        """Parse UQ response for model and firmware info."""
        lines = response.strip().split("\n")
        lines = [line.strip() for line in lines if line.strip()]

        if not lines:
            return

        # Try to parse as single-line format first
        # Format: "UKQ1935HMU V4.70,8,200,0001,000"
        if "," in lines[0]:
            parts = lines[0].split(",")
            if parts:
                # First part may contain model and firmware
                first = parts[0].strip()
                # Look for version pattern
                version_match = re.search(r"(V\d+\.\d+)", first)
                if version_match:
                    status.firmware = version_match.group(1)
                    # Model is everything before the version
                    model_part = first[: version_match.start()].strip()
                    if model_part:
                        status.model = model_part
                else:
                    status.model = first

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
            # Multi-line format
            # First non-empty line is usually model
            if lines:
                status.model = lines[0]

            # Look for firmware version in subsequent lines
            for line in lines[1:]:
                if line.startswith("V") and "." in line:
                    status.firmware = line
                    break

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
