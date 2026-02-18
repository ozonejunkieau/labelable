"""Printer configuration models."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class PrinterType(StrEnum):
    """Supported printer types."""

    ZPL = "zpl"
    EPL2 = "epl2"
    PTOUCH = "ptouch"


class ConnectionType(StrEnum):
    """Supported connection types."""

    TCP = "tcp"
    SERIAL = "serial"
    USB = "usb"
    HA = "ha"


class TCPConnection(BaseModel):
    """TCP/IP connection configuration."""

    type: Literal["tcp"] = "tcp"
    host: str
    port: int = 9100


class SerialConnection(BaseModel):
    """Serial port connection configuration."""

    type: Literal["serial"] = "serial"
    device: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1


class USBConnection(BaseModel):
    """USB connection configuration (for P-Touch printers)."""

    type: Literal["usb"] = "usb"
    vendor_id: int = 0x04F9  # Brother
    product_id: int = 0x20AF  # PT-P710BT


class HAConnection(BaseModel):
    """Home Assistant zebra_printer integration connection.

    Uses the HA zebra_printer integration as a transport layer.
    """

    type: Literal["ha"] = "ha"
    device_id: str  # HA config entry ID or device ID
    ha_url: str = "http://supervisor/core"
    ha_token: str | None = None  # Optional if running as addon (uses SUPERVISOR_TOKEN)


ConnectionConfig = Annotated[
    TCPConnection | SerialConnection | USBConnection | HAConnection,
    Field(discriminator="type"),
]


class HealthcheckConfig(BaseModel):
    """Healthcheck configuration for a printer."""

    interval: int = 60  # Seconds between status checks
    command: str | None = None  # Custom command (default depends on printer type)


class PrinterConfig(BaseModel):
    """Configuration for a single printer."""

    name: str
    type: PrinterType
    connection: ConnectionConfig
    enabled: bool = True
    healthcheck: HealthcheckConfig = HealthcheckConfig()
