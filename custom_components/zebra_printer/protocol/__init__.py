"""Protocol implementations for Zebra printers."""

from __future__ import annotations

from .base import PrinterProtocol, PrinterStatus
from .epl2 import EPL2Protocol
from .zpl import ZPLProtocol

__all__ = [
    "PrinterProtocol",
    "PrinterStatus",
    "ZPLProtocol",
    "EPL2Protocol",
    "get_protocol",
]


def get_protocol(protocol_type: str) -> type[PrinterProtocol]:
    """Get protocol class by type."""
    protocols = {
        "zpl": ZPLProtocol,
        "epl2": EPL2Protocol,
    }
    return protocols.get(protocol_type, ZPLProtocol)
