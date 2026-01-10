"""Brother P-Touch printer implementation (stub)."""

from labelable.models.printer import PrinterConfig
from labelable.printers.base import BasePrinter


class PTouchPrinter(BasePrinter):
    """Brother P-Touch Cube printer implementation.

    This is a stub implementation. The P-Touch Cube requires:
    - Bluetooth connectivity
    - Bitmap image rendering
    - P-Touch specific protocol

    These features will be implemented in a future version.
    """

    def __init__(self, config: PrinterConfig) -> None:
        super().__init__(config)

    async def connect(self) -> None:
        """Establish Bluetooth connection to the P-Touch printer.

        Not yet implemented.
        """
        raise NotImplementedError(
            "P-Touch Bluetooth connectivity is not yet implemented. This feature will be added in a future version."
        )

    async def disconnect(self) -> None:
        """Close Bluetooth connection."""
        raise NotImplementedError("P-Touch support is not yet implemented.")

    async def is_online(self) -> bool:
        """Check if the P-Touch printer is online.

        Not yet implemented.
        """
        return False

    async def get_media_size(self) -> tuple[float, float] | None:
        """Query media size from P-Touch printer.

        The P-Touch Cube can report the installed tape cassette size.
        This will be implemented when Bluetooth support is added.
        """
        raise NotImplementedError("P-Touch support is not yet implemented.")

    async def print_raw(self, data: bytes) -> None:
        """Send bitmap data to the P-Touch printer.

        P-Touch printers require bitmap data in a specific format.
        This will be implemented when Bluetooth support is added.
        """
        raise NotImplementedError("P-Touch support is not yet implemented.")
