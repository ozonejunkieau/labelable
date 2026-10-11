"""Printer implementations for Labelable."""

from labelable.models.printer import (
    BridgeConnection,
    PrinterConfig,
    PrinterType,
    PTouchBridgeConnection,
    ZebraBridgeConnection,
)
from labelable.models.template import EngineType, TemplateConfig
from labelable.printers.base import BasePrinter
from labelable.printers.bridge import BridgePTouchPrinter
from labelable.printers.epl2 import EPL2Printer
from labelable.printers.ptouch import PTouchPrinter
from labelable.printers.ptouch_net import PTouchNetPrinter
from labelable.printers.zebra_net import ZebraNetPrinter
from labelable.printers.zpl import ZPLPrinter

__all__ = [
    "BasePrinter",
    "BridgePTouchPrinter",
    "EPL2Printer",
    "PTouchNetPrinter",
    "PTouchPrinter",
    "ZPLPrinter",
    "ZebraNetPrinter",
    "create_printer",
    "dpi_mismatch_warning",
]


def create_printer(config: PrinterConfig) -> BasePrinter:
    """Factory function to create a printer instance from config."""
    # Bridge connections always use BridgePTouchPrinter regardless of printer type
    if isinstance(config.connection, BridgeConnection):
        return BridgePTouchPrinter(config)

    # ESP32 network bridge - HTTP transport, raw raster payload
    if isinstance(config.connection, PTouchBridgeConnection):
        return PTouchNetPrinter(config)

    # ESP32 network bridge - HTTP transport, raw ZPL payload
    if isinstance(config.connection, ZebraBridgeConnection):
        return ZebraNetPrinter(config)

    printer_classes = {
        PrinterType.ZPL: ZPLPrinter,
        PrinterType.EPL2: EPL2Printer,
        PrinterType.PTOUCH: PTouchPrinter,
    }
    printer_class = printer_classes.get(config.type)
    if not printer_class:
        raise ValueError(f"Unknown printer type: {config.type}")
    return printer_class(config)


def dpi_mismatch_warning(printer: BasePrinter, template: TemplateConfig) -> str | None:
    """Warn when an image template is rendered for a different resolution.

    Image templates are laid out in mm but rasterised at ``template.dpi``, so
    a 203 dpi template on a 300 dpi printer prints at about two-thirds size.
    Only printers that report their resolution can be checked; jinja
    templates are raw printer commands in dots and are not checked.
    """
    printer_dpi = printer.dpi
    # A warning must never fail a print, so anything but a real int is unknown
    if template.engine != EngineType.IMAGE or not isinstance(printer_dpi, int) or printer_dpi <= 0:
        return None
    if abs(printer_dpi - template.dpi) <= 1:
        return None
    return (
        f"Template '{template.name}' is rendered at {template.dpi} dpi but printer "
        f"'{printer.name}' is {printer_dpi} dpi; the label will print at "
        f"{template.dpi / printer_dpi:.0%} of its intended size"
    )
