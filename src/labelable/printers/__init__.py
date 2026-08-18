"""Printer implementations for Labelable."""

from labelable.models.printer import BridgeConnection, PrinterConfig, PrinterType, PTouchBridgeConnection
from labelable.printers.base import BasePrinter
from labelable.printers.bridge import BridgePTouchPrinter
from labelable.printers.epl2 import EPL2Printer
from labelable.printers.ptouch import PTouchPrinter
from labelable.printers.ptouch_net import PTouchNetPrinter
from labelable.printers.zpl import ZPLPrinter

__all__ = [
    "BasePrinter",
    "BridgePTouchPrinter",
    "EPL2Printer",
    "PTouchNetPrinter",
    "PTouchPrinter",
    "ZPLPrinter",
    "create_printer",
]


def create_printer(config: PrinterConfig) -> BasePrinter:
    """Factory function to create a printer instance from config."""
    # Bridge connections always use BridgePTouchPrinter regardless of printer type
    if isinstance(config.connection, BridgeConnection):
        return BridgePTouchPrinter(config)

    # ESP32 network bridge - HTTP transport, raw raster payload
    if isinstance(config.connection, PTouchBridgeConnection):
        return PTouchNetPrinter(config)

    printer_classes = {
        PrinterType.ZPL: ZPLPrinter,
        PrinterType.EPL2: EPL2Printer,
        PrinterType.PTOUCH: PTouchPrinter,
    }
    printer_class = printer_classes.get(config.type)
    if not printer_class:
        raise ValueError(f"Unknown printer type: {config.type}")
    return printer_class(config)
